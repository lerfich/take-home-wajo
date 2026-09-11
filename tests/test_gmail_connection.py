import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch, ANY

from mail_agent.gmail import MANAGE_SCOPE, READONLY_SCOPE
from mail_agent.web import Application


class GmailConnectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.token = root / "token.json"
        self.client = root / "client.json"
        self.client.write_text("{}")
        self.token.write_text(json.dumps({"scopes": [MANAGE_SCOPE], "token": "DO-NOT-EXPOSE"}))
        self.app = Application(root / "state.sqlite3", gmail_token=self.token,
                               gmail_credentials=self.client)
        self.connection = self.app.gmail_connection
        self.api = MagicMock()
        self.users = self.api.users.return_value
        self.users.getProfile.return_value.execute.return_value = {"emailAddress": "owner@example.test", "historyId": "10"}
        self.users.labels.return_value.list.return_value.execute.return_value = {
            "labels": [{"id": "test-label", "name": "Wajo-Test"}]}

    def tearDown(self):
        if self.connection.thread:
            self.connection.thread.join(3)
        self.directory.cleanup()

    def run_operation(self, operation, data=None):
        self.app.mutate("/api/gmail/" + operation, data or {})
        self.connection.thread.join(3)
        self.assertFalse(self.connection.thread.is_alive())
        return self.connection.snapshot()

    def consent(self, **changes):
        return {"allow_groq": True, "account": "owner@example.test", "live": True, "limit": 10, **changes}

    def modern_consent(self, **changes):
        return {"allow_groq": True, "account": "owner@example.test", "live": True,
                "history_mode": "new", "label_ids": [], **changes}

    def check(self):
        with patch("mail_agent.gmail.service", return_value=self.api):
            return self.run_operation("status")

    def test_status_is_explicit_read_only_and_public_state_is_sanitized(self):
        with patch("mail_agent.gmail.service", return_value=self.api) as service:
            self.assertEqual(self.app.state()["gmail_connection"]["status"], "unchecked")
            service.assert_not_called()
            state = self.run_operation("status")
        self.assertEqual(state["account"], "owner@example.test")
        self.assertEqual(state["access"], "manage")
        self.assertTrue(state["label_ready"])
        self.users.messages.assert_not_called()
        self.assertNotIn("DO-NOT-EXPOSE", json.dumps(self.app.state()))

    def test_sync_requires_exact_consent_account_mode_and_bounded_limit(self):
        self.check()
        with patch("mail_agent.gmail.service") as service:
            for changes in [{"allow_groq": False}, {"allow_groq": 1}, {"account": "other@example.test"},
                            {"live": False}, {"live": 1}, {"limit": True}, {"limit": 51}, {"limit": 0},
                            {"label": "INBOX"}]:
                with self.assertRaises(ValueError):
                    self.run_operation("sync", self.consent(**changes))
            service.assert_not_called()

    def test_sync_reads_only_selected_label_and_deduplicates(self):
        import base64
        messages = self.users.messages.return_value
        messages.list.return_value.execute.return_value = {"messages": [{"id": "one"}], "nextPageToken": "private-cursor"}
        messages.get.return_value.execute.return_value = {"labelIds": ["test-label", "INBOX"], "payload": {
            "mimeType": "text/plain", "headers": [{"name": "From", "value": "sender@example.test"}],
            "body": {"data": base64.urlsafe_b64encode(b"Synthetic reference").decode()}}}
        self.check()
        with patch("mail_agent.gmail.service", return_value=self.api):
            first = self.run_operation("sync", self.consent())
            second = self.run_operation("sync", self.consent())
        self.assertEqual(first["last_sync"]["queued"], 1)
        self.assertEqual(second["last_sync"]["already_imported"], 1)
        self.assertTrue(first["last_sync"]["more_available"])
        self.assertNotIn("private-cursor", json.dumps(first))
        messages.list.assert_called_with(userId="me", labelIds=["test-label"], maxResults=10)
        messages.modify.assert_not_called()
        messages.send.assert_not_called()
        with self.app.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM gmail_bindings").fetchone()[0], 1)

    def test_modern_sync_loads_labels_and_new_only_needs_no_test_label(self):
        self.users.labels.return_value.list.return_value.execute.return_value = {"labels": [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "work", "name": "Work", "type": "user"}]}
        checked = self.check()
        self.assertFalse(checked["label_ready"])
        self.assertEqual({x["name"] for x in checked["labels"]}, {"INBOX", "Work"})
        with patch("mail_agent.gmail.service", return_value=self.api):
            state = self.run_operation("sync", self.modern_consent(label_ids=["work"]))
        self.assertEqual(state["status"], "connected")
        sync = self.app.state()["gmail_sync"]["settings"]
        self.assertEqual(sync["history_mode"], "new")
        self.assertEqual(sync["history_labels"], ["work"])
        self.assertEqual(sync["history_status"], "done")
        self.users.messages.return_value.list.assert_not_called()

    def test_modern_sync_rejects_invalid_scope_before_background_work(self):
        self.check()
        with patch("mail_agent.gmail.service") as service:
            for changes in [{"history_mode": "week"}, {"label_ids": "work"},
                            {"label_ids": [1]}, {"extra": True}]:
                with self.assertRaises(ValueError):
                    self.app.mutate("/api/gmail/sync", self.modern_consent(**changes))
            service.assert_not_called()

    def test_automatic_poll_rechecks_account_and_backs_off_without_reading_history(self):
        self.users.labels.return_value.list.return_value.execute.return_value = {"labels": []}
        self.check()
        with patch("mail_agent.gmail.service", return_value=self.api):
            self.run_operation("sync", self.modern_consent())
            self.users.getProfile.return_value.execute.return_value = {
                "emailAddress": "other@example.test", "historyId": "11"}
            self.assertTrue(self.connection.poll_if_due())
            self.connection.thread.join(3)
        self.assertIn("try again", self.connection.snapshot()["error"])
        self.users.history.return_value.list.assert_not_called()
        with self.app.connect() as db:
            row = db.execute("SELECT history_id,next_poll_at FROM gmail_sync_settings").fetchone()
        self.assertEqual(row["history_id"], "10")
        self.assertTrue(row["next_poll_at"])

    def test_changed_account_blocks_message_reads(self):
        self.check()
        self.users.getProfile.return_value.execute.return_value = {"emailAddress": "other@example.test"}
        with patch("mail_agent.gmail.service", return_value=self.api):
            state = self.run_operation("sync", self.consent())
        self.assertEqual(state["status"], "unverified")
        self.assertIsNone(state["account"])
        self.users.messages.assert_not_called()
        self.assertEqual(self.app.state()["jobs"], [])

    def test_partial_sync_failure_preserves_queued_email_without_claiming_success(self):
        import base64
        messages = self.users.messages.return_value
        messages.list.return_value.execute.return_value = {"messages": [{"id": "one"}, {"id": "two"}]}
        messages.get.return_value.execute.side_effect = [{"labelIds": ["test-label", "INBOX"], "payload": {
            "mimeType": "text/plain", "headers": [{"name": "From", "value": "sender@example.test"}],
            "body": {"data": base64.urlsafe_b64encode(b"Synthetic reference").decode()}}}, RuntimeError("PRIVATE")]
        self.check()
        with patch("mail_agent.gmail.service", return_value=self.api):
            state = self.run_operation("sync", self.consent())
        self.assertEqual(state["status"], "unverified")
        self.assertIn("Some messages may already be queued", state["error"])
        self.assertNotIn("PRIVATE", state["error"])
        self.assertIsNone(state["last_sync"])
        self.assertEqual(len(self.app.state()["jobs"]), 1)

    def test_account_changed_between_preflight_and_import_is_also_blocked(self):
        self.check()
        self.users.getProfile.return_value.execute.side_effect = [
            {"emailAddress": "owner@example.test"}, {"emailAddress": "other@example.test"}]
        with patch("mail_agent.gmail.service", return_value=self.api):
            state = self.run_operation("sync", self.consent())
        self.assertEqual(state["status"], "unverified")
        self.users.messages.assert_not_called()

    def test_missing_label_and_readonly_access_block_live_sync(self):
        self.users.labels.return_value.list.return_value.execute.return_value = {"labels": []}
        self.check()
        with self.assertRaisesRegex(ValueError, "label"):
            self.run_operation("sync", self.consent())
        self.users.labels.return_value.list.return_value.execute.return_value = {"labels": [{"name": "Wajo-Test"}]}
        self.token.write_text(json.dumps({"scopes": [READONLY_SCOPE]}))
        self.check()
        with self.assertRaisesRegex(ValueError, "Reconnect"):
            self.run_operation("sync", self.consent())

    def test_background_connect_single_flight_and_executor_serialization(self):
        entered, release = threading.Event(), threading.Event()
        def authorize(*args, **kwargs):
            entered.set()
            release.wait(3)
        with patch("mail_agent.gmail.authorize", side_effect=authorize) as auth, \
             patch("mail_agent.gmail.service", return_value=self.api):
            try:
                self.app.mutate("/api/gmail/connect", {})
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.app.state()["gmail_connection"]["operation"], "connect")
                self.assertFalse(self.app.lock.acquire(blocking=False))
                with self.assertRaisesRegex(ValueError, "already running"):
                    self.app.mutate("/api/gmail/connect", {})
                with self.assertRaisesRegex(ValueError, "already running"):
                    self.app.mutate("/api/gmail/status", {})
            finally:
                release.set()
                self.connection.thread.join(3)
        auth.assert_called_once_with(self.client, self.token, "manage", on_url=ANY)
        self.assertEqual(self.connection.snapshot()["status"], "connected")
        self.assertEqual(self.app.state()["jobs"], [])

    def test_oauth_errors_are_sanitized_and_do_not_destroy_existing_token(self):
        original = self.token.read_bytes()
        with patch("mail_agent.gmail.authorize", side_effect=RuntimeError("DO-NOT-EXPOSE")):
            state = self.run_operation("connect")
        self.assertIsNone(state["operation"])
        self.assertEqual(state["status"], "unverified")
        self.assertNotIn("DO-NOT-EXPOSE", json.dumps(state))
        self.assertEqual(self.token.read_bytes(), original)

    def test_demo_and_missing_client_cannot_start_oauth(self):
        self.client.unlink()
        with self.assertRaisesRegex(ValueError, "setup"):
            self.run_operation("connect")
        self.app.demo = True
        for operation in ["connect", "status", "sync"]:
            with self.assertRaisesRegex(ValueError, "sample mode"):
                self.run_operation(operation)

    def test_simulation_uses_readonly_oauth_and_cannot_request_live_sync(self):
        self.app.gmail_token = None
        self.token.write_text(json.dumps({"scopes": [READONLY_SCOPE]}))
        with patch("mail_agent.gmail.authorize") as authorize, patch("mail_agent.gmail.service", return_value=self.api):
            self.run_operation("connect")
            authorize.assert_called_once_with(self.client, self.token, "readonly", on_url=ANY)
            with self.assertRaisesRegex(ValueError, "mode changed"):
                self.run_operation("sync", self.consent())
            self.users.messages.return_value.list.return_value.execute.return_value = {}
            state = self.run_operation("sync", self.consent(live=False))
        self.assertEqual(state["last_sync"]["queued"], 0)
