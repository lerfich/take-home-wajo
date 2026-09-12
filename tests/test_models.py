from dataclasses import asdict
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from mail_agent.core import Email, Proposal
from mail_agent.groq_provider import GroqProposer, ProviderError
from mail_agent.model_settings import (
    BUNDLED_GROQ,
    USER_GROQ,
    USER_OPENAI,
    CredentialStore,
    ModelSettings,
    create_provider,
    load_model_settings,
    save_model_settings,
    validate_user_key,
)
from mail_agent.openai_provider import DEFAULT_OPENAI_MODEL, OpenAIProposer
from mail_agent.web import Application


class ModelSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = sqlite3.connect(":memory:")
        self.credentials = CredentialStore(Path(self.temp.name) / "data" / "model-credentials.json")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_defaults_and_mode_concurrency_are_persisted(self):
        self.assertEqual(load_model_settings(self.db), ModelSettings(BUNDLED_GROQ, 3))
        self.assertEqual(save_model_settings(self.db, USER_GROQ, 12), ModelSettings(USER_GROQ, 12))
        self.assertEqual(load_model_settings(self.db), ModelSettings(USER_GROQ, 12))
        row = self.db.execute("SELECT mode, concurrency FROM model_settings").fetchone()
        self.assertEqual(row, (USER_GROQ, 12))

    def test_concurrency_policy_is_enforced(self):
        for mode, concurrency in ((BUNDLED_GROQ, 4), (USER_GROQ, 2), (USER_OPENAI, 13)):
            with self.subTest(mode=mode, concurrency=concurrency):
                with self.assertRaises(ValueError):
                    save_model_settings(self.db, mode, concurrency)
        with self.assertRaises(ValueError):
            save_model_settings(self.db, "other", 3)
        with self.assertRaises(ValueError):
            save_model_settings(self.db, USER_GROQ, True)

    def test_keys_are_separate_owner_only_and_not_returned_with_settings(self):
        key = "gsk_user_secret_123"
        self.credentials.set_key(USER_GROQ, key)
        self.assertEqual(self.credentials.get_key(USER_GROQ), key)
        self.assertEqual(self.credentials.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(key, json.dumps(load_model_settings(self.db).public_dict()))
        tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        for (table,) in tables:
            columns = self.db.execute(f"PRAGMA table_info({table})").fetchall()
            self.assertNotIn("api_key", {column[1] for column in columns})

    def test_provider_factory_honors_selected_mode(self):
        self.credentials.set_key(USER_GROQ, "gsk_user_key")
        self.credentials.set_key(USER_OPENAI, "sk-user-key")
        self.assertIsInstance(
            create_provider(ModelSettings(USER_GROQ, 3), self.credentials, Path("unused")), GroqProposer
        )
        self.assertFalse(
            create_provider(ModelSettings(USER_GROQ, 3), self.credentials, Path("unused")).serialize_calls
        )
        openai = create_provider(ModelSettings(USER_OPENAI, 5), self.credentials, Path("unused"))
        self.assertIsInstance(openai, OpenAIProposer)
        self.assertEqual(openai.model, DEFAULT_OPENAI_MODEL)
        with patch("mail_agent.model_settings.GroqProposer.from_env", return_value="bundled") as factory:
            self.assertEqual(
                create_provider(ModelSettings(), self.credentials, Path("task/.env")), "bundled"
            )
            factory.assert_called_once_with(Path("task/.env"))

    def test_missing_user_key_fails_without_changing_mode(self):
        settings = save_model_settings(self.db, USER_OPENAI, 3)
        with self.assertRaisesRegex(ProviderError, "Enter and verify"):
            create_provider(settings, self.credentials, Path("unused"))
        self.assertEqual(load_model_settings(self.db), settings)

    def test_application_restart_restores_user_mode_key_and_concurrency(self):
        path = Path(self.temp.name) / "restart.sqlite3"
        token = Path(self.temp.name) / "missing-token.json"
        client = Path(self.temp.name) / "missing-client.json"
        first = Application(path, connection_token=token, gmail_credentials=client)
        try:
            first.credentials.set_key(USER_OPENAI, "sk-local-restart-test")
            with first.connect() as db:
                save_model_settings(db, USER_OPENAI, 9)
        finally:
            first.notifications.close()
        second = Application(path, connection_token=token, gmail_credentials=client)
        try:
            self.assertEqual(second.model_settings(), ModelSettings(USER_OPENAI, 9))
            self.assertEqual(second.analysis_limit, 9)
            self.assertTrue(second.public_models()["has_user_openai_key"])
            self.assertEqual(second.credentials.get_key(USER_OPENAI), "sk-local-restart-test")
        finally:
            second.notifications.close()


class ProviderContractTests(unittest.TestCase):
    def setUp(self):
        self.email = Email("server-id", "sender@example.test", "Update", "Project update")
        self.proposal = Proposal("draft", "Reply", text="Thank you.", recipient=self.email.sender)

    @staticmethod
    def response(data):
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data)}}]}

    def test_openai_propose_matches_existing_contract_and_uses_structured_output(self):
        provider = OpenAIProposer("sk-secret")
        with patch.object(provider, "request", return_value=self.response(asdict(self.proposal))) as request:
            self.assertEqual(provider.propose(self.email), self.proposal)
        payload = request.call_args.args[1]
        self.assertEqual(payload["model"], DEFAULT_OPENAI_MODEL)
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertNotIn("server-id", json.dumps(payload))
        self.assertNotIn("sk-secret", json.dumps(payload))

    def test_openai_rewrite_matches_existing_contract(self):
        provider = OpenAIProposer("sk-secret")
        with patch.object(provider, "request", return_value=self.response({"text": "Thanks."})) as request:
            result = provider.rewrite_draft(self.email, self.proposal, {
                "length": "concise",
                "greeting": "omit",
                "signoff": "omit",
                "reply_account": "owner@example.test",
                "confirmed_signature_name": "Nikita",
            })
        self.assertEqual(result, "Thanks.")
        self.assertEqual(request.call_args.args[1]["response_format"]["type"], "json_schema")

    def test_openai_http_error_and_validation_result_hide_key(self):
        key = "sk-secret-value"
        error = HTTPError(
            "https://api.openai.com/v1/models",
            401,
            key,
            {},
            io.BytesIO(json.dumps({"error": {"message": key + " invalid"}}).encode()),
        )
        with patch("mail_agent.openai_provider.build_opener") as opener:
            opener.return_value.open.side_effect = error
            result = validate_user_key(USER_OPENAI, key)
        self.assertFalse(result.ok)
        self.assertIn("401", result.error)
        self.assertNotIn(key, result.error)

    def test_openai_wire_request_uses_responses_structured_output_without_storage(self):
        provider = OpenAIProposer("sk-secret")
        raw_response = {
            "status": "completed",
            "model": DEFAULT_OPENAI_MODEL,
            "output": [{"type": "message", "content": [{
                "type": "output_text", "text": json.dumps(asdict(self.proposal))
            }]}],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }
        with patch("mail_agent.openai_provider.build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.headers = {}
            response.read.return_value = json.dumps(raw_response).encode()
            self.assertEqual(provider.propose(self.email), self.proposal)
        request = opener.return_value.open.call_args.args[0]
        wire = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertIs(wire["store"], False)
        self.assertEqual(wire["reasoning"], {"effort": "none"})
        self.assertTrue(wire["text"]["format"]["strict"])
        self.assertEqual(wire["text"]["format"]["type"], "json_schema")
        self.assertNotIn("response_format", wire)

    def test_validation_success_and_rejects_bundled_mode(self):
        with patch.object(GroqProposer, "models", return_value=[GroqProposer("gsk-valid").model]):
            self.assertTrue(validate_user_key(USER_GROQ, "gsk-valid").ok)
        result = validate_user_key(BUNDLED_GROQ, "ignored")
        self.assertFalse(result.ok)
        self.assertNotIn("ignored", result.error)


if __name__ == "__main__":
    unittest.main()
