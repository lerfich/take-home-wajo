from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from mail_agent.core import Agent, Email, Proposal
from mail_agent.organization import current, pause, submit


class Fixed:
    def __init__(self, proposal):
        self.proposal = proposal

    def propose(self, email):
        return self.proposal


class OrganizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.proposal = Proposal(
            "label", "Application received", label="AI: Work",
            label_kind="job_application_receipt", pattern_evidence="Application received",
            requires_action=False, has_deadline=False, significant_change=False, sensitive=False)
        self.agent = Agent(self.path, Fixed(self.proposal))

    def tearDown(self):
        self.agent.close()
        self.temp.cleanup()

    def ingest(self, identifier, sender="sender@example.test", proposal=None):
        self.agent.proposer = Fixed(proposal or self.proposal)
        return self.agent.ingest(Email(str(identifier), sender, "Update", "Application received. No action needed."))

    def test_default_hierarchy_and_email_only_review(self):
        action = self.ingest(1)
        self.assertEqual(current(self.agent, action["id"])["topic"], "Applications")
        result = submit(self.agent, action["id"], "Career", "Received", True, "email")
        self.assertEqual((result["topic"], result["subtype"], result["important"]),
                         ("Career", "Received", True))
        other = self.ingest(2)
        self.assertEqual(current(self.agent, other["id"])["topic"], "Applications")
        self.assertEqual(self.agent.snapshot()["organization_rules"], [])

    def test_similar_rule_transfers_to_same_kind_but_not_contrast(self):
        first = self.ingest(1)
        submit(self.agent, first["id"], "Applications", "Receipts", False, "similar")
        matching = self.ingest(2, "other@example.test")
        self.assertEqual(current(self.agent, matching["id"])["subtype"], "Receipts")
        contrast = replace(self.proposal, label_kind="job_interview")
        different = self.ingest(3, proposal=contrast)
        self.assertEqual(current(self.agent, different["id"])["subtype"], "Interview")

    def test_sender_rule_overrides_general_and_can_be_paused(self):
        submit(self.agent, self.ingest(1)["id"], "Applications", "Receipt", False, "similar")
        submit(self.agent, self.ingest(2)["id"], "Hiring", "Priority receipt", True, "sender")
        matching = self.ingest(3)
        value = current(self.agent, matching["id"])
        self.assertEqual((value["topic"], value["important"]), ("Hiring", True))
        rule = next(r for r in self.agent.snapshot()["organization_rules"] if r["scope"] != "*")
        pause(self.agent, rule["id"])
        fallback = self.ingest(4)
        self.assertEqual((current(self.agent, fallback["id"])["topic"], current(self.agent, fallback["id"])["important"]),
                         ("Applications", False))

    def test_importance_never_changes_action_permission(self):
        archive = replace(self.proposal, action="archive", label="", pattern="acknowledgement_only")
        pending = self.ingest(1, proposal=archive)
        self.assertEqual(pending["status"], "pending")
        submit(self.agent, pending["id"], "Applications", "Receipt", True, "similar")
        second = self.ingest(2, proposal=archive)
        self.assertTrue(current(self.agent, second["id"])["important"])
        self.assertEqual(second["status"], "pending")

    def test_unknown_or_unevidenced_kind_cannot_create_future_rule(self):
        for index, proposal in enumerate((replace(self.proposal, label_kind="unknown"),
                                          replace(self.proposal, pattern_evidence="missing"))):
            action = self.ingest(index, proposal=proposal)
            with self.assertRaises(ValueError):
                submit(self.agent, action["id"], "Other", "Custom", False, "similar")
            submit(self.agent, action["id"], "Other", "Custom", False, "email")

    def test_input_validation(self):
        action = self.ingest(1)
        for topic, subtype, important, scope in (("", "x", False, "email"),
                                                  ("x", "\n", False, "email"),
                                                  ("x", "y", 1, "email"),
                                                  ("x", "y", False, "all")):
            with self.assertRaises(ValueError):
                submit(self.agent, action["id"], topic, subtype, important, scope)


if __name__ == "__main__":
    unittest.main()
