from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mail_agent.core import Agent, Email, Proposal
from mail_agent.draft_preferences import derive, preview, save, choose, signature_name


class Rewriter:
    def __init__(self, proposal, rewritten="Thanks, received.", fail=False):
        self.proposal = proposal
        self.rewritten = rewritten
        self.fail = fail
        self.styles = []

    def propose(self, email):
        return self.proposal

    def rewrite_draft(self, email, proposal, style):
        self.styles.append(dict(style))
        if self.fail:
            raise ValueError("rewrite failed")
        return self.rewritten


class DraftPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.proposal = Proposal("send", "Reply", text="Hello,\n\nThank you for the update. I confirm that I received it.\n\nBest,",
                                 recipient="sender@example.test", label_kind="support_response",
                                 pattern_evidence="The fix is ready")
        self.agent = Agent(self.path, Rewriter(self.proposal))

    def tearDown(self):
        self.agent.close()
        self.temp.cleanup()

    def edited_action(self, evidence=True):
        proposal = self.proposal if evidence else Proposal(
            "send", "Reply", text=self.proposal.text, recipient="sender@example.test",
            label_kind="support_response", pattern_evidence="missing")
        self.agent.proposer = Rewriter(proposal)
        action = self.agent.ingest(Email("train" + str(evidence), "sender@example.test", "Support update",
                                        "The fix is ready. Please try it and let us know."))
        self.agent.revise_send(action["id"], "Thanks, received.", "sender@example.test")
        return action["id"]

    def test_preview_derives_only_structural_style(self):
        style = derive(self.proposal.text, "Thanks, received.")
        self.assertEqual((style["length"], style["greeting"], style["signoff"]),
                         ("concise", "omit", "omit"))
        self.assertIn("concise", preview(self.agent, self.edited_action(), 2)["summary"])

    def test_named_signoff_is_preserved(self):
        for closing in ("Best,\nNikita", "Best regards,\nNikita Smith", "С уважением,\nНикита",
                        "Kind regards,\nNikita", "Warm regards, Nikita", "Many thanks, Nikita",
                        "Cheers,\nNikita", "С наилучшими пожеланиями,\nНикита"):
            with self.subTest(closing=closing):
                self.assertEqual(derive("Original", "Hi,\n\nReceived.\n\n" + closing)["signoff"], "include")
                self.assertIn(signature_name(closing), {"Nikita", "Nikita Smith", "Никита"})

    def test_signature_identity_is_account_bound(self):
        skill = {'id': 10, 'account': 'owner@example.test',
                 'config': {'example_after': 'Received.\n\nBest,\nNikita'}}
        email = Email('identity-test', 'other@example.test', 'Update', 'Routine')
        with patch('mail_agent.skills.choose', return_value=skill):
            with patch('mail_agent.draft_preferences.account_for', return_value=skill['account']):
                rule = choose(self.agent, self.proposal, email)
                self.assertEqual(rule['confirmed_signature_name'], 'Nikita')
                self.assertEqual(rule['reply_account'], skill['account'])
            with patch('mail_agent.draft_preferences.account_for', return_value='different@example.test'):
                self.assertNotIn('confirmed_signature_name', choose(self.agent, self.proposal, email))
        self.assertEqual(signature_name('Best,\n[User Name]'), '')

    def test_body_thanks_is_not_a_named_signoff(self):
        self.assertEqual(derive("Original", "Thanks,\nPlease review the updated document tomorrow.")["signoff"], "omit")
        for body in ("Thanks, please review the document.", "Regards, [User Name]",
                     "Best,\nPlease review", "Best,\n1234", "Best,\nexample@example.com"):
            with self.subTest(body=body):
                self.assertEqual(signature_name(body), "")

    def test_confirmed_style_transfers_without_send_permission(self):
        save(self.agent, self.edited_action(), 2, "similar")
        provider = Rewriter(self.proposal)
        self.agent.proposer = provider
        result = self.agent.ingest(Email("new", "other@example.test", "Another support update",
                                         "The fix is ready. Please check again."))
        self.assertEqual(result["proposal"]["text"], "Thanks, received.")
        self.assertEqual(result["status"], "pending")
        self.assertEqual(len(provider.styles), 1)
        self.assertEqual(provider.styles[0]["example_before"], self.proposal.text)
        self.assertEqual(provider.styles[0]["example_after"], "Thanks, received.")
        application = self.agent.snapshot()["draft_style_applications"][0]
        self.assertEqual(application["status"], "applied")
        self.assertEqual(self.agent.snapshot()["sent"], [])

    def test_sender_scope_does_not_transfer_to_other_sender(self):
        save(self.agent, self.edited_action(), 2, "sender")
        provider = Rewriter(self.proposal)
        self.agent.proposer = provider
        result = self.agent.ingest(Email("other", "other@example.test", "Update",
                                         "The fix is ready. Please check again."))
        self.assertEqual(result["proposal"]["text"], self.proposal.text)
        self.assertEqual(provider.styles, [])

    def test_rule_does_not_apply_without_evidence_in_the_new_email(self):
        save(self.agent, self.edited_action(), 2, "similar")
        proposal = Proposal("send", "Reply", text=self.proposal.text,
                            recipient="other@example.test", label_kind="support_response",
                            pattern_evidence="not present")
        provider = Rewriter(proposal)
        self.agent.proposer = provider
        result = self.agent.ingest(Email("unevidenced", "other@example.test", "Update",
                                         "Please check again."))
        self.assertEqual(result["proposal"]["text"], self.proposal.text)
        self.assertEqual(provider.styles, [])

    def test_missing_evidence_cannot_train_and_rewrite_failure_falls_back(self):
        with self.assertRaises(ValueError):
            save(self.agent, self.edited_action(False), 2, "similar")
        save(self.agent, self.edited_action(True), 2, "similar")
        provider = Rewriter(self.proposal, fail=True)
        self.agent.proposer = provider
        result = self.agent.ingest(Email("fallback", "other@example.test", "Update",
                                         "The fix is ready. Please check again."))
        self.assertEqual(result["proposal"]["text"], self.proposal.text)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(self.agent.snapshot()["draft_style_applications"][0]["status"], "fallback")


if __name__ == "__main__":
    unittest.main()
