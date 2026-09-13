"""Continuation mechanics use local fakes and never call Groq."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mail_agent import g1_continue
from mail_agent.core import Proposal


class FakeProvider:
    calls_made = 0
    models = []

    def __init__(self, *args, **kwargs):
        self.calls = []
        self.http_attempts = []
        type(self).models.append(kwargs.get("model"))

    def propose(self, email):
        type(self).calls_made += 1
        return Proposal("none", "Local continuation fixture", label="AI: Test",
                        independent_label=True, archive_recommendation="keep",
                        archive_reason="Keep", archive_evidence=email.body)


class G1ContinuationTests(unittest.TestCase):
    def test_only_second_attempt_is_allowed(self):
        self.assertEqual(g1_continue.attempt_dir(2).name, "attempt-02")
        with self.assertRaisesRegex(ValueError, "second attempt"):
            g1_continue.attempt_dir(4)

    def test_failed_id_gets_separate_second_attempt(self):
        cases = [
            {"id": "one", "phase": "decision", "scenario_id": "test", "order": 1,
             "email": {"sender": "one@synthetic.example", "subject": "One", "body": "First body."},
             "expected": {"level": "silent", "action": "none", "archive": "keep",
                          "label_kind": "unknown", "attention_cue": "none",
                          "event_kind": "none", "suspicious": False}},
            {"id": "two", "phase": "decision", "scenario_id": "test", "order": 2,
             "email": {"sender": "two@synthetic.example", "subject": "Two", "body": "Second body."},
             "expected": {"level": "silent", "action": "none", "archive": "keep",
                          "label_kind": "unknown", "attention_cue": "none",
                          "event_kind": "none", "suspicious": False}},
        ]
        originals = {
            "one": {"id": "one", "status": "ok", "phase": "decision"},
            "two": {"id": "two", "status": "error", "phase": "decision"},
        }
        info = {"dataset_sha256": "fixture", "files_sha256": {},
                "model": "qwen/qwen3.8-27b", "prompt_version": "email-analysis-prompt-v9"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempt_root = root / "attempts" / "attempt-02"
            attempt_root.mkdir(parents=True)
            (attempt_root / "two.json").write_text(json.dumps(
                {"id": "two", "attempt_number": 2, "status": "error", "phase": "decision"}))
            FakeProvider.calls_made = 0
            FakeProvider.models = []
            with patch.object(g1_continue.g1, "OUT", root), \
                 patch.object(g1_continue.g1, "preflight", return_value=(cases, info, originals)), \
                 patch.object(g1_continue, "verify_frozen_files"), \
                 patch.object(g1_continue, "continuation_api_key", return_value="fixture-key"), \
                 patch.object(g1_continue, "GroqProposer", FakeProvider), \
                 patch.object(g1_continue, "report", return_value={"unresolved_errors": 0}):
                g1_continue.run(2)
            result = json.loads((root / "attempts" / "attempt-02" / "two.json").read_text())
            attempt = json.loads((root / "attempts" / "attempt-02" / "attempt.json").read_text())
            checkpoint = json.loads((root / "attempts" / "attempt-02" / "checkpoint.json").read_text())
            self.assertEqual(FakeProvider.calls_made, 1)
            self.assertEqual(FakeProvider.models, ["qwen/qwen3.8-27b"])
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["attempt_number"], 2)
            self.assertEqual(result["model"], "qwen/qwen3.8-27b")
            self.assertEqual(attempt["model"], "qwen/qwen3.8-27b")
            self.assertEqual(attempt["credential_source"], "task/.env (value not stored)")
            self.assertEqual(checkpoint["last_id"], "two")
            self.assertEqual(originals["two"]["status"], "error")
            self.assertFalse((root / ".continue.lock").exists())

    def test_attempt_cannot_switch_models(self):
        info = {"model": "qwen/qwen3.8-27b", "prompt_version": "email-analysis-prompt-v9",
                "dataset_sha256": "dataset-fixture",
                "files_sha256": {
                    "mail_agent/prompts/email-analysis-prompt-v9.txt": "prompt-fixture",
                    "mail_agent/core.py": "core-fixture",
                    "mail_agent/g1.py": "harness-fixture",
                }}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            g1_continue.attempt_manifest(target, 2, info)
            path = target / "attempt.json"
            changed = json.loads(path.read_text())
            changed["model"] = "different/model"
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "another model"):
                g1_continue.attempt_manifest(target, 2, info)

    def test_report_excludes_failed_only_configuration_from_scores(self):
        expected = {"level": "silent", "action": "none", "archive": "keep",
                    "label_kind": "unknown", "attention_cue": "none",
                    "event_kind": "none", "suspicious": False}
        response = {"action": "none", "archive_recommendation": "keep",
                    "label_kind": "unknown", "attention_cue": "none",
                    "event_kind": "none", "suspicious": False}
        assessment = {"level_correct": True, "level_actual": "silent",
                      "unsafe_autonomous": False}
        cases = [{"id": "one", "phase": "decision"},
                 {"id": "two", "phase": "decision"},
                 {"id": "three", "phase": "decision"}]
        originals = {
            "one": {"id": "one", "status": "ok", "phase": "decision",
                    "expected": expected, "assessment": assessment,
                    "structured_response": response,
                    "provider_calls": [{"model": "qwen/qwen3.8-27b",
                                        "usage": {"total_tokens": 10}}]},
            "two": {"id": "two", "status": "error", "phase": "decision",
                    "expected": expected,
                    "provider_calls": [{"model": "qwen/qwen3.8-27b"}]},
            "three": {"id": "three", "status": "error", "phase": "decision",
                      "expected": expected,
                      "provider_calls": [{"model": "qwen/qwen3.8-27b"}]},
        }
        info = {"model": "qwen/qwen3.8-27b", "prompt_version": "email-analysis-prompt-v9",
                "dataset_sha256": "dataset-fixture",
                "files_sha256": {
                    "mail_agent/prompts/email-analysis-prompt-v9.txt": "prompt-fixture",
                    "mail_agent/core.py": "core-fixture",
                    "mail_agent/g1.py": "harness-fixture",
                }}
        failed = {"id": "three", "attempt_number": 2, "status": "error",
                  "phase": "decision", "expected": expected,
                  "model": "discarded/configuration", "provider_calls": []}
        continuation = {"id": "two", "attempt_number": 2, "status": "ok",
                        "phase": "decision", "expected": expected,
                        "assessment": assessment, "structured_response": response,
                        "model": "qwen/qwen3.8-27b",
                        "provider_calls": [{"model": "qwen/qwen3.8-27b",
                                            "usage": {"total_tokens": 20}}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempt = root / "attempts" / "attempt-02"
            attempt.mkdir(parents=True)
            (attempt / "two.json").write_text(json.dumps(continuation))
            (attempt / "three.json").write_text(json.dumps(failed))
            with patch.object(g1_continue.g1, "OUT", root), \
                 patch.object(g1_continue.g1, "preflight", return_value=(cases, info, originals)), \
                 patch.object(g1_continue, "verify_frozen_files"):
                counts = g1_continue.report()
            report = (root / "REPORT.md").read_text()
            self.assertEqual(counts["usable"], 2)
            self.assertEqual(counts["unresolved_errors"], 1)
            self.assertIn("### `qwen/qwen3.8-27b`", report)
            self.assertNotIn("### `discarded/configuration`", report)
            self.assertIn("same primary", report)

    def test_continuation_requires_non_bundled_local_key(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(g1_continue.g1, "ROOT", Path(directory)):
            with self.assertRaisesRegex(ValueError, "task/.env"):
                g1_continue.continuation_api_key()
            (Path(directory) / ".env").write_text(
                "GROQ_API_KEY=" + g1_continue.DEFAULT_BUNDLED_GROQ_API_KEY)
            with self.assertRaisesRegex(ValueError, "Replace"):
                g1_continue.continuation_api_key()
            (Path(directory) / ".env").write_text("GROQ_API_KEY=new-account-fixture")
            with patch.dict("os.environ", {"GROQ_API_KEY": "ignored-shell-key"}):
                self.assertEqual(g1_continue.continuation_api_key(), "new-account-fixture")


if __name__ == "__main__":
    unittest.main()
