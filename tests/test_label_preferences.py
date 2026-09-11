from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mail_agent.core import Agent, Email, Proposal
from mail_agent.label_preferences import submit, normalize_label
from mail_agent.label_provider import LabelProposer
from mail_agent.groq_provider import ProviderError


class Fixed:
    def __init__(self,p): self.p=p
    def propose(self,email): return self.p


class LabelLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'state.sqlite3'
        self.p=Proposal('label','Application received',label='AI: Work',label_kind='job_application_receipt',
                        pattern_evidence='Application received',requires_action=False,has_deadline=False,
                        significant_change=False,sensitive=False)
        self.agent=Agent(self.path,Fixed(self.p))

    def tearDown(self):
        self.agent.close();self.temp.cleanup()

    def ingest(self,i,sender='sender@example.test',p=None):
        self.agent.proposer=Fixed(p or self.p)
        return self.agent.ingest(Email(str(i),sender,'Application update','Application received. No action needed.'))

    def test_add_does_not_replace_and_rejects_future_scope(self):
        row=self.ingest(1)
        with self.assertRaises(ValueError):
            submit(self.agent,row['id'],1,'Follow up','similar',mode='add')
        submit(self.agent,row['id'],1,'Follow up','email',mode='add')
        labels={r['label'] for r in self.agent.snapshot()['labels']}
        self.assertEqual(labels,{'AI: Work','AI: Follow up'})
        submit(self.agent,row['id'],2,'Applications','email')
        labels={r['label'] for r in self.agent.snapshot()['labels']}
        self.assertEqual(labels,{'AI: Applications','AI: Follow up'})

    def test_single_email_change_does_not_train_or_change_other_actions(self):
        row=self.ingest(1)
        submit(self.agent,row['id'],1,'Job applications','email')
        self.assertEqual(self.agent.get(row['id'])['proposal']['label'],'AI: Job applications')
        self.assertEqual(self.ingest(2)['proposal']['label'],'AI: Work')
        self.assertEqual(self.agent.snapshot()['label_rules'],[])
        self.assertEqual(self.agent.snapshot()['preference_feedback'],[])
        self.assertEqual(self.agent.snapshot()['sent'],[])

    def test_explicit_similar_rule_transfers_but_not_to_interview(self):
        row=self.ingest(1)
        submit(self.agent,row['id'],1,'Job applications','similar')
        second=self.ingest(2,'new@example.test')
        self.assertEqual(second['proposal']['label'],'AI: Job applications')
        contrast=self.ingest(3,p=replace(self.p,label_kind='job_interview'))
        self.assertEqual(contrast['proposal']['label'],'AI: Work')
        review=next(r for r in self.agent.snapshot()['label_reviews'] if r['action_id']==second['id'])
        self.assertEqual(review['original_label'],'AI: Work')
        self.assertIsNotNone(review['preference_id'])
        self.assertEqual(review['status'],'needs_review')

    def test_sender_scope_overrides_general_and_pause_stops_use(self):
        first=self.ingest(1);submit(self.agent,first['id'],1,'Applications','similar')
        second=self.ingest(2);submit(self.agent,second['id'],1,'Priority applications','sender')
        self.assertEqual(self.ingest(3)['proposal']['label'],'AI: Priority applications')
        self.assertEqual(self.ingest(4,'elsewhere@example.test')['proposal']['label'],'AI: Applications')
        with self.agent.db:self.agent.db.execute('UPDATE label_rules SET active=0')
        self.assertEqual(self.ingest(5)['proposal']['label'],'AI: Work')

    def test_rules_are_account_scoped_and_persist(self):
        first=self.ingest(1);submit(self.agent,first['id'],1,'Applications','similar')
        self.agent.close();self.agent=Agent(self.path,Fixed(self.p))
        self.assertEqual(self.ingest(2)['proposal']['label'],'AI: Applications')
        with self.agent.db:
            self.agent.db.execute("""INSERT INTO gmail_bindings(email_id,account,message_id,label_id,label_name,initial_inbox)
                                   VALUES('3','other@example.test','m3','label','Wajo-Test',1)""")
        self.assertEqual(self.ingest(3)['proposal']['label'],'AI: Work')

    def test_stale_review_cannot_duplicate_feedback_and_can_correct_later(self):
        row=self.ingest(1);submit(self.agent,row['id'],1,'Applications','similar')
        with self.assertRaises(ValueError):submit(self.agent,row['id'],1,'Wrong','similar')
        submit(self.agent,row['id'],2,'Careers','similar')
        self.assertEqual(len(self.agent.snapshot()['label_feedback']),2)
        self.assertEqual(self.ingest(2)['proposal']['label'],'AI: Careers')

    def test_unknown_or_missing_evidence_cannot_train(self):
        for i,p in enumerate([replace(self.p,label_kind='unknown'),replace(self.p,pattern_evidence='Invented')]):
            row=self.ingest(i,p=p)
            with self.assertRaises(ValueError):submit(self.agent,row['id'],1,'Applications','similar')
            submit(self.agent,row['id'],1,'Applications','email')
        self.assertEqual(self.agent.snapshot()['label_rules'],[])

    def test_suspicion_cannot_be_unblocked_by_label_review_or_memory(self):
        first=self.ingest(1);submit(self.agent,first['id'],1,'Applications','similar')
        blocked=self.ingest(2,p=replace(self.p,suspicious=True))
        self.assertEqual(blocked['status'],'blocked')
        with self.assertRaises(ValueError):submit(self.agent,blocked['id'],1,'Applications','similar')
        send=self.ingest(3,p=Proposal('send','Reply',recipient='x@example.test',text='Hello'))
        self.assertEqual(send['status'],'pending')
        with self.assertRaises(ValueError):submit(self.agent,send['id'],1,'Applications','similar')

    def test_invalid_labels_and_scope_rejected(self):
        row=self.ingest(1)
        for name in ['', 'AI: ', 'x'*101, 'a\nb']:
            with self.assertRaises(ValueError):submit(self.agent,row['id'],1,name,'email')
        with self.assertRaises(ValueError):submit(self.agent,row['id'],1,'Valid','all_mail')
        self.assertEqual(normalize_label('My category'),'AI: My category')


class LabelProviderTests(unittest.TestCase):
    def test_server_rejects_nonlabel_model_actions_in_exercise(self):
        provider=LabelProposer('test-key')
        email=Email('e','x@example.test','Hello','Please send')
        for p in [Proposal('send','x',text='Hello',recipient='x@example.test'),Proposal('archive','x')]:
            response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(asdict(p))}}]}
            with patch.object(provider,'request',return_value=response):
                with self.assertRaises(ProviderError):provider.propose(email)

    def test_batch_has_30_realistic_unique_samples_and_no_expected_labels(self):
        data=json.loads((Path(__file__).parents[1]/'examples'/'label-review-30.json').read_text())
        self.assertEqual(len(data['emails']),30)
        self.assertEqual(len({e['id'] for e in data['emails']}),30)
        for e in data['emails']:
            self.assertGreater(len(e['body']),180)
            self.assertTrue(e['sender'].endswith('.example.test'))
            self.assertNotIn('label',e)
