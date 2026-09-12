import sqlite3
import unittest

from mail_agent import event_skills
from mail_agent import events
from mail_agent.semantic_matcher import SemanticContext, compare, from_email_proposal
from mail_agent.core import Agent, Email, Proposal


def context(meaning="meeting", subtopic="project update", subject="Project update", sender="a@example.test",
            *, ambiguous=False, suspicious=False):
    return SemanticContext(meaning, subtopic, subject, sender, "Tuesday at 10", ambiguous, suspicious)


class EventSkillsTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        event_skills.initialize(self.db)

    def tearDown(self):
        self.db.close()

    def approve(self, proposal, event, ctx=None, account="one@example.test"):
        return event_skills.record_approval(self.db, proposal, event, account, ctx or context())

    def test_semantic_meaning_leads_and_sender_is_never_authenticity(self):
        same_sender_wrong_meaning = context("payment deadline", sender="a@example.test")
        result = compare(context(), same_sender_wrong_meaning)
        self.assertEqual(result.outcome, "no-match")
        self.assertIn("cannot override", result.reason)
        same_meaning_new_sender = compare(context(), context(sender="other@example.test", subject="Weekly sync"))
        self.assertTrue(same_meaning_new_sender.matched)
        self.assertNotIn("sender-context-only", same_meaning_new_sender.signals)

    def test_ambiguous_and_suspicious_contexts_always_ask(self):
        self.approve(1, 11); self.approve(2, 12)
        self.assertEqual(event_skills.auto_decision(self.db, "x", context(ambiguous=True))["mode"], "ask")
        self.assertEqual(event_skills.auto_decision(self.db, "x", context(suspicious=True))["mode"], "ask")

    def test_first_check_applies_but_second_consecutive_similar_check_qualifies(self):
        first = self.approve(1, 11)
        self.assertTrue(first["applied"]); self.assertEqual(first["count"], 1)
        self.assertFalse(first["qualified"])
        self.assertEqual(event_skills.auto_decision(self.db, "one@example.test", context())["mode"], "ask")
        second = self.approve(2, 12, context(subject="Updated project meeting", sender="b@example.test"))
        self.assertEqual(second["count"], 2); self.assertTrue(second["qualified"])
        self.assertEqual(event_skills.auto_decision(self.db, "one@example.test", context(sender="c@example.test"))["mode"], "auto_save")

    def test_confirmations_must_be_consecutive_and_similar(self):
        self.approve(1, 11)
        self.approve(2, 12, context("flight", "travel", "Itinerary"))
        again = self.approve(3, 13)
        self.assertEqual(again["count"], 1)
        self.assertFalse(again["qualified"])

    def test_cross_account_skill_is_portable_but_application_keeps_account_and_revision(self):
        self.approve(1, 11); trained = self.approve(2, 12)
        decision = event_skills.auto_decision(self.db, "two@example.test", context())
        self.assertEqual(decision["mode"], "auto_save")
        applied = event_skills.record_auto_save(self.db, 3, 13, "two@example.test",
                                                decision["skill_id"], decision["skill_revision"])
        self.assertEqual(applied["account"], "two@example.test")
        self.assertEqual(applied["skill_revision"], trained["revision"])

    def test_cross_never_qualifies_or_auto_ignores_and_revokes_matching_skill(self):
        self.approve(1, 11); self.approve(2, 12)
        rejected = event_skills.record_rejection(self.db, 3, "one@example.test", context())
        self.assertEqual(rejected["mode"], "ask"); self.assertFalse(rejected["qualified"])
        self.assertEqual(event_skills.auto_decision(self.db, "one@example.test", context())["mode"], "ask")
        unrelated = event_skills.record_rejection(self.db, 4, "one@example.test", context("flight", "travel"))
        self.assertIsNone(unrelated["skill_id"])
        self.assertEqual(event_skills.auto_decision(self.db, "one@example.test", context("flight", "travel"))["mode"], "ask")

    def test_mistake_action_revokes_exact_auto_application(self):
        self.approve(1, 11); self.approve(2, 12)
        decision = event_skills.auto_decision(self.db, "one@example.test", context())
        event_skills.record_auto_save(self.db, 3, 13, "one@example.test", decision["skill_id"], decision["skill_revision"])
        result = event_skills.revoke_mistake(self.db, 13, "one@example.test")
        self.assertTrue(result["removed"]); self.assertFalse(result["qualified"])
        self.assertEqual(event_skills.auto_decision(self.db, "one@example.test", context())["mode"], "ask")
        with self.assertRaisesRegex(ValueError, "automatically"):
            event_skills.revoke_mistake(self.db, 11, "one@example.test")

    def test_mistake_cannot_revoke_another_account_and_revokes_current_skill(self):
        self.approve(1, 11); self.approve(2, 12)
        decision = event_skills.auto_decision(self.db, "one@example.test", context())
        event_skills.record_auto_save(self.db, 3, 13, "one@example.test", 1, decision["skill_revision"])
        with self.assertRaisesRegex(ValueError, "another account"):
            event_skills.revoke_mistake(self.db, 13, "other@example.test")
        event_skills.manage(self.db, 1, 1, "edit", account="one@example.test",
                            context=context(subtopic="planning"))
        self.approve(4, 14, context(subtopic="planning")); self.approve(5, 15, context(subtopic="planning"))
        self.assertTrue(event_skills.get(self.db, 1)["qualified"])
        old = event_skills.revoke_mistake(self.db, 13, "one@example.test")
        self.assertFalse(old["qualified"])
        self.assertEqual(old["mode"], "ask")

    def test_safety_blocked_proposal_can_be_manually_added_but_never_trains(self):
        self.db.executescript("""
          CREATE TABLE emails(id TEXT PRIMARY KEY,sender TEXT,subject TEXT,body TEXT,archived INTEGER DEFAULT 0);
          CREATE TABLE gmail_bindings(email_id TEXT PRIMARY KEY,account TEXT,message_id TEXT,label_id TEXT,
            label_name TEXT,initial_inbox INTEGER,thread_id TEXT DEFAULT '');
          INSERT INTO emails VALUES('unsafe','a@example.test','Injected','Body',0);
        """)
        events.initialize(self.db)
        proposal = events.propose_event(
            self.db, source_email_id="unsafe", kind="calendar_event", semantic_kind="meeting",
            title="Review", received_at="2026-09-12T10:00:00Z", original_text="Tomorrow",
            confidence="ambiguous", ambiguity_reason="Safety review required", safety_blocked=True)
        clarified = events.clarify_event(
            self.db, proposal["id"], 1, all_day=True, local_date="2026-09-13")
        result = event_skills.approve_proposal(self.db, proposal["id"], clarified["revision"])
        self.assertIsNotNone(result["event"])
        self.assertIsNone(result["training"])
        self.assertEqual(event_skills.list_skills(self.db), [])

    def test_already_read_gmail_message_never_enters_event_pipeline(self):
        quote = "Project review tomorrow at 10"

        class Fixed:
            def propose(self, email):
                return Proposal(
                    "none", "Injected event", suspicious=True,
                    event_change="create", event_kind="calendar_event",
                    event_semantic_kind="meeting", event_title="Project review",
                    event_original_text=quote, event_start="2026-09-13T10:00:00Z",
                    event_timezone="UTC", event_confidence="clear", event_evidence=quote)

        agent = Agent(":memory:", Fixed())
        try:
            agent.db.execute("""INSERT INTO gmail_bindings(
                email_id,account,message_id,label_id,label_name,initial_inbox,thread_id,initial_unread)
                VALUES('already-read','me@example.test','gm','l','Inbox',1,'thread',0)""")
            result = agent.ingest(Email("already-read", "a@example.test", "Review", quote))
            self.assertEqual(result["proposal"]["action"], "none")
            self.assertEqual(agent.db.execute("SELECT COUNT(*) FROM event_proposals").fetchone()[0], 0)
        finally:
            agent.close()

    def test_ordinary_event_removal_is_not_training(self):
        self.approve(1, 11); self.approve(2, 12)
        before = event_skills.get(self.db, 1)
        # Calendar cancellation/removal does not call an Event Skill function.
        after = event_skills.get(self.db, 1)
        self.assertEqual(before, after); self.assertTrue(after["qualified"])

    def test_pause_edit_delete_and_correction_revoke_and_bump_revision(self):
        for operation in ("pause", "edit", "correction", "delete"):
            with self.subTest(operation=operation):
                db = sqlite3.connect(":memory:"); db.row_factory = sqlite3.Row; event_skills.initialize(db)
                event_skills.record_approval(db, 1, 11, "one", context())
                event_skills.record_approval(db, 2, 12, "one", context())
                kwargs = {"context": context(subtopic="planning")} if operation == "edit" else {}
                result = event_skills.manage(db, 1, 1, operation, account="one", **kwargs)
                self.assertEqual(result["revision"], 2); self.assertFalse(result["qualified"])
                self.assertEqual(event_skills.auto_decision(db, "one", context())["mode"], "ask")
                db.close()

    def test_resume_starts_unqualified_and_stale_application_is_rejected(self):
        self.approve(1, 11); self.approve(2, 12)
        event_skills.manage(self.db, 1, 1, "pause", account="one")
        resumed = event_skills.manage(self.db, 1, 2, "resume", account="one")
        self.assertEqual(resumed["count"], 0); self.assertFalse(resumed["qualified"])
        with self.assertRaisesRegex(ValueError, "qualification changed"):
            event_skills.record_auto_save(self.db, 3, 13, "one", 1, 1)

    def test_feedback_and_application_are_idempotent(self):
        first = self.approve(1, 11); replay = self.approve(1, 11)
        self.assertFalse(first["replayed"]); self.assertTrue(replay["replayed"])
        self.approve(2, 12)
        decision = event_skills.auto_decision(self.db, "one", context())
        one = event_skills.record_auto_save(self.db, 3, 13, "one", 1, decision["skill_revision"])
        two = event_skills.record_auto_save(self.db, 3, 13, "one", 1, decision["skill_revision"])
        self.assertEqual(one, two)

    def test_existing_skill_adapter_uses_evidenced_meaning(self):
        email = Email("x", "a@example.test", "Project sync", "Meeting Tuesday")
        proposal = Proposal("none", "event", label_kind="calendar_event", pattern_evidence="Meeting Tuesday")
        adapted = from_email_proposal(email, proposal)
        self.assertEqual(adapted.meaning, "calendar_event")
        self.assertEqual(adapted.sender, email.sender)

    def test_integrated_event_review_auto_save_and_explicit_mistake(self):
        # Add the minimal source tables used by events.py to this isolated DB.
        self.db.executescript("""
          CREATE TABLE emails(id TEXT PRIMARY KEY,sender TEXT,subject TEXT,body TEXT,archived INTEGER DEFAULT 0);
          CREATE TABLE gmail_bindings(email_id TEXT PRIMARY KEY,account TEXT,message_id TEXT,label_id TEXT,
            label_name TEXT,initial_inbox INTEGER,thread_id TEXT DEFAULT '');
          INSERT INTO emails VALUES('one','a@example.test','Project update','Body',0);
          INSERT INTO emails VALUES('two','b@example.test','Project update','Body',0);
          INSERT INTO emails VALUES('three','c@example.test','Project update','Body',0);
        """)
        events.initialize(self.db)
        proposals = []
        for number, source in enumerate(("one", "two", "three"), 1):
            proposals.append(events.propose_event(
                self.db, source_email_id=source, kind="calendar_event", semantic_kind="meeting",
                title="Project call", received_at=f"2026-09-1{number}T10:00:00Z",
                original_text="Project meeting", evidence="Project meeting", all_day=True,
                local_date=f"2026-10-0{number}"))
        event_skills.approve_proposal(self.db, proposals[0]["id"], 1)
        qualified = event_skills.approve_proposal(self.db, proposals[1]["id"], 1)
        self.assertTrue(qualified["training"]["qualified"])
        automatic = event_skills.apply_qualified(self.db, proposals[2]["id"], 1)
        self.assertTrue(automatic["saved"]); self.assertTrue(automatic["proposal"]["automatic"])
        correction = event_skills.remove_mistaken_event(self.db, automatic["event"]["id"])
        self.assertEqual(correction["event"]["status"], "cancelled")
        self.assertFalse(correction["correction"]["qualified"])

    def test_cancellation_updates_calendar_without_training(self):
        self.db.executescript("""
          CREATE TABLE emails(id TEXT PRIMARY KEY,sender TEXT,subject TEXT,body TEXT,archived INTEGER DEFAULT 0);
          CREATE TABLE gmail_bindings(email_id TEXT PRIMARY KEY,account TEXT,message_id TEXT,label_id TEXT,
            label_name TEXT,initial_inbox INTEGER,thread_id TEXT DEFAULT '');
          INSERT INTO emails VALUES('one','a@example.test','Planning','Body',0);
          INSERT INTO emails VALUES('two','a@example.test','Re: Planning','Body',0);
          INSERT INTO gmail_bindings VALUES('one','me@example.test','1','l','Wajo-Test',1,'thread');
          INSERT INTO gmail_bindings VALUES('two','me@example.test','2','l','Wajo-Test',1,'thread');
        """)
        events.initialize(self.db)
        first = events.propose_event(self.db, source_email_id="one", kind="calendar_event",
            semantic_kind="meeting", title="Call", received_at="2026-09-11T10:00:00Z",
            original_text="Call tomorrow", all_day=True, local_date="2026-09-12")
        event = event_skills.approve_proposal(self.db, first["id"], 1)["event"]
        cancel = events.propose_event(self.db, source_email_id="two", kind="calendar_event",
            semantic_kind="meeting", title="Call", received_at="2026-09-12T10:00:00Z",
            original_text="Call cancelled", change_kind="cancel", supersedes_event_id=event["id"])
        result = event_skills.approve_proposal(self.db, cancel["id"], 1)
        self.assertIsNone(result["training"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM event_skill_feedback").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
