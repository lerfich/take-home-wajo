import unittest
from mail_agent.core import Agent, Email, Proposal
from mail_agent.attention import set_rule


class Fixed:
    def __init__(self,p):self.p=p
    def propose(self,email):return self.p


class AttentionTests(unittest.TestCase):
    def test_attention_is_separate_from_label_and_forces_archive_review(self):
        p=Proposal('label','Access changed',label='AI: Accounts',label_kind='account_notice',pattern_evidence='Changed')
        a=Agent(':memory:',Fixed(p))
        try:
            r=a.ingest(Email('1','a@example.test','Notice','Changed'))
            set_rule(a,r['id'],True,'similar')
            a.proposer=Fixed(Proposal('archive','Update',label_kind='account_notice',pattern='informational_reference',
                            pattern_evidence='Changed',requires_action=False,has_deadline=False,significant_change=False,sensitive=False))
            next_row=a.ingest(Email('2','b@example.test','Notice','Changed'))
            self.assertEqual(next_row['status'],'pending')
            self.assertTrue(next_row['proposal']['notify'])
            self.assertEqual(len(a.snapshot()['attention_items']),2)
            self.assertEqual(a.snapshot()['label_rules'],[])
            self.assertEqual(a.snapshot()['preference_feedback'],[])
        finally:a.close()

    def test_sender_scope_and_disable(self):
        p=Proposal('label','Notice',label='AI: Account',label_kind='account_notice',pattern_evidence='Notice')
        a=Agent(':memory:',Fixed(p))
        try:
            r=a.ingest(Email('1','a@example.test','Notice','Notice'));set_rule(a,r['id'],True,'sender')
            self.assertEqual(a.ingest(Email('2','b@example.test','Notice','Notice'))['autonomy'],'silent')
            self.assertEqual(a.ingest(Email('3','a@example.test','Notice','Notice'))['autonomy'],'notify')
            set_rule(a,r['id'],False,'sender')
            self.assertEqual(a.ingest(Email('4','a@example.test','Notice','Notice'))['autonomy'],'silent')
        finally:a.close()
