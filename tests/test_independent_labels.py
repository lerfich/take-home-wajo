"""Independent Gmail label decisions use isolated state and verified writes."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from mail_agent.core import Agent, Email, Proposal
from mail_agent.gmail_executor import recover, run_one
from mail_agent.label_preferences import decide_independent, public_independent


class Fixed:
    def __init__(self, proposal):
        self.proposal = proposal

    def propose(self, email):
        return self.proposal


class VerifiedExecutor:
    def __init__(self, verified=True):
        self.verified = verified
        self.calls = []

    def apply(self, binding, operation, label="", check_only=False):
        self.calls.append((operation, label, check_only))
        return {"verified": self.verified, "already_present": False}


class IndependentLabelTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "state.sqlite3"
        self.proposal = Proposal(
            "none", "Routine update", label="AI: Work Update",
            independent_label=True, label_kind="work_status",
            archive_recommendation="keep", archive_reason="Useful update",
            archive_evidence="Project update received",
            requires_action=False, has_deadline=False,
            significant_change=False, sensitive=False,
        )
        self.agent = Agent(self.path, Fixed(self.proposal))

    def tearDown(self):
        self.agent.close()
        self.directory.cleanup()

    def ingest(self, index, unread=True):
        email_id = f"gmail:owner@example.test:m{index}"
        with self.agent.db:
            self.agent.db.execute("""INSERT INTO gmail_bindings
                (email_id,account,message_id,label_id,label_name,initial_inbox,initial_unread)
                VALUES(?, 'owner@example.test', ?, '', '', 1, ?)""",
                (email_id, f"m{index}", int(unread)))
        return self.agent.ingest(Email(email_id, "sender@example.test", "Project update",
                                       "Project update received. No action needed."))

    def test_confirmed_label_is_separate_and_written_only_after_readback(self):
        action = self.ingest(1)
        action_id = action["id"]
        decision = public_independent(self.agent.db, action_id)
        self.assertEqual(decision["status"], "awaiting_confirmation")
        self.assertEqual(self.agent.snapshot()["labels"], [])
        self.assertEqual(self.agent.db.execute("SELECT status FROM archive_decisions WHERE action_id=?",
                                               (action_id,)).fetchone()[0], "awaiting_confirmation")
        before = self.agent.get(action_id)
        decide_independent(self.agent, action_id, decision["revision"], "confirm", "AI: Work Update")
        self.assertEqual(self.agent.get(action_id)["status"], before["status"])
        self.assertEqual(self.agent.get(action_id)["revision"], before["revision"])
        self.assertEqual(self.agent.snapshot()["labels"], [])
        operation = self.agent.db.execute("SELECT id FROM gmail_operations WHERE action_id=? AND operation='label-independent'",
                                          (action_id,)).fetchone()[0]
        executor = VerifiedExecutor()
        self.assertTrue(run_one(self.path, executor, operation))
        self.assertEqual(executor.calls, [("label", ["AI: Work Update"], False)])
        self.assertEqual(public_independent(self.agent.db, action_id)["status"], "confirmed")
        self.assertEqual([row["label"] for row in self.agent.snapshot()["labels"]], ["AI: Work Update"])
        self.assertEqual(self.agent.db.execute("SELECT status FROM archive_decisions WHERE action_id=?",
                                               (action_id,)).fetchone()[0], "awaiting_confirmation")
        self.assertEqual(self.agent.db.execute("SELECT count(*) FROM label_rules").fetchone()[0], 0)

    def test_skip_closes_only_label_stream_and_old_read_mail_is_not_enrolled(self):
        action = self.ingest(2)
        decide_independent(self.agent, action["id"], 1, "skip")
        self.assertEqual(public_independent(self.agent.db, action["id"])["status"], "skipped")
        self.assertEqual(self.agent.db.execute("SELECT count(*) FROM gmail_operations WHERE action_id=?",
                                               (action["id"],)).fetchone()[0], 0)
        old = self.ingest(3, unread=False)
        self.assertIsNone(public_independent(self.agent.db, old["id"]))

    def test_uncertain_write_is_only_reconciled_read_only(self):
        action = self.ingest(4)
        decide_independent(self.agent, action["id"], 1, "confirm")
        operation = self.agent.db.execute("SELECT id FROM gmail_operations WHERE action_id=? AND operation='label-independent'",
                                          (action["id"],)).fetchone()[0]
        uncertain = VerifiedExecutor(False)
        run_one(self.path, uncertain, operation)
        self.assertEqual(public_independent(self.agent.db, action["id"])["status"], "unknown")
        self.assertEqual(self.agent.snapshot()["labels"], [])
        checked = VerifiedExecutor(True)
        run_one(self.path, checked, operation, check_only=True)
        self.assertEqual(checked.calls, [("label", ["AI: Work Update"], True)])
        self.assertEqual(public_independent(self.agent.db, action["id"])["status"], "confirmed")

    def test_reply_archive_label_and_event_are_four_separate_decisions(self):
        meeting = "Project meeting on September 18 at 15:00 Europe/Moscow"
        self.agent.proposer = Fixed(replace(
            self.proposal, action="send", text="Received, thank you.",
            recipient="sender@example.test", archive_evidence=meeting,
            event_change="create", event_kind="calendar_event",
            event_semantic_kind="project_meeting", event_title="Project meeting",
            event_original_text=meeting, event_evidence=meeting,
            event_start="2026-09-18T15:00:00+03:00", event_timezone="Europe/Moscow",
            event_confidence="clear",
        ))
        email_id = "gmail:owner@example.test:m5"
        with self.agent.db:
            self.agent.db.execute("""INSERT INTO gmail_bindings
                (email_id,account,message_id,label_id,label_name,initial_inbox,initial_unread)
                VALUES(?, 'owner@example.test', 'm5', '', '', 1, 1)""", (email_id,))
        row = self.agent.ingest(Email(email_id, "sender@example.test", "Project meeting",
                                      meeting + ". Please confirm receipt."))
        action_id = row["id"]
        self.assertEqual(row["status"], "executing")  # Gmail draft save, never send.
        self.assertEqual(public_independent(self.agent.db, action_id)["status"], "awaiting_confirmation")
        self.assertEqual(self.agent.db.execute("SELECT status FROM archive_decisions WHERE action_id=?",
                                               (action_id,)).fetchone()[0], "awaiting_confirmation")
        self.assertEqual(self.agent.db.execute("SELECT status FROM event_proposals WHERE source_email_id=?",
                                               (email_id,)).fetchone()[0], "awaiting_confirmation")
        self.assertEqual(self.agent.db.execute("SELECT count(*) FROM sent WHERE action_id=?",
                                               (action_id,)).fetchone()[0], 0)

    def test_primary_reanalysis_keeps_an_existing_label_decision(self):
        action = self.ingest(6)
        decide_independent(self.agent, action["id"], 1, "skip")
        with self.agent.db:
            self.agent.db.execute("UPDATE actions SET status='error' WHERE id=?", (action["id"],))
        email = self.agent.email_for(action["id"])
        self.agent.ingest(email, retry_error=True)
        self.assertEqual(public_independent(self.agent.db, action["id"])["status"], "skipped")
        self.assertEqual(self.agent.db.execute("SELECT count(*) FROM independent_label_decisions WHERE action_id=?",
                                               (action["id"],)).fetchone()[0], 1)

    def test_two_labels_can_be_confirmed_together(self):
        action = self.ingest(7)
        decide_independent(self.agent, action["id"], 1, "confirm", "AI: Work Update", "AI: Action")
        operation = self.agent.db.execute("SELECT id FROM gmail_operations WHERE action_id=? AND operation='label-independent'",
                                          (action["id"],)).fetchone()[0]
        executor = VerifiedExecutor()
        run_one(self.path, executor, operation)
        self.assertEqual(executor.calls, [("label", ["AI: Work Update", "AI: Action"], False)])
        self.assertEqual({r["label"] for r in self.agent.snapshot()["labels"]},
                         {"AI: Action", "AI: Work Update"})

    def test_restart_marks_only_interrupted_label_stream_uncertain(self):
        action = self.ingest(8)
        decide_independent(self.agent, action["id"], 1, "confirm")
        before = self.agent.get(action["id"])["status"]
        with self.agent.db:
            self.agent.db.execute("""UPDATE gmail_operations SET status='processing'
                WHERE action_id=? AND operation='label-independent'""", (action["id"],))
        recover(self.path)
        self.assertEqual(public_independent(self.agent.db, action["id"])["status"], "unknown")
        self.assertEqual(self.agent.get(action["id"])["status"], before)


if __name__ == "__main__":
    unittest.main()
