import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch, ANY

from mail_agent.gmail import MANAGE_SCOPE, READONLY_SCOPE
from mail_agent.core import Email
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
                            {"label_ids": [1]}, {"label_ids": [str(x) for x in range(11)]}, {"extra": True}]:
                with self.assertRaises(ValueError):
                    self.app.mutate("/api/gmail/sync", self.modern_consent(**changes))
            service.assert_not_called()

    def test_account_switch_keep_pauses_old_writes_and_resumes_when_switching_back(self):
        self.check()
        with patch("mail_agent.gmail.service", return_value=self.api):
            self.run_operation("sync", self.modern_consent())
        agent = self.app.agent()
        try:
            action = agent.ingest(Email("old", "sender@example.test", "Old", "Old local message"))
            with agent.db:
                agent.db.execute("""INSERT INTO skills(family,account,source_id,status,config,origin)
                                  VALUES('attention','owner@example.test',?,'active',?,'switch:test')""",
                                 (action["id"], json.dumps({"enabled": True, "kind": "decision_required",
                                  "scope": "similar", "contains": "", "excludes": ""})))
                agent.db.execute("""INSERT INTO gmail_bindings
                    (email_id,account,message_id,label_id,label_name,initial_inbox)
                    VALUES(?,?,?,?,?,?)""", ("old", "owner@example.test", "old-message", "", "", 1))
                agent.db.execute("UPDATE actions SET transport='gmail' WHERE id=?", (action["id"],))
                agent.queue_gmail(action["id"], "archive")
        finally:
            agent.close()
        self.users.getProfile.return_value.execute.return_value = {
            "emailAddress": "other@example.test", "historyId": "11"}
        switched = self.check()
        self.assertEqual(switched["status"], "account_choice")
        self.assertEqual(switched["previous_account"], "owner@example.test")
        self.assertEqual(switched["switch_counts"]["emails"], 1)
        self.assertEqual(switched["switch_counts"]["skills"], 1)
        with self.assertRaisesRegex(ValueError, "connection"):
            self.app.mutate("/api/gmail/sync", self.modern_consent(account="other@example.test"))
        result = self.app.mutate("/api/gmail/account-choice", {
            "account": "other@example.test", "previous_account": "owner@example.test", "choice": "keep"})
        self.assertEqual(result["choice"], "keep")
        self.assertIsNone(result["removed"])
        self.assertEqual(self.connection.snapshot()["status"], "connected")
        with self.app.connect() as db:
            self.assertEqual(db.execute("SELECT active_account FROM gmail_account_state").fetchone()[0],
                             "other@example.test")
            self.assertEqual(db.execute("SELECT count(*) FROM emails").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM skills").fetchone()[0], 1)
            operation = db.execute("SELECT status FROM gmail_operations WHERE action_id=?",
                                   (action["id"],)).fetchone()
            self.assertEqual(operation["status"], "account_paused")
        self.users.messages.return_value.modify.assert_not_called()

        self.users.getProfile.return_value.execute.return_value = {
            "emailAddress": "owner@example.test", "historyId": "12"}
        switched_back = self.check()
        self.assertEqual(switched_back["status"], "account_choice")
        self.app.mutate("/api/gmail/account-choice", {
            "account": "owner@example.test", "previous_account": "other@example.test", "choice": "keep"})
        with self.app.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM gmail_operations WHERE action_id=?",
                                       (action["id"],)).fetchone()[0], "queued")

    def test_legacy_binding_requires_account_choice_before_adopting_new_account(self):
        agent = self.app.agent()
        try:
            action = agent.ingest(Email("legacy", "sender@example.test", "Old", "Old local message"))
            with agent.db:
                agent.db.execute("""INSERT INTO gmail_bindings
                    (email_id,account,message_id,label_id,label_name,initial_inbox)
                    VALUES(?,?,?,?,?,?)""", ("legacy", "legacy@example.test", "legacy-message", "", "", 1))
        finally:
            agent.close()
        self.users.getProfile.return_value.execute.return_value = {
            "emailAddress": "other@example.test", "historyId": "11"}
        state = self.check()
        self.assertEqual(state["status"], "account_choice")
        self.assertEqual(state["previous_account"], "legacy@example.test")

    def test_start_fresh_counts_and_atomically_clears_every_local_mail_table(self):
        self.check()
        with patch("mail_agent.gmail.service", return_value=self.api):
            self.run_operation("sync", self.modern_consent())
        agent = self.app.agent()
        try:
            action = agent.ingest(Email("old", "sender@example.test", "Old", "Old local message"))
            with agent.db:
                agent.db.execute("""INSERT INTO skills(family,account,source_id,config,origin)
                                  VALUES('attention','owner@example.test',?,?, 'fresh:test')""",
                                 (action["id"], json.dumps({"enabled": True, "kind": "decision_required",
                                  "scope": "similar", "contains": "", "excludes": ""})))
                skill_id = agent.db.execute("SELECT id FROM skills WHERE source_id=?", (action["id"],)).fetchone()[0]
                agent.db.execute("""INSERT INTO superpower_applications
                    (action_id,skill_id,skill_revision,account,applied_at)
                    VALUES(?,?,?,?,?)""", (action["id"], skill_id, 1, "owner@example.test", "2026-09-12T00:00:00Z"))
                agent.db.execute("""INSERT INTO superpower_settings
                    (singleton,enabled,account,reviewed_at,updated_at) VALUES(1,1,?,?,?)""",
                    ("owner@example.test", "2026-09-12T00:00:00Z", "2026-09-12T00:00:00Z"))
        finally:
            agent.close()
        self.users.getProfile.return_value.execute.return_value = {
            "emailAddress": "other@example.test", "historyId": "11"}
        switched = self.check()
        cleared_tables = (
            "autosent_journal", "superpower_confirmations", "superpower_revocations",
            "superpower_applications", "superpower_settings", "gmail_operations", "gmail_replies",
            "label_conflicts", "label_targets", "skill_examples", "skill_legacy_links",
            "skill_feedback_seen", "skill_draft_seen", "skills", "draft_style_applications",
            "draft_edit_versions", "draft_style_rules", "draft_style_feedback", "email_organization",
            "organization_rules", "organization_feedback", "attention_items", "attention_rules",
            "attention_feedback", "label_rules", "label_feedback", "label_reviews",
            "preference_feedback", "archive_rules", "sent", "drafts", "labels", "audit",
            "gmail_bindings", "actions", "emails", "incoming_jobs", "gmail_message_cache",
            "gmail_sync_settings")
        with self.app.connect() as db:
            exact_total = sum(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                              for table in cleared_tables)
        self.assertEqual(switched["switch_counts"]["total_records"], exact_total)
        result = self.app.mutate("/api/gmail/account-choice", {
            "account": "other@example.test", "previous_account": "owner@example.test", "choice": "fresh"})
        self.assertEqual(result["removed"]["emails"], 1)
        self.assertEqual(result["removed"]["skills"], 1)
        with self.app.connect() as db:
            self.assertEqual(db.execute("SELECT active_account FROM gmail_account_state").fetchone()[0],
                             "other@example.test")
            for table in cleared_tables:
                self.assertEqual(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
        self.users.messages.assert_not_called()

    def test_automatic_poll_rechecks_account_and_backs_off_without_reading_history(self):
        self.users.labels.return_value.list.return_value.execute.return_value = {"labels": []}
        self.check()
        with patch("mail_agent.gmail.service", return_value=self.api):
            self.run_operation("sync", self.modern_consent())
            with self.app.connect() as db:
                db.execute("UPDATE gmail_sync_settings SET sync_enabled=0 WHERE account=?", ("owner@example.test",))
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
