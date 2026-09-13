import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from mail_agent.core import Agent, Email, Proposal
from mail_agent.web import create_server
from mail_agent.model_settings import KeyValidation, USER_GROQ


class WebTests(unittest.TestCase):
    def test_retry_requeues_failed_analysis_but_not_healthy_job(self):
        with self.server.app.connect() as db:
            db.execute("INSERT INTO incoming_jobs(id,email,status,created_at) VALUES('retry-me','{}','error','now')")
        self.assertTrue(self.post("/api/retry", {"email_id": "retry-me"})["retried"])
        with self.server.app.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM incoming_jobs WHERE id='retry-me'").fetchone()[0], "queued")
        with self.assertRaises(HTTPError):
            self.post("/api/retry", {"email_id": "retry-me"})

    def test_retry_uncertain_gmail_write_only_reconciles(self):
        actions = self.post("/api/demo", {})
        action = actions[0]
        with self.server.app.connect() as db:
            cursor = db.execute("INSERT INTO gmail_operations(action_id,revision,operation,status) VALUES(?,?,'label','unknown')",
                                (action["id"], action["revision"]))
            operation_id = cursor.lastrowid
        self.server.app.gmail_token = Path(self.directory.name) / "token.json"
        with patch.object(self.server.app, "run_gmail") as run:
            response = self.post("/api/retry", {"action_id": action["id"], "revision": action["revision"]})
            run.assert_called_once_with(operation_id, check_only=True)
            self.assertFalse(response["retried"])
        with self.server.app.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM gmail_operations WHERE id=?", (operation_id,)).fetchone()[0], "unknown")

    def test_model_settings_require_current_validation_and_never_expose_key(self):
        key = "gsk_private_test_key"
        with patch("mail_agent.web.validate_user_key", return_value=KeyValidation(True)):
            checked = self.post("/api/models/validate", {"mode": USER_GROQ, "key": key})
            later = self.post("/api/models/validate", {"mode": USER_GROQ, "key": key + "-later"})
        self.assertTrue(checked["valid"])
        self.assertTrue(later["valid"])
        with self.assertRaises(HTTPError):
            self.post("/api/models/apply", {"mode": USER_GROQ, "concurrency": 7,
                      "key": key + "-changed", "validation_token": checked["token"]})
        applied = self.post("/api/models/apply", {"mode": USER_GROQ, "concurrency": 7,
                            "key": key, "validation_token": checked["token"]})
        self.assertTrue(applied["applied"])
        state = self.get()
        self.assertEqual((state["models"]["mode"], state["models"]["concurrency"]),
                         (USER_GROQ, 7))
        self.assertEqual(self.server.app.analysis_limit, 7)
        self.assertNotIn(key, json.dumps(state))

    def test_event_decision_is_independent_and_visible_in_calendar(self):
        quote = "Project review on 2027-01-20 at 10:00 UTC"
        class Fixed:
            def propose(self, email):
                return Proposal(
                    "none", "Calendar item", requires_action=False, has_deadline=False,
                    significant_change=False, sensitive=False,
                    event_change="create", event_kind="calendar_event",
                    event_semantic_kind="project_review", event_title="Project review",
                    event_original_text=quote, event_start="2027-01-20T10:00:00+00:00",
                    event_timezone="UTC", event_confidence="clear", event_evidence=quote)
        agent = self.server.app.agent(Fixed())
        try:
            action = agent.ingest(Email("event-web", "team@example.test", "Review", quote))
        finally:
            agent.close()
        before = self.get()
        row = next(item for item in before["actions"] if item["id"] == action["id"])
        proposal = next(item for item in before["event_proposals"]
                        if item["source_email_id"] == "event-web")
        self.assertEqual(row["status"], "executed")
        self.assertTrue(row["awaiting_event_decision"])
        self.assertEqual(before["events"], [])
        self.post("/api/events/approve", {"proposal_id": proposal["id"],
                  "revision": proposal["revision"]})
        after = self.get()
        self.assertFalse(next(item for item in after["actions"]
                              if item["id"] == action["id"])["awaiting_event_decision"])
        self.assertEqual(after["events"][0]["title"], "Project review")
        self.assertEqual(after["event_skills"][0]["approval_streak"], 1)
        with self.server.app.connect() as db:
            reminder = db.execute(
                "SELECT status FROM notification_jobs WHERE kind='event_reminder'"
            ).fetchone()
        self.assertEqual(reminder["status"], "pending")
        skill = after["event_skills"][0]
        self.post("/api/event-skills/manage", {"skill_id": skill["id"],
                  "revision": skill["revision"], "operation": "pause"})
        paused = self.get()["event_skills"][0]
        self.assertEqual(paused["status"], "paused")
        self.assertFalse(paused["qualified"])

    def test_event_skill_qualifies_auto_saves_and_mistake_returns_to_questions(self):
        def analyzed(email_id, day):
            quote = f"Project review on 2027-02-{day:02d}"
            class Fixed:
                def propose(self, email):
                    return Proposal(
                        "none", "Calendar item", event_change="create",
                        event_kind="calendar_event", event_semantic_kind="project_review",
                        event_title="Project review", event_original_text=quote,
                        event_start=f"2027-02-{day:02d}", event_all_day=True,
                        event_confidence="clear", event_evidence=quote)
            agent = self.server.app.agent(Fixed())
            try:
                return agent.ingest(Email(email_id, "team@example.test", "Project review", quote))
            finally:
                agent.close()

        for index, day in enumerate((10, 11), 1):
            action = analyzed(f"event-train-{index}", day)
            proposal = next(item for item in self.get()["event_proposals"]
                            if item["source_email_id"] == action["email_id"])
            self.post("/api/events/approve", {"proposal_id": proposal["id"],
                      "revision": proposal["revision"]})
        self.assertTrue(self.get()["event_skills"][0]["qualified"])

        automatic_action = analyzed("event-automatic", 12)
        automatic_state = self.get()
        automatic_proposal = next(item for item in automatic_state["event_proposals"]
                                  if item["source_email_id"] == automatic_action["email_id"])
        self.assertEqual(automatic_proposal["status"], "approved")
        self.assertTrue(automatic_proposal["automatic"])
        automatic_event = next(item for item in automatic_state["events"]
                               if item["proposal_id"] == automatic_proposal["id"])
        correction = self.post("/api/events/mistake", {"event_id": automatic_event["id"]})
        self.assertEqual(correction["correction"]["mode"], "ask")
        after = self.get()
        self.assertFalse(after["event_skills"][0]["qualified"])
        self.assertNotIn(automatic_event["id"], {item["id"] for item in after["events"]})

    def test_attention_state_exposes_disabled_effective_exception(self):
        from mail_agent.attention import set_rule
        class Fixed:
            def propose(self, email):
                return Proposal('label', 'Booking', label='AI: Travel',
                                attention_cue='personal_commitment', attention_evidence='Confirmed')
        agent = self.server.app.agent(Fixed())
        try:
            row = agent.ingest(Email('attention-1', 'one@example.test', 'Trip', 'Confirmed'))
            set_rule(agent, row['id'], True, 'similar')
            set_rule(agent, row['id'], False, 'sender')
        finally:
            agent.close()
        action = next(r for r in self.server.app.state()['actions'] if r['id'] == row['id'])
        self.assertEqual(action['attention_effective_rule']['scope'], 'one@example.test')
        self.assertEqual(action['attention_effective_rule']['enabled'], 0)

    def test_duplicate_start_does_not_reset_processing_queue(self):
        self.server.app.demo = False
        self.server.app.enqueue({"sender": "test@example.test", "subject": "Test", "body": "Synthetic"})
        with self.server.app.connect() as db:
            db.execute("UPDATE incoming_jobs SET status='processing'")
        with self.assertRaises(OSError):
            create_server(self.server.app.db_path, self.server.server_address[1])
        self.assertEqual(self.server.app.state()["jobs"][0]["status"], "processing")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.server = create_server(Path(self.directory.name) / "web.sqlite3", 0, True,
                                    connection_token=Path(self.directory.name) / "token.json",
                                    gmail_credentials=Path(self.directory.name) / "client.json")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.app.stop.set()
        self.server.app.wakeup.set()
        if self.server.app.worker.is_alive():
            self.server.app.worker.join(3)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def get(self, path="/api/state"):
        try:
            with urlopen(self.url + path, timeout=3) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            exc.close()
            raise

    def post(self, path, data, **headers):
        request = Request(self.url + path, data=json.dumps(data).encode(), headers={
            "Content-Type": "application/json", "Origin": self.url,
            "X-CSRF-Token": self.server.app.csrf, **headers})
        try:
            with urlopen(request, timeout=3) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            exc.close()
            raise

    def test_host_origin_csrf_and_static_paths(self):
        for headers in ({"Host": "evil.test"}, {"Origin": "https://evil.test"}, {"X-CSRF-Token": "wrong"}):
            with self.assertRaises(HTTPError) as error:
                self.post("/api/demo", {}, **headers)
            self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError):
            self.get("/../.env")
        with urlopen(self.url) as response:
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
            self.assertIn(b"app.js", response.read())
        self.assertEqual(self.get()["emails"], [])

    def test_demo_approval_correction_and_count_sources(self):
        rows = self.post("/api/demo", {})
        self.post("/api/demo", {})
        self.assertEqual(len(self.get()["emails"]), 7)
        archive = next(r for r in rows if r["proposal"]["action"] == "archive")
        args = {"action_id": archive["id"], "revision": 1}
        self.post("/api/approve", args)
        self.assertEqual(sum(e["archived"] for e in self.get()["emails"]), 1)
        with self.assertRaises(HTTPError):
            self.post("/api/approve", args)
        self.post("/api/correct", args)
        self.assertEqual(sum(e["archived"] for e in self.get()["emails"]), 0)

    def test_blocked_action_not_approvable_and_edit_revision_required(self):
        rows = self.post("/api/demo", {})
        blocked = next(r for r in rows if r["status"] == "blocked")
        with self.assertRaises(HTTPError):
            self.post("/api/approve", {"action_id": blocked["id"], "revision": 1})
        send = next(r for r in rows if r["proposal"]["action"] == "send")
        args = {"action_id": send["id"], "revision": 1, "recipient": "new@example.test", "text": "Received"}
        updated = self.post("/api/edit", args)
        self.assertEqual(updated["revision"], 2)
        with self.assertRaises(HTTPError):
            self.post("/api/approve", args)
        self.assertEqual(self.get()["sent"], [])

    def test_explicit_rule_blocks_existing_pending_archive(self):
        rows = self.post("/api/demo", {})
        archive = next(r for r in rows if r["proposal"]["action"] == "archive")
        self.post("/api/rule", {"sender": "news@example.test"})
        with self.assertRaises(HTTPError):
            self.post("/api/approve", {"action_id": archive["id"], "revision": 1})
        self.assertEqual(sum(e["archived"] for e in self.get()["emails"]), 0)

    def test_arbitrary_input_cannot_use_scripted_mode(self):
        with self.assertRaises(HTTPError):
            self.post("/api/ingest", {"sender": "x@example.test", "subject": "hello", "body": "test"})

    def test_organization_route_keeps_action_permission_separate(self):
        rows = self.post("/api/demo", {})
        row = rows[0]
        before = next(a for a in self.get()["actions"] if a["id"] == row["id"])
        result = self.post("/api/organization", {"action_id": row["id"], "topic": "Work",
                           "subtype": "Important update", "important": True, "scope": "email"})
        self.assertEqual(result["topic"], "Work")
        after = next(a for a in self.get()["actions"] if a["id"] == row["id"])
        self.assertTrue(after["organization"]["important"])
        self.assertEqual((after["autonomy"], after["status"]), (before["autonomy"], before["status"]))

    def test_local_draft_edit_can_save_style_without_sending(self):
        class Fixed:
            def propose(self, email):
                return Proposal("send", "Reply", text="Hello, thank you for the update. Best,",
                                recipient=email.sender, label_kind="support_response",
                                pattern_evidence="A fix is ready")
        agent = Agent(self.server.app.db_path, Fixed())
        try:
            action = agent.ingest(Email("style-web", "support@example.test", "Fix available",
                                        "A fix is ready. Please try again."))
        finally:
            agent.close()
        edited = self.post("/api/edit", {"action_id": action["id"], "revision": 1,
                           "recipient": "support@example.test", "text": "Thanks, received."})
        row = next(a for a in self.get()["actions"] if a["id"] == action["id"])
        self.assertEqual(edited["status"], "pending")
        self.assertIn("concise", row["draft_style_preview"]["summary"])
        saved = self.post("/api/draft-style", {"action_id": action["id"], "revision": 2, "scope": "similar"})
        self.assertTrue(saved["suggested"])
        state = self.get()
        self.assertEqual(len(state["draft_style_rules"]), 0)
        skill = next(s for s in state['skills'] if s['family'] == 'draft')
        self.assertEqual(skill['status'], 'suggested')
        data = {'skill_id': skill['id'], 'scope': 'similar', 'exclusions': []}
        preview = self.post('/api/skills/preview', data)
        self.post('/api/skills/save', dict(data, token=preview['token'], reviewed=[e['id'] for e in preview['examples']]))
        state = self.get()
        self.assertEqual(state["sent"], [])
        row = next(a for a in state["actions"] if a["id"] == action["id"])
        self.assertTrue(row["draft_style_saved"])
        self.assertIsNone(row["draft_style_preview"])
        self.assertIn("saved", row["draft_style_note"])

    def test_unchanged_reply_can_be_explicitly_confirmed_as_style(self):
        class Fixed:
            def propose(self, email):
                return Proposal("send", "Reply", text="Thanks, received.",
                                recipient=email.sender, label_kind="support_response",
                                pattern_evidence="A fix is ready")
        agent = Agent(self.server.app.db_path, Fixed())
        try:
            action = agent.ingest(Email("style-unchanged", "support@example.test", "Fix available",
                                        "A fix is ready. Please try again."))
        finally:
            agent.close()
        self.post("/api/edit", {"action_id": action["id"], "revision": 1,
                  "recipient": "support@example.test", "text": "Thanks, received."})
        row = next(a for a in self.get()["actions"] if a["id"] == action["id"])
        self.assertEqual(row["draft_style_preview"]["basis"], "confirmed")
        saved = self.post("/api/draft-style", {"action_id": action["id"], "revision": 2,
                          "scope": "similar"})
        self.assertTrue(saved["suggested"])
        self.assertEqual(self.get()["sent"], [])

    def test_gmail_routes_require_csrf_and_return_async_status(self):
        from unittest.mock import MagicMock
        from mail_agent.gmail import READONLY_SCOPE
        connection = self.server.app.gmail_connection
        connection.token_path = Path(self.directory.name) / "token.json"
        connection.token_path.write_text(json.dumps({"scopes": [READONLY_SCOPE], "token": "PRIVATE"}))
        self.server.app.demo = False
        api = MagicMock()
        api.users.return_value.getProfile.return_value.execute.return_value = {"emailAddress": "owner@example.test"}
        api.users.return_value.labels.return_value.list.return_value.execute.return_value = {"labels": []}
        with patch("mail_agent.gmail.service", return_value=api) as service:
            for route in ["status", "connect", "sync"]:
                with self.assertRaises(HTTPError) as error:
                    self.post("/api/gmail/" + route, {}, **{"X-CSRF-Token": "wrong"})
                self.assertEqual(error.exception.code, 403)
            service.assert_not_called()
            self.assertEqual(self.post("/api/gmail/status", {}), {"started": "check"})
            connection.thread.join(3)
            state = self.get()
        self.assertEqual(state["gmail_connection"]["status"], "connected")
        self.assertNotIn("PRIVATE", json.dumps(state))
        with self.assertRaises(HTTPError):
            self.get("/api/gmail/connect")

    def test_background_processing_and_diagnostics(self):
        self.server.app.demo = False
        class FakeProvider:
            calls = [{"status": "ok", "http_attempts": [{"http_status": 200}]}]
            def propose(self, email):
                return Proposal("label", "test", label="AI: Работа")
        with patch("mail_agent.web.GroqProposer.from_env", return_value=FakeProvider()):
            self.server.app.worker.start()
            self.post("/api/ingest", {"sender": "x@example.test", "subject": "<script>alert(1)</script>", "body": "<img src=x onerror=alert(1)>"})
            deadline = time.monotonic() + 3
            state = self.get()
            # An action can become visible before its background job finishes.
            while (not state["jobs"] or any(job["status"] not in {"done", "error"}
                                            for job in state["jobs"])) and time.monotonic() < deadline:
                time.sleep(.02)
                state = self.get()
            self.assertTrue(state["jobs"])
            self.assertTrue(all(job["status"] == "done" for job in state["jobs"]))
            self.assertEqual(len(state["labels"]), 1)
            self.assertIn("<script>", state["emails"][0]["subject"])
            # The frontend must render this as text, never as markup.
            self.assertEqual(state["actions"][0]["status"], "executed")


if __name__ == "__main__":
    unittest.main()
