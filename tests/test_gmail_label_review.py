import unittest
from unittest.mock import MagicMock
from dataclasses import replace

import test_gmail_executor as fixtures
Fixed = fixtures.Fixed
from mail_agent.core import Proposal
from mail_agent.gmail_executor import run_one, recover
from mail_agent.label_preferences import submit


class GmailLabelReviewTests(unittest.TestCase):
    setUp=fixtures.GmailExecutionTests.setUp
    tearDown=fixtures.GmailExecutionTests.tearDown

    def label_action(self):
        self.labels.append({'id':'human','name':'human ready','type':'user'})
        self.ids.add('human')
        self.agent.proposer=Fixed(Proposal('label','Receipt',label='AI: Work',label_kind='job_application_receipt',pattern_evidence='Receipt only'))
        action=self.agent.ingest(self.email)
        run_one(self.path,self.executor)
        def create(**kwargs):
            request=MagicMock()
            def execute():
                item={'id':'new-label','name':kwargs['body']['name'],'type':'user'}
                self.labels.append(item)
                return item
            request.execute.side_effect=execute
            return request
        self.api.users.return_value.labels.return_value.create.side_effect=create
        return action

    def test_replacement_removes_only_previous_ai_label_and_learns_after_verification(self):
        action=self.label_action()
        submit(self.agent,action['id'],1,'Job applications','similar')
        self.assertEqual(self.agent.snapshot()['label_rules'],[])
        self.assertEqual(self.agent.snapshot()['label_feedback'][0]['status'],'pending')
        run_one(self.path,self.executor)
        self.assertNotIn('ai',self.ids)
        self.assertTrue({'new-label','human','INBOX','test'}.issubset(self.ids))
        self.assertEqual(self.agent.get(action['id'])['proposal']['label'],'AI: Job applications')
        self.assertEqual(self.agent.snapshot()['label_reviews'][0]['status'],'reviewed')
        self.assertEqual(self.agent.snapshot()['label_rules'][0]['label'],'AI: Job applications')
        self.assertEqual(self.agent.snapshot()['preference_feedback'],[])
        self.api.users.return_value.messages.return_value.send.assert_not_called()

    def test_confirm_existing_label_is_verified_without_mutation(self):
        action=self.label_action()
        modifications=self.api.users.return_value.messages.return_value.modify.call_count
        submit(self.agent,action['id'],1,'AI: Work','email')
        run_one(self.path,self.executor)
        self.assertEqual(self.api.users.return_value.messages.return_value.modify.call_count,modifications)
        self.assertEqual(self.agent.snapshot()['label_reviews'][0]['status'],'reviewed')
        self.assertEqual(self.agent.snapshot()['label_rules'],[])

    def test_unknown_change_never_repeats_and_read_only_reconciles(self):
        action=self.label_action()
        submit(self.agent,action['id'],1,'Applications','similar')
        def uncertain(**kwargs):
            self.ids.discard('ai');self.ids.add('new-label')
            raise TimeoutError('PRIVATE')
        messages=self.api.users.return_value.messages.return_value
        messages.modify.side_effect=uncertain
        run_one(self.path,self.executor)
        self.assertEqual(self.agent.get(action['id'])['status'],'unknown')
        self.assertEqual(self.agent.snapshot()['label_rules'],[])
        attempts=messages.modify.call_count
        self.assertFalse(run_one(self.path,self.executor))
        op=self.agent.snapshot()['gmail_operations'][-1]
        run_one(self.path,self.executor,op['id'],check_only=True)
        self.assertEqual(messages.modify.call_count,attempts)
        self.assertEqual(self.agent.get(action['id'])['status'],'executed')
        self.assertEqual(self.agent.snapshot()['label_feedback'][0]['status'],'verified')
        self.assertNotIn('PRIVATE',str(self.agent.snapshot()))

    def test_external_old_label_removal_blocks_overwrite_and_learning(self):
        action=self.label_action()
        submit(self.agent,action['id'],1,'Applications','similar')
        self.ids.discard('ai')
        self.api.users.return_value.messages.return_value.modify.reset_mock()
        run_one(self.path,self.executor)
        self.assertEqual(self.agent.get(action['id'])['status'],'error')
        self.api.users.return_value.messages.return_value.modify.assert_not_called()
        self.assertEqual(self.agent.snapshot()['label_rules'],[])

    def test_forged_revision_or_removed_scope_blocks_correction(self):
        for kind in ['revision','scope']:
            with self.subTest(kind=kind):
                if kind=='scope':
                    # Reset isolated fixture for the second condition.
                    self.tearDown();self.setUp()
                action=self.label_action()
                submit(self.agent,action['id'],1,'Applications','similar')
                if kind=='revision':
                    with self.agent.db:self.agent.db.execute("UPDATE actions SET revision=99 WHERE id=?",(action['id'],))
                else:self.ids.discard('test')
                self.api.users.return_value.messages.return_value.modify.reset_mock()
                run_one(self.path,self.executor)
                self.api.users.return_value.messages.return_value.modify.assert_not_called()
                self.assertEqual(self.agent.snapshot()['label_rules'],[])

    def test_restart_recovery_preserves_uncertain_review_without_training(self):
        action=self.label_action();submit(self.agent,action['id'],1,'Applications','similar')
        op=self.agent.snapshot()['gmail_operations'][-1]
        with self.agent.db:self.agent.db.execute("UPDATE gmail_operations SET status='processing' WHERE id=?",(op['id'],))
        recover(self.path)
        self.assertFalse(run_one(self.path,self.executor))
        self.assertEqual(self.agent.get(action['id'])['status'],'unknown')
        self.assertEqual(self.agent.snapshot()['label_rules'],[])

    def test_add_preserves_original_and_unrelated_labels(self):
        action=self.label_action()
        submit(self.agent,action['id'],1,'Follow up','email',mode='add')
        run_one(self.path,self.executor)
        self.assertTrue({'ai','new-label','human','INBOX','test'}.issubset(self.ids))
        body=self.api.users.return_value.messages.return_value.modify.call_args.kwargs['body']
        self.assertEqual(body['removeLabelIds'],[])
        self.assertEqual(self.agent.get(action['id'])['proposal']['label'],'AI: Work')
        labels={r['label'] for r in self.agent.snapshot()['labels']}
        self.assertTrue({'AI: Work','AI: Follow up'}.issubset(labels))
        self.assertEqual(self.agent.snapshot()['label_rules'],[])

    def test_uncertain_add_reconciles_without_removing_original(self):
        action=self.label_action()
        submit(self.agent,action['id'],1,'Follow up','email',mode='add')
        messages=self.api.users.return_value.messages.return_value
        def uncertain(**kwargs):
            self.ids.add('new-label')
            raise TimeoutError()
        messages.modify.side_effect=uncertain
        run_one(self.path,self.executor)
        self.assertEqual(self.agent.get(action['id'])['status'],'unknown')
        attempts=messages.modify.call_count
        op=self.agent.snapshot()['gmail_operations'][-1]
        run_one(self.path,self.executor,op['id'],check_only=True)
        self.assertEqual(messages.modify.call_count,attempts)
        self.assertIn('ai',self.ids)
        self.assertEqual(self.agent.get(action['id'])['status'],'executed')
