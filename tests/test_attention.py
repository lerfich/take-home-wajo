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
            self.assertEqual(next_row['autonomy'],'ask')
            self.assertFalse(next_row['proposal']['notify'])
            self.assertIn('Attention preference',next_row['reason'])
            self.assertEqual(len(a.snapshot()['attention_items']),2)
            self.assertEqual(len(a.snapshot()['attention_feedback']),1)
            self.assertEqual(a.snapshot()['label_rules'],[])
            self.assertEqual(a.snapshot()['preference_feedback'],[])
        finally:a.close()

    def test_sender_scope_and_disable(self):
        p=Proposal('label','Notice',label='AI: Account',label_kind='account_notice',pattern_evidence='Notice')
        a=Agent(':memory:',Fixed(p))
        try:
            r=a.ingest(Email('1','a@example.test','Notice','Notice'));set_rule(a,r['id'],True,'sender')
            self.assertEqual(a.ingest(Email('2','b@example.test','Notice','Notice'))['autonomy'],'silent')
            matched=a.ingest(Email('3','a@example.test','Notice','Notice'))
            self.assertEqual(matched['autonomy'],'silent')
            self.assertTrue(any(row['action_id']==matched['id'] and not row['seen']
                                for row in a.snapshot()['attention_items']))
            set_rule(a,r['id'],False,'sender')
            self.assertEqual(a.ingest(Email('4','a@example.test','Notice','Notice'))['autonomy'],'silent')
            self.assertEqual([row['enabled'] for row in a.snapshot()['attention_feedback']],[1,0])
        finally:a.close()

    def test_attention_visibility_does_not_change_autonomy(self):
        p=Proposal('label','Notice',label='AI: Account',label_kind='account_notice',pattern_evidence='Notice')
        a=Agent(':memory:',Fixed(p))
        try:
            r=a.ingest(Email('1','a@example.test','Notice','Notice'));set_rule(a,r['id'],True,'similar')
            next_row=a.ingest(Email('2','b@example.test','Notice','Notice'))
            self.assertEqual((next_row['autonomy'],next_row['status']),('silent','executed'))
            self.assertFalse(next_row['proposal']['notify'])
            item=next(x for x in a.snapshot()['attention_items'] if x['action_id']==next_row['id'])
            self.assertFalse(item['seen'])
        finally:a.close()

    def test_future_attention_rule_requires_evidenced_kind(self):
        p=Proposal('label','Notice',label='AI: Account',label_kind='account_notice',pattern_evidence='not present')
        a=Agent(':memory:',Fixed(p))
        try:
            r=a.ingest(Email('1','a@example.test','Notice','Notice'))
            with self.assertRaisesRegex(ValueError,'evidenced situation'):
                set_rule(a,r['id'],True,'similar')
            self.assertEqual(a.snapshot()['attention_feedback'],[])
            self.assertTrue(set_rule(a,r['id'],True,'email')['saved'])
        finally:a.close()
