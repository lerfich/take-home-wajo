import unittest
import json
import tempfile
import sqlite3
from pathlib import Path
from dataclasses import replace
from mail_agent.core import Agent, Email, Proposal
from mail_agent.attention import set_rule, matches, cue_for, effective_rule


class Fixed:
    def __init__(self,p):self.p=p
    def propose(self,email):return self.p


class AttentionTests(unittest.TestCase):
    def test_legacy_rule_migration_preserves_explicit_cues_and_history(self):
        from mail_agent.attention import initialize
        db = sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE attention_rules(account TEXT, kind TEXT, scope TEXT, enabled INTEGER, PRIMARY KEY(account,kind,scope))')
            db.execute("INSERT INTO attention_rules VALUES('local','travel_confirmation','*',0)")
            initialize(db)
            self.assertEqual(db.execute('SELECT enabled,cue FROM attention_rules').fetchone(), (0, 'personal_commitment'))
            db.execute("UPDATE attention_rules SET cue='none'")
            initialize(db)
            self.assertEqual(db.execute('SELECT enabled,cue FROM attention_rules').fetchone(), (0, 'none'))
            self.assertEqual(db.execute('SELECT count(*) FROM attention_feedback').fetchone()[0], 0)
        finally:
            db.close()

    def test_precedence_history_and_restart(self):
        p = Proposal('label', 'Booking', label='AI: Travel', label_kind='travel_confirmation',
                     attention_cue='personal_commitment', attention_evidence='Confirmed')
        email = Email('1', 'one@example.test', 'Trip', 'Confirmed')
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'test.sqlite3')
            a = Agent(path, Fixed(p))
            row = a.ingest(email)
            set_rule(a, row['id'], True, 'similar')
            set_rule(a, row['id'], False, 'sender')
            self.assertFalse(matches(a, email, p))
            self.assertTrue(matches(a, replace(email, id='2', sender='two@example.test'), p))
            set_rule(a, row['id'], True, 'email')
            self.assertTrue(matches(a, email, p))
            set_rule(a, row['id'], False, 'email')
            self.assertFalse(matches(a, email, p))
            result = set_rule(a, row['id'], True, 'email')
            self.assertNotIn('unchanged', result)
            self.assertTrue(set_rule(a, row['id'], True, 'email')['unchanged'])
            self.assertEqual([r['enabled'] for r in a.snapshot()['attention_feedback']
                              if r['scope'] == 'email:1'], [1, 0, 1])
            before = a.snapshot()
            a.close()
            a = Agent(path, Fixed(p))
            try:
                self.assertEqual(a.snapshot(), before)
                self.assertEqual(effective_rule(a, email, p)['scope'], 'email:1')
                self.assertTrue(matches(a, email, p))
                self.assertEqual(a.ingest(email)['id'], row['id'])
                self.assertEqual(a.snapshot(), before)
            finally:
                a.close()

    def test_explicit_none_unknown_and_missing_legacy_field(self):
        email = Email('1', 'one@example.test', 'Trip', 'Confirmed')
        legacy = dict(action='label', reason='Booking', label='AI: Travel',
                      label_kind='travel_confirmation', pattern_evidence='Confirmed')
        self.assertEqual(cue_for(email, Proposal(**json.loads(json.dumps(legacy)))), 'personal_commitment')
        for cue in ('none', 'unknown'):
            self.assertEqual(cue_for(email, Proposal(**legacy, attention_cue=cue)), cue)
        self.assertEqual(cue_for(email, Proposal(**legacy, attention_cue='personal_commitment',
                                               attention_evidence='missing')), 'unknown')

    def test_other_example_changes_state_before_original_is_saved_again(self):
        p = Proposal('label', 'Booking', label='AI: Travel', label_kind='travel_confirmation',
                     attention_cue='personal_commitment', attention_evidence='Confirmed')
        a = Agent(':memory:', Fixed(p))
        try:
            first = a.ingest(Email('1', 'one@example.test', 'Trip', 'Confirmed'))
            second = a.ingest(Email('2', 'two@example.test', 'Trip', 'Confirmed'))
            set_rule(a, first['id'], True, 'similar')
            set_rule(a, second['id'], False, 'similar')
            self.assertNotIn('unchanged', set_rule(a, first['id'], True, 'similar'))
            self.assertEqual([r['enabled'] for r in a.snapshot()['attention_feedback']], [1, 0, 1])
        finally:
            a.close()

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
            with self.assertRaisesRegex(ValueError,'evidenced attention cue'):
                set_rule(a,r['id'],True,'similar')
            self.assertEqual(a.snapshot()['attention_feedback'],[])
            self.assertTrue(set_rule(a,r['id'],True,'email')['saved'])
        finally:a.close()

    def test_semantic_cue_transfers_across_topics_but_not_writing_style(self):
        first=Proposal('label','Booking',label='AI: Travel',label_kind='travel_confirmation',
                       pattern_evidence='Booking confirmed',attention_cue='personal_commitment',
                       attention_evidence='Booking confirmed')
        a=Agent(':memory:',Fixed(first))
        try:
            r=a.ingest(Email('1','travel@example.test','Trip','Booking confirmed for October 3'))
            set_rule(a,r['id'],True,'similar')
            a.proposer=Fixed(Proposal('none','Interview choice',needs_human=True,label_kind='job_interview',
                             pattern_evidence='choose a time',attention_cue='personal_commitment',
                             attention_evidence='choose a time'))
            interview=a.ingest(Email('2','jobs@example.test','Interview','Please choose a time next week'))
            self.assertEqual(interview['autonomy'],'escalate')
            self.assertTrue(any(x['action_id']==interview['id'] for x in a.snapshot()['attention_items']))
            set_rule(a,interview['id'],False,'similar')
            self.assertFalse(any(r['enabled'] for r in a.snapshot()['attention_rules']
                                 if r['cue']=='personal_commitment' and r['scope']=='*'))
            a.proposer=Fixed(first)
            later=a.ingest(Email('4','travel@example.test','Trip','Booking confirmed for October 4'))
            self.assertFalse(any(x['action_id']==later['id'] for x in a.snapshot()['attention_items']))
            a.proposer=Fixed(Proposal('label','Marketing urgency',label='AI: Newsletter',label_kind='newsletter',
                             pattern_evidence='Last chance today',attention_cue='none'))
            promotion=a.ingest(Email('3','promo@example.test','Last chance','Last chance today'))
            self.assertFalse(any(x['action_id']==promotion['id'] for x in a.snapshot()['attention_items']))
        finally:a.close()

    def test_repeated_save_is_idempotent_but_second_example_is_retained(self):
        p=Proposal('label','Booking',label='AI: Travel',label_kind='travel_confirmation',
                   pattern_evidence='Confirmed',attention_cue='personal_commitment',
                   attention_evidence='Confirmed')
        a=Agent(':memory:',Fixed(p))
        try:
            first=a.ingest(Email('1','one@example.test','Trip one','Confirmed one'))
            saved=set_rule(a,first['id'],True,'similar')
            repeated=set_rule(a,first['id'],True,'similar')
            second=a.ingest(Email('2','two@example.test','Trip two','Confirmed two'))
            set_rule(a,second['id'],True,'similar')
            self.assertNotIn('unchanged',saved)
            self.assertTrue(repeated['unchanged'])
            self.assertEqual(len(a.snapshot()['attention_feedback']),2)
        finally:a.close()
