import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from mail_agent.core import Agent, Email, Proposal
from mail_agent.gmail_sync import (configure, defer_poll, fetch_one, history_step, poll_new,
                                   public, refresh_before_analysis, retry_incomplete, set_history_status)
from mail_agent.web import Application


def message(ident, labels=None, body="Synthetic message", thread="thread-1"):
    return {"id": ident, "threadId": thread, "labelIds": labels or ["INBOX", "UNREAD"],
            "internalDate": "1000", "payload": {"mimeType": "text/plain", "headers": [
                {"name": "From", "value": "Sender <sender@example.test>"},
                {"name": "Subject", "value": "Subject " + ident}],
                "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}}


class Fixed:
    def __init__(self, proposal):
        self.proposal = proposal

    def propose(self, email):
        return self.proposal


class GmailSyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.app = Application(root / "state.sqlite3", connection_token=root / "missing-token.json",
                               gmail_credentials=root / "missing-client.json")
        self.api = MagicMock()
        self.users = self.api.users.return_value
        self.users.getProfile.return_value.execute.return_value = {
            "emailAddress": "owner@example.test", "historyId": "10"}
        self.users.labels.return_value.list.return_value.execute.return_value = {"labels": [
            {"id": "work", "name": "Work", "type": "user"},
            {"id": "other", "name": "Other", "type": "user"}]}

    def tearDown(self):
        self.directory.cleanup()

    def config(self, mode="last30", labels=None):
        return configure(self.api, self.app, {"allow_groq": True, "account": "owner@example.test",
            "live": False, "history_mode": mode, "label_ids": labels or []})

    def test_history_scope_pause_resume_progress_and_repeat_are_deduplicated(self):
        self.config(labels=["work"])
        set_history_status(self.app, "owner@example.test", "paused")
        self.assertFalse(history_step(self.api, self.app, "owner@example.test"))
        self.users.messages.return_value.list.assert_not_called()

        set_history_status(self.app, "owner@example.test", "running")
        self.users.messages.return_value.list.return_value.execute.return_value = {
            "messages": [{"id": "one"}, {"id": "two"}], "resultSizeEstimate": 2}
        self.users.messages.return_value.get.return_value.execute.side_effect = [
            message("one", ["INBOX", "UNREAD", "work"]), message("two", ["INBOX", "UNREAD", "other"])]
        self.assertFalse(history_step(self.api, self.app, "owner@example.test"))
        state = public(self.app, "owner@example.test")["settings"]
        self.assertEqual((state["history_status"], state["scanned"], state["imported"], state["skipped"]),
                         ("done", 2, 1, 1))
        self.assertEqual(state["progress"], 100)
        self.assertEqual(len(self.app.state()["jobs"]), 1)

        # Repeating the same selection keeps the completed cursor and unique IDs.
        self.config(labels=["work"])
        self.assertEqual(public(self.app, "owner@example.test")["settings"]["scanned"], 2)
        self.assertEqual(len(self.app.state()["jobs"]), 1)

    def test_history_page_cursor_continues_after_application_restart(self):
        self.config(mode="all")
        messages = self.users.messages.return_value
        messages.list.return_value.execute.return_value = {
            "messages": [{"id": "page-one"}], "nextPageToken": "page-two", "resultSizeEstimate": 2}
        messages.get.return_value.execute.return_value = message("page-one")
        self.assertTrue(history_step(self.api, self.app, "owner@example.test"))

        restarted = Application(self.app.db_path, recover_jobs=False,
                                connection_token=Path(self.directory.name) / "missing-token.json",
                                gmail_credentials=Path(self.directory.name) / "missing-client.json")
        messages.list.return_value.execute.return_value = {"messages": [{"id": "page-two"}]}
        messages.get.return_value.execute.return_value = message("page-two")
        self.assertFalse(history_step(self.api, restarted, "owner@example.test"))
        messages.list.assert_called_with(userId="me", maxResults=25, pageToken="page-two")
        state = public(restarted, "owner@example.test")["settings"]
        self.assertEqual((state["history_status"], state["scanned"], state["imported"]), ("done", 2, 2))
        self.assertEqual(len(restarted.state()["jobs"]), 2)

    def test_incomplete_message_is_locked_and_retried_after_one_hour(self):
        start = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
        self.users.messages.return_value.get.return_value.execute.side_effect = RuntimeError("private")
        self.assertEqual(fetch_one(self.api, self.app, "owner@example.test", {"id": "broken"}, now=start),
                         "incomplete")
        cached = self.app.state()["gmail_messages"][0]
        self.assertEqual(cached["state"], "incomplete")
        self.assertEqual(cached["error"], "Message could not be fully loaded.")
        calls = self.users.messages.return_value.get.call_count
        self.assertEqual(retry_incomplete(self.api, self.app, "owner@example.test", start + timedelta(minutes=59)), 0)
        self.assertEqual(self.users.messages.return_value.get.call_count, calls)

        self.users.messages.return_value.get.return_value.execute.side_effect = None
        self.users.messages.return_value.get.return_value.execute.return_value = message("broken")
        self.assertEqual(retry_incomplete(self.api, self.app, "owner@example.test", start + timedelta(hours=1)), 1)
        self.assertEqual(self.app.state()["gmail_messages"][0]["state"], "complete")
        self.assertEqual(len(self.app.state()["jobs"]), 1)

    def test_incomplete_history_retry_preserves_original_label_filter_after_restart(self):
        start = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
        self.config(labels=["work"])
        self.users.messages.return_value.list.return_value.execute.return_value = {
            "messages": [{"id": "temporarily-broken", "threadId": "thread"}]}
        self.users.messages.return_value.get.return_value.execute.side_effect = RuntimeError("private")
        self.assertFalse(history_step(self.api, self.app, "owner@example.test", start))

        restarted = Application(self.app.db_path, recover_jobs=False,
            connection_token=Path(self.directory.name) / "missing-token.json",
            gmail_credentials=Path(self.directory.name) / "missing-client.json")
        self.users.messages.return_value.get.return_value.execute.side_effect = None
        self.users.messages.return_value.get.return_value.execute.return_value = message(
            "temporarily-broken", ["INBOX", "UNREAD", "other"])
        self.assertEqual(retry_incomplete(self.api, restarted, "owner@example.test",
                                         start + timedelta(hours=1)), 1)
        self.assertEqual(restarted.state()["jobs"], [])
        self.assertEqual(restarted.state()["gmail_messages"], [])

    def test_new_mail_ignores_history_label_filter_and_advances_cursor(self):
        self.config(mode="new", labels=["work"])
        self.users.history.return_value.list.return_value.execute.return_value = {
            "historyId": "12", "history": [{"messagesAdded": [{"message": {"id": "new", "threadId": "t2"}}]}]}
        self.users.messages.return_value.get.return_value.execute.return_value = message("new", ["INBOX", "UNREAD", "other"])
        result = poll_new(self.api, self.app, "owner@example.test",
                          datetime(2026, 9, 11, 13, tzinfo=timezone.utc))
        self.assertEqual(result["added"], 1)
        self.assertEqual(len(self.app.state()["jobs"]), 1)
        with self.app.connect() as db:
            self.assertEqual(db.execute("SELECT history_id FROM gmail_sync_settings").fetchone()[0], "12")

    def test_history_labels_are_or_after_last_30_selection_and_attachments_are_recorded(self):
        self.config(labels=["work", "other"])
        self.users.messages.return_value.list.return_value.execute.return_value = {
            "messages": [{"id": "work-mail"}, {"id": "other-mail"}, {"id": "none"}]}
        attached = message("work-mail", ["INBOX", "UNREAD", "work"])
        attached["payload"] = {"mimeType": "multipart/mixed", "headers": attached["payload"]["headers"],
            "parts": [{"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"Body").decode()}},
                      {"mimeType": "application/pdf", "filename": "terms.pdf",
                       "body": {"attachmentId": "private"}}]}
        self.users.messages.return_value.get.return_value.execute.side_effect = [
            attached, message("other-mail", ["INBOX", "UNREAD", "other"]),
            message("none", ["INBOX", "UNREAD"])]
        history_step(self.api, self.app, "owner@example.test")
        self.users.messages.return_value.list.assert_called_with(userId="me", maxResults=25)
        with self.app.connect() as db:
            bindings = {row["message_id"]: row for row in db.execute("SELECT * FROM gmail_bindings")}
        self.assertEqual(set(bindings), {"work-mail", "other-mail"})
        self.assertTrue(bindings["work-mail"]["has_attachments"])

    def test_sent_and_user_drafts_are_context_not_agent_jobs(self):
        self.users.messages.return_value.get.return_value.execute.side_effect = [
            message("sent", ["SENT"], thread="shared"), message("draft", ["DRAFT"], thread="shared")]
        self.assertEqual(fetch_one(self.api, self.app, "owner@example.test", {"id": "sent"}), "context")
        self.assertEqual(fetch_one(self.api, self.app, "owner@example.test", {"id": "draft"}), "context")
        self.assertEqual(self.app.state()["jobs"], [])
        self.assertEqual({x["role"] for x in self.app.state()["gmail_messages"]}, {"sent", "draft"})
        self.assertTrue(all(not x["managed"] for x in self.app.state()["gmail_messages"] if x["role"] == "draft"))

    def test_wajo_draft_marker_is_distinguished_from_user_draft(self):
        email = Email("gmail:owner@example.test:source", "sender@example.test", "Question", "Please answer")
        self.app.enqueue({"sender": email.sender, "subject": email.subject, "body": email.body}, email.id,
            {"account": "owner@example.test", "message_id": "source", "initial_inbox": 1,
             "initial_unread": 1, "thread_id": "shared", "source_role": "incoming"})
        agent = Agent(self.app.db_path, Fixed(Proposal("draft", "Prepare answer", text="Answer",
            recipient=email.sender, label_kind="support_response", pattern_evidence="Please answer")))
        try:
            action = agent.ingest(email)
            key = agent.db.execute("SELECT message_key FROM gmail_replies WHERE action_id=?",
                                   (action["id"],)).fetchone()[0]
        finally:
            agent.close()
        draft = message("wajo-draft", ["DRAFT"], body="Answer", thread="shared")
        draft["payload"]["headers"].append({"name": "X-Wajo-Reply-Key", "value": key})
        self.users.messages.return_value.get.return_value.execute.return_value = draft
        self.assertEqual(fetch_one(self.api, self.app, "owner@example.test", {"id": "wajo-draft"}), "context")
        cached = next(x for x in self.app.state()["gmail_messages"] if x["message_id"] == "wajo-draft")
        self.assertTrue(cached["managed"])

    def test_spam_and_trash_are_excluded_even_without_a_readable_body(self):
        self.users.messages.return_value.get.return_value.execute.return_value = {
            "id": "trash", "threadId": "discarded", "labelIds": ["TRASH"], "payload": {}}
        self.assertEqual(fetch_one(self.api, self.app, "owner@example.test", {"id": "trash"}), "context")
        self.assertEqual(self.app.state()["jobs"], [])
        self.assertEqual(self.app.state()["gmail_messages"], [])
        with self.app.connect() as db:
            cached = db.execute("SELECT role,state FROM gmail_message_cache").fetchone()
        self.assertEqual(tuple(cached), ("excluded", "complete"))

    def test_failed_poll_backoff_does_not_advance_cursor(self):
        start = datetime(2026, 9, 11, 14, tzinfo=timezone.utc)
        self.config(mode="new")
        defer_poll(self.app, "owner@example.test", start)
        with self.app.connect() as db:
            row = db.execute("SELECT history_id,next_poll_at FROM gmail_sync_settings").fetchone()
        self.assertEqual(row["history_id"], "10")
        self.assertEqual(row["next_poll_at"], (start + timedelta(minutes=1)).isoformat())

    def test_already_read_mail_suppresses_proactive_action_but_keeps_organization(self):
        email = Email("gmail:owner@example.test:read", "sender@example.test", "Question", "Please answer")
        self.app.enqueue({"sender": email.sender, "subject": email.subject, "body": email.body}, email.id,
            {"account": "owner@example.test", "message_id": "read", "initial_inbox": 1,
             "initial_unread": 0, "thread_id": "thread", "source_role": "incoming"})
        agent = Agent(self.app.db_path, Fixed(Proposal("send", "Reply needed", text="Answer", recipient=email.sender,
            needs_human=True, label_kind="support_response", pattern_evidence="Please answer",
            attention_cue="decision_required", attention_evidence="Please answer")))
        try:
            row = agent.ingest(email)
            self.assertEqual(row["proposal"]["action"], "none")
            self.assertEqual(row["status"], "executed")
            self.assertEqual(agent.snapshot()["drafts"], [])
            self.assertEqual(agent.snapshot()["attention_items"], [])
            self.assertTrue(agent.snapshot()["email_organization"])
            self.assertIn("read_mail_action_suppressed", [x["event"] for x in agent.snapshot()["audit"]])
        finally:
            agent.close()

    def test_already_read_label_keeps_label_but_cannot_escalate(self):
        email = Email("gmail:owner@example.test:read-label", "sender@example.test", "Receipt", "Support received")
        self.app.enqueue({"sender": email.sender, "subject": email.subject, "body": email.body}, email.id,
            {"account": "owner@example.test", "message_id": "read-label", "initial_inbox": 1,
             "initial_unread": 0, "thread_id": "thread", "source_role": "incoming"})
        proposal = Proposal("label", "Potentially suspicious", label="AI: Support", suspicious=True,
            needs_human=True, label_kind="support_receipt", pattern_evidence="Support received")
        agent = Agent(self.app.db_path, Fixed(proposal))
        try:
            row = agent.ingest(email)
            self.assertEqual(row["proposal"]["action"], "label")
            self.assertFalse(row["proposal"]["suspicious"])
            self.assertFalse(row["proposal"]["needs_human"])
            self.assertEqual(row["autonomy"], "silent")
            self.assertEqual(row["status"], "executing")
            operation = agent.snapshot()["gmail_operations"][0]
            self.assertEqual((operation["operation"], operation["status"]), ("label", "queued"))
        finally:
            agent.close()

    def test_failed_analysis_of_already_read_mail_is_retried_without_escalation(self):
        class Broken:
            def propose(self, email):
                raise RuntimeError("temporary")
        email = Email("gmail:owner@example.test:read-error", "sender@example.test", "Read", "Body")
        self.app.enqueue({"sender": email.sender, "subject": email.subject, "body": email.body}, email.id,
            {"account": "owner@example.test", "message_id": "read-error", "initial_inbox": 1,
             "initial_unread": 0, "thread_id": "thread", "source_role": "incoming"})
        agent = Agent(self.app.db_path, Broken())
        try:
            row = agent.ingest(email)
            self.assertEqual(row["status"], "error")
            self.assertEqual(row["autonomy"], "silent")
            self.assertNotIn("notification", [x["event"] for x in agent.snapshot()["audit"]])
        finally:
            agent.close()

    def test_read_state_is_refreshed_immediately_before_analysis(self):
        event = "gmail:owner@example.test:became-read"
        self.app.enqueue({"sender": "sender@example.test", "subject": "Read", "body": "Body"}, event,
            {"account": "owner@example.test", "message_id": "became-read", "initial_inbox": 1,
             "initial_unread": 1, "thread_id": "thread", "source_role": "incoming"})
        token = Path(self.directory.name) / "token.json"
        token.write_text("{}")
        self.users.messages.return_value.get.return_value.execute.return_value = {"labelIds": ["INBOX"]}
        with patch("mail_agent.gmail.service", return_value=self.api):
            refresh_before_analysis(token, self.app, event)
        with self.app.connect() as db:
            self.assertEqual(db.execute("SELECT initial_unread FROM gmail_bindings WHERE email_id=?", (event,)).fetchone()[0], 0)
        self.users.messages.return_value.get.assert_called_with(userId="me", id="became-read", format="minimal")

    def test_message_moved_to_special_folder_before_analysis_is_excluded(self):
        event = "gmail:owner@example.test:moved"
        self.app.enqueue({"sender": "sender@example.test", "subject": "Moved", "body": "Body"}, event,
            {"account": "owner@example.test", "message_id": "moved", "initial_inbox": 1,
             "initial_unread": 1, "thread_id": "thread", "source_role": "incoming"})
        with self.app.connect() as db:
            db.execute("""INSERT INTO gmail_message_cache(account,message_id,thread_id,role,state)
                          VALUES('owner@example.test','moved','thread','incoming','complete')""")
        token = Path(self.directory.name) / "token.json"
        token.write_text("{}")
        self.users.messages.return_value.get.return_value.execute.return_value = {"labelIds": ["TRASH"]}
        with patch("mail_agent.gmail.service", return_value=self.api):
            self.assertFalse(refresh_before_analysis(token, self.app, event))
        self.assertEqual(self.app.state()["gmail_messages"], [])

    def test_failed_analysis_is_not_seen_and_can_complete_on_retry(self):
        class Flaky:
            def __init__(self): self.count = 0
            def propose(self, email):
                self.count += 1
                if self.count == 1:
                    raise RuntimeError("temporary")
                return Proposal("label", "Recovered", label="AI: Work")
        agent = Agent(self.app.db_path, Flaky())
        try:
            email = Email("retry", "sender@example.test", "Retry", "Body")
            failed = agent.ingest(email)
            self.assertEqual(failed["status"], "error")
            completed = agent.ingest(email, retry_error=True)
            self.assertEqual(completed["status"], "executed")
            self.assertEqual(len(agent.snapshot()["actions"]), 1)
            self.assertIn("analysis_retried", [x["event"] for x in agent.snapshot()["audit"]])
        finally:
            agent.close()

    def test_analysis_pool_never_exceeds_four_messages(self):
        class Blocking:
            def __init__(self):
                self.calls = []
                self.active = 0
                self.maximum = 0
                self.lock = threading.Lock()
                self.four = threading.Event()
                self.release = threading.Event()
            def propose(self, email):
                with self.lock:
                    self.active += 1
                    self.maximum = max(self.maximum, self.active)
                    if self.active == 4:
                        self.four.set()
                self.release.wait(3)
                with self.lock:
                    self.active -= 1
                    self.calls.append({"status": "ok"})
                return Proposal("label", "Work", label="AI: Work")
        provider = Blocking()
        for index in range(8):
            self.app.enqueue({"sender": "sender@example.test", "subject": f"Mail {index}", "body": "Body"},
                             f"parallel-{index}")
        with patch("mail_agent.groq_provider.GroqProposer.from_env", return_value=provider):
            self.app.worker.start()
            try:
                self.assertTrue(provider.four.wait(3))
                time.sleep(.1)
                self.assertEqual(provider.maximum, 4)
            finally:
                provider.release.set()
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    if all(x["status"] == "done" for x in self.app.state()["jobs"]):
                        break
                    time.sleep(.05)
                self.app.stop.set(); self.app.wakeup.set(); self.app.worker.join(4)
        self.assertTrue(all(x["status"] == "done" for x in self.app.state()["jobs"]))
        self.assertEqual(provider.maximum, 4)
