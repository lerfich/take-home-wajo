from dataclasses import replace
import tempfile
from pathlib import Path
import unittest

from mail_agent.core import Agent, Email, Proposal, PATTERNS
from test_core import Fixed


def candidate(pattern="acknowledgement_only"):
    return Proposal("archive", "Routine information", pattern=pattern, requires_action=False,
                    has_deadline=False, significant_change=False, sensitive=False,
                    pattern_evidence="Received, no action needed.")


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.agent = Agent(":memory:", Fixed(candidate()))

    def tearDown(self):
        self.agent.close()

    def ingest(self, key, sender=None):
        return self.agent.ingest(Email(key, sender or f"{key}@example.test", "Update", "Received, no action needed."))

    def train(self, scope="general", sender=None):
        for i in range(3):
            row = self.ingest(f"training-{i}", sender)
            self.assertEqual(row["status"], "pending")
            self.agent.approve(row["id"], 1, scope)

    def test_transfer_across_senders_and_recorded_basis(self):
        self.train()
        row = self.ingest("new-company")
        self.assertEqual((row["status"], row["autonomy"]), ("executed", "notify"))
        state = self.agent.snapshot()
        self.assertEqual(len(state["preference_feedback"]), 3)  # auto action is not approval
        self.assertEqual(len([r for r in state["audit"] if r["event"] == "learned_permission"]), 1)

    def test_pattern_is_separate_and_no_response_is_not_approval(self):
        self.train()
        self.agent.proposer = Fixed(candidate("routine_success"))
        for i in range(4):
            self.assertEqual(self.ingest(f"success-{i}")["status"], "pending")
        self.assertEqual(len(self.agent.snapshot()["preference_feedback"]), 3)

    def test_sender_learning_does_not_transfer(self):
        self.train("sender", "known@example.test")
        self.assertEqual(self.ingest("known", "known@example.test")["status"], "executed")
        self.assertEqual(self.ingest("other")["status"], "pending")

    def test_sender_correction_overrides_general_but_preserves_other_senders(self):
        self.train()
        row = self.ingest("one", "one@example.test")
        self.agent.correct_archive(row["id"], "sender")
        self.assertEqual(self.agent.snapshot()["emails"][-1]["archived"], 0)
        self.assertEqual(self.ingest("same", "one@example.test")["status"], "pending")
        self.assertEqual(self.ingest("other")["status"], "executed")
        with self.assertRaises(ValueError):
            self.agent.correct_archive(row["id"], "sender")

    def test_general_correction_resets_and_requires_three_new_approvals(self):
        self.train()
        row = self.ingest("wrong")
        self.agent.correct_archive(row["id"])
        for i in range(3):
            row = self.ingest(f"retrain-{i}")
            self.assertEqual(row["status"], "pending")
            self.agent.approve(row["id"], 1)
        self.assertEqual(self.ingest("relearned")["status"], "executed")

    def test_explicit_exception_overrides_memory_and_pending_approval(self):
        row = self.ingest("pending", "special@example.test")
        self.agent.set_archive_rule("special@example.test")
        with self.assertRaises(ValueError):
            self.agent.approve(row["id"], 1)
        self.train()
        self.assertEqual(self.ingest("special", "special@example.test")["status"], "skipped")
        self.assertEqual(self.ingest("other")["status"], "executed")
        self.agent.set_archive_rule("special@example.test", keep=False)
        self.assertEqual(self.ingest("restored", "special@example.test")["status"], "executed")

    def test_risk_flags_and_missing_evidence_prevent_transfer(self):
        self.train()
        changes = [{field: True} for field in ("requires_action", "has_deadline", "significant_change", "sensitive", "notify", "suspicious", "needs_human")]
        changes += [{"pattern_evidence": "Invented quote"}, {"pattern": "unknown"}]
        for i, change in enumerate(changes):
            self.agent.proposer = Fixed(replace(candidate(), **change))
            row = self.ingest(f"risk-{i}")
            self.assertNotEqual(row["status"], "executed")
        self.assertEqual(len(self.agent.snapshot()["preference_feedback"]), 3)

    def test_learning_never_authorizes_sending_or_unsupported_operations(self):
        self.train()
        for action in ("send", "pay", "delete", "shell"):
            self.agent.proposer = Fixed(replace(candidate(), action=action, text="Hello", recipient="a@example.test"))
            row = self.ingest(action)
            self.assertEqual(row["status"], "pending" if action == "send" else "blocked")
        self.assertEqual(self.agent.snapshot()["sent"], [])

    def test_repeated_event_and_approval_cannot_duplicate_experience(self):
        row = self.ingest("one")
        self.agent.approve(row["id"], 1)
        self.ingest("one")
        with self.assertRaises(ValueError):
            self.agent.approve(row["id"], 1)
        self.assertEqual(len(self.agent.snapshot()["preference_feedback"]), 1)

    def test_preferences_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            self.agent.close()
            path = str(Path(directory) / "state.sqlite3")
            self.agent = Agent(path, Fixed(candidate()))
            self.train()
            self.agent.set_archive_rule("special@example.test")
            self.agent.close()
            self.agent = Agent(path, Fixed(candidate()))
            self.assertEqual(self.ingest("new")["status"], "executed")
            self.assertEqual(self.ingest("special", "special@example.test")["status"], "skipped")

    def test_all_semantic_patterns_support_transfer(self):
        for pattern in sorted(PATTERNS):
            with self.subTest(pattern=pattern):
                self.agent.proposer = Fixed(candidate(pattern))
                for i in range(3):
                    row = self.ingest(f"{pattern}-{i}")
                    self.assertEqual(row["status"], "pending")
                    self.agent.approve(row["id"], 1)
                self.assertEqual(self.ingest(f"{pattern}-new")["autonomy"], "notify")

    def test_rejection_prevents_older_approvals_from_counting(self):
        for i in range(2):
            row = self.ingest(f"yes-{i}")
            self.agent.approve(row["id"], 1)
        row = self.ingest("no")
        self.agent.reject(row["id"], 1)
        row = self.ingest("later")
        self.agent.approve(row["id"], 1)
        self.assertEqual(self.ingest("still-ask")["status"], "pending")


if __name__ == "__main__":
    unittest.main()
