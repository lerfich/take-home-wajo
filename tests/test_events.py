import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from mail_agent import events
from mail_agent.core import Agent
from mail_agent.demo import ScriptedProposer


class EventTests(unittest.TestCase):
    def setUp(self):
        self.agent = Agent(":memory:", ScriptedProposer())
        self.db = self.agent.db
        self.db.execute("INSERT INTO emails(id,sender,subject,body) VALUES('m1','a@example.test','Planning','Meet Friday')")
        self.db.execute("INSERT INTO emails(id,sender,subject,body) VALUES('m2','a@example.test','Re: Planning','Moved')")
        self.db.execute("INSERT INTO emails(id,sender,subject,body) VALUES('m3','a@example.test','Re: Planning','Cancelled')")
        self.db.execute("INSERT INTO gmail_bindings(email_id,account,message_id,label_id,label_name,initial_inbox,thread_id) VALUES('m1','me@example.test','gm1','l','Wajo-Test',1,'thread-1')")
        self.db.execute("INSERT INTO gmail_bindings(email_id,account,message_id,label_id,label_name,initial_inbox,thread_id) VALUES('m2','me@example.test','gm2','l','Wajo-Test',1,'thread-1')")
        self.db.execute("INSERT INTO gmail_bindings(email_id,account,message_id,label_id,label_name,initial_inbox,thread_id) VALUES('m3','me@example.test','gm3','l','Wajo-Test',1,'thread-1')")
        events.initialize(self.db)

    def tearDown(self):
        self.agent.close()

    def proposal(self, **changes):
        values = dict(source_email_id="m1", kind="calendar_event", title="Project call",
                      received_at="2026-09-12T10:00:00+03:00", original_text="Friday at 15:00",
                      semantic_kind="project_meeting", evidence="Friday at 15:00",
                      start_at="2026-09-18T15:00:00", source_timezone="Europe/Moscow")
        values.update(changes)
        return events.propose_event(self.db, **values)

    def test_timed_event_is_stored_utc_with_source_context(self):
        proposal = self.proposal()
        self.assertEqual(proposal["start_utc"], "2026-09-18T12:00:00+00:00")
        self.assertEqual(proposal["source_timezone"], "Europe/Moscow")
        self.assertEqual(proposal["received_at"], "2026-09-12T07:00:00+00:00")
        result = events.approve_event(self.db, proposal["id"], 1)
        self.assertEqual(result["event"]["original_text"], "Friday at 15:00")
        self.assertEqual(events.list_events(self.db, now=datetime(2026, 9, 12, tzinfo=timezone.utc),
                                            local_timezone="Europe/Moscow")[0]["local_start"],
                         "2026-09-18T15:00:00+03:00")

    def test_all_day_date_is_not_shifted(self):
        proposal = self.proposal(all_day=True, start_at="", source_timezone="",
                                 local_date="2027-01-02", original_text="on January 2")
        event = events.approve_event(self.db, proposal["id"], 1)["event"]
        self.assertEqual(event["local_date"], "2027-01-02")
        self.assertEqual(event["start_utc"], "")

    def test_ambiguous_value_requires_clarification_then_fresh_approval(self):
        proposal = self.proposal(confidence="ambiguous", ambiguity_reason="Which Friday?", start_at="")
        self.assertEqual(proposal["status"], "needs_clarification")
        with self.assertRaises(ValueError):
            events.approve_event(self.db, proposal["id"], 1)
        revised = events.clarify_event(self.db, proposal["id"], 1,
                                       start_at="2026-09-18T18:00:00", local_timezone="Europe/Moscow")
        self.assertEqual((revised["status"], revised["revision"]), ("awaiting_confirmation", 2))
        with self.assertRaises(ValueError):
            events.approve_event(self.db, proposal["id"], 1)
        self.assertIsNotNone(events.approve_event(self.db, proposal["id"], 2)["event"])
        bad_zone = self.proposal(candidate_key="bad-zone", source_timezone="Mars/Olympus")
        self.assertEqual(bad_zone["status"], "needs_clarification")
        self.assertIn("timezone", bad_zone["ambiguity_reason"])

    def test_rejection_is_independent_from_mail_action(self):
        proposal = self.proposal()
        rejected = events.reject_event(self.db, proposal["id"], 1)
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM actions").fetchone()[0], 0)

    def test_idempotency_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "events.sqlite3")
            first = Agent(path, ScriptedProposer())
            first.db.execute("INSERT INTO emails(id,sender,subject,body) VALUES('x','a@b.test','S','B')")
            kwargs = dict(source_email_id="x", kind="calendar_event", title="Day",
                          received_at="2026-09-12T00:00:00Z", original_text="January 2",
                          candidate_key="one", all_day=True, local_date="2027-01-02")
            p1 = events.propose_event(first.db, **kwargs)
            first.close()
            second = Agent(path, ScriptedProposer())
            p2 = events.propose_event(second.db, **kwargs)
            created = events.approve_event(second.db, p2["id"], 1)
            repeated = events.approve_event(second.db, p2["id"], 1)
            self.assertEqual(p1["id"], p2["id"])
            self.assertTrue(repeated["idempotent"])
            self.assertEqual(created["event"]["id"], repeated["event"]["id"])
            second.close()

    def test_later_message_reschedules_and_cancels_only_within_thread(self):
        first = events.approve_event(self.db, self.proposal()["id"], 1)["event"]
        with self.assertRaisesRegex(ValueError, "later source message"):
            events.propose_event(self.db, source_email_id="m1", kind="calendar_event",
                title="Project call", received_at="2026-09-13T10:00:00Z", original_text="Moved",
                candidate_key="not-later", start_at="2026-09-21T12:00:00Z", source_timezone="UTC",
                change_kind="reschedule", supersedes_event_id=first["id"])
        moved = events.propose_event(self.db, source_email_id="m2", kind="calendar_event",
            title="Project call", received_at="2026-09-13T10:00:00Z", original_text="Moved to Monday",
            start_at="2026-09-21T12:00:00Z", source_timezone="UTC", change_kind="reschedule",
            supersedes_event_id=first["id"])
        current = events.approve_event(self.db, moved["id"], 1)["event"]
        self.assertEqual(events.get_event(self.db, first["id"])["status"], "rescheduled")
        cancel = events.propose_event(self.db, source_email_id="m3", kind="calendar_event",
            title="Project call", received_at="2026-09-14T10:00:00Z", original_text="Cancelled",
            candidate_key="cancel", change_kind="cancel", supersedes_event_id=current["id"])
        self.assertIsNone(events.approve_event(self.db, cancel["id"], 1)["event"])
        self.assertEqual(events.list_events(self.db, now=datetime(2026, 9, 12, tzinfo=timezone.utc)), [])

    def test_source_thread_cannot_be_forged(self):
        with self.assertRaises(ValueError):
            self.proposal(source_thread_id="someone-elses-thread")

    def test_listing_window_and_mistake_removal(self):
        for key, day in (("old", "2025-09-11"), ("edge", "2025-09-12"), ("future", "2029-09-13")):
            p = self.proposal(candidate_key=key, all_day=True, start_at="", source_timezone="",
                              local_date=day, original_text=day)
            events.approve_event(self.db, p["id"], 1, automatic=(key == "edge"))
        listed = events.list_events(self.db, now=datetime(2026, 9, 12, tzinfo=timezone.utc))
        self.assertEqual([item["local_date"] for item in listed], ["2025-09-12"])
        removed = events.remove_mistaken_event(self.db, listed[0]["id"])
        self.assertEqual(removed["status"], "cancelled")
        self.assertEqual(events.get_proposal(self.db, removed["proposal_id"])["status"], "mistake_removed")


if __name__ == "__main__":
    unittest.main()
