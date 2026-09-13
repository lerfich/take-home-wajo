import tempfile
import unittest
from pathlib import Path

from mail_agent import archive_skills
from mail_agent.core import Agent, Email, Proposal
from mail_agent.gmail_executor import run_one
from mail_agent.web import Application


class Fixed:
    def __init__(self, proposal):
        self.proposal = proposal

    def propose(self, email):
        return self.proposal


class VerifiedExecutor:
    def __init__(self):
        self.calls = []

    def apply(self, binding, operation, label="", check_only=False):
        self.calls.append((binding["message_id"], operation, check_only))
        return {"verified": True, "already_present": False}


class ArchiveSkillTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "archive.sqlite3"
        self.app = Application(self.path)
        self.executor = VerifiedExecutor()

    def tearDown(self):
        self.directory.cleanup()

    @staticmethod
    def proposal(result="keep", *, risky=False, evidence="Routine digest delivered"):
        return Proposal(
            "none", "Routine newsletter", pattern="periodic_digest",
            label_kind="newsletter", pattern_evidence=evidence,
            archive_recommendation=result, archive_reason=f"Recommend {result} for this digest.",
            archive_evidence=evidence, requires_action=False, has_deadline=risky,
            significant_change=False, sensitive=False,
        )

    def ingest_live(self, index, proposal=None):
        proposal = proposal or self.proposal()
        evidence = proposal.archive_evidence
        email = Email(
            f"gmail:owner@example.test:m{index}", "digest@example.test",
            f"Weekly digest {index}", f"Hello\n{evidence}\nIssue {index}",
        )
        binding = {
            "account": "owner@example.test", "message_id": f"m{index}",
            "label_id": "", "label_name": "", "initial_inbox": 1,
        }
        self.app.enqueue({"sender": email.sender, "subject": email.subject, "body": email.body},
                         email.id, binding)
        agent = Agent(self.path, Fixed(proposal))
        try:
            return agent.ingest(email)
        finally:
            agent.close()

    def decide(self, action, choice):
        agent = Agent(self.path, None)
        try:
            with agent.db:
                return archive_skills.decide(agent, action["id"], 1, choice)
        finally:
            agent.close()

    def skills(self):
        agent = Agent(self.path, None)
        try:
            return archive_skills.list_skills(agent.db, "owner@example.test")
        finally:
            agent.close()

    def test_keep_is_independent_and_three_real_agreements_auto_activate(self):
        for index in range(1, 4):
            action = self.ingest_live(index)
            self.assertEqual(action["proposal"]["action"], "none")
            decision = self.decide(action, "keep")
            self.assertEqual(decision["status"], "confirmed")
            self.assertEqual(len(self.skills()), 1 if index == 3 else 0)
        future = self.ingest_live(4)
        agent = Agent(self.path, None)
        try:
            decision = archive_skills.public(agent.db, future["id"])
        finally:
            agent.close()
        self.assertTrue(decision["automatic"])
        self.assertEqual((decision["chosen"], decision["status"]), ("keep", "automatic"))

    def test_local_simulation_and_risky_feedback_never_qualify(self):
        local = Agent(":memory:", Fixed(self.proposal()))
        try:
            for index in range(3):
                action = local.ingest(Email(str(index), "digest@example.test", "Digest",
                                           "Routine digest delivered"))
                with local.db:
                    archive_skills.decide(local, action["id"], 1, "keep")
            self.assertEqual(archive_skills.list_skills(local.db), [])
        finally:
            local.close()
        for index in range(10, 13):
            action = self.ingest_live(index, self.proposal(risky=True))
            self.decide(action, "keep")
        self.assertEqual(self.skills(), [])

    def test_archive_qualifies_executes_and_bad_automatic_choice_revokes_skill(self):
        proposal = self.proposal("archive")
        for index in range(20, 23):
            action = self.ingest_live(index, proposal)
            decision = self.decide(action, "archive")
            self.assertEqual(decision["status"], "executing")
            self.assertTrue(run_one(self.path, self.executor))
        skill = self.skills()[0]
        self.assertEqual((skill["status"], skill["result"]), ("active", "archive"))

        future = self.ingest_live(23, proposal)
        self.assertTrue(run_one(self.path, self.executor))
        agent = Agent(self.path, None)
        try:
            automatic = archive_skills.public(agent.db, future["id"])
            self.assertEqual((automatic["chosen"], automatic["status"]), ("archive", "automatic"))
            with agent.db:
                correction = archive_skills.correct_automatic_archive(agent, future["id"])
            self.assertEqual(correction["status"], "correcting")
        finally:
            agent.close()
        self.assertTrue(run_one(self.path, self.executor))
        self.assertEqual(self.skills(), [])
        agent = Agent(self.path, None)
        try:
            corrected = archive_skills.public(agent.db, future["id"])
            self.assertEqual(corrected["status"], "corrected")
            self.assertFalse(agent.db.execute(
                "SELECT archived FROM emails WHERE id=?", (future["email_id"],)
            ).fetchone()[0])
        finally:
            agent.close()
        again = self.ingest_live(24, proposal)
        agent = Agent(self.path, None)
        try:
            self.assertEqual(
                archive_skills.public(agent.db, again["id"])["status"],
                "awaiting_confirmation",
            )
        finally:
            agent.close()

    def test_paused_skill_is_visible_but_does_not_apply(self):
        for index in range(30, 33):
            self.decide(self.ingest_live(index), "keep")
        skill = self.skills()[0]
        agent = Agent(self.path, None)
        try:
            with agent.db:
                archive_skills.manage(
                    agent.db, skill["id"], skill["revision"], "pause", "owner@example.test"
                )
        finally:
            agent.close()
        paused = self.skills()[0]
        self.assertEqual(paused["status"], "paused")
        action = self.ingest_live(33)
        agent = Agent(self.path, None)
        try:
            self.assertEqual(
                archive_skills.public(agent.db, action["id"])["status"],
                "awaiting_confirmation",
            )
        finally:
            agent.close()

    def test_deleting_active_skill_resets_matching_training_history(self):
        for index in range(50, 53):
            self.decide(self.ingest_live(index), "keep")
        skill = self.skills()[0]
        agent = Agent(self.path, None)
        try:
            with agent.db:
                archive_skills.manage(
                    agent.db, skill["id"], skill["revision"], "delete", "owner@example.test"
                )
            self.assertEqual(agent.db.execute(
                "SELECT count(*) FROM archive_skill_feedback"
            ).fetchone()[0], 0)
        finally:
            agent.close()
        self.assertEqual(self.skills(), [])
        self.decide(self.ingest_live(53), "keep")
        self.assertEqual(self.skills(), [])

    def test_bad_automatic_keep_can_be_changed_to_archive_and_restarts_learning(self):
        for index in range(60, 63):
            self.decide(self.ingest_live(index), "keep")
        future = self.ingest_live(63)
        agent = Agent(self.path, None)
        try:
            with agent.db:
                corrected = archive_skills.correct_automatic_archive(agent, future["id"])
            self.assertEqual((corrected["chosen"], corrected["status"]), ("archive", "correcting"))
        finally:
            agent.close()
        self.assertTrue(run_one(self.path, self.executor))
        agent = Agent(self.path, None)
        try:
            corrected = archive_skills.public(agent.db, future["id"])
            self.assertEqual(corrected["status"], "corrected")
            self.assertTrue(agent.db.execute(
                "SELECT archived FROM emails WHERE id=?", (future["email_id"],)
            ).fetchone()[0])
        finally:
            agent.close()
        self.assertEqual(self.skills(), [])

    def test_archive_evidence_allows_only_whitespace_differences(self):
        good = self.proposal(evidence="Routine digest\n delivered")
        self.ingest_live(40, good)
        bad = self.proposal(evidence="Invented evidence")
        email = Email("bad-evidence", "digest@example.test", "Digest", "Actual email text")
        agent = Agent(self.path, Fixed(bad))
        try:
            action = agent.ingest(email)
            self.assertEqual(action["status"], "error")
            self.assertIsNone(archive_skills.public(agent.db, action["id"]))
        finally:
            agent.close()


if __name__ == "__main__":
    unittest.main()
