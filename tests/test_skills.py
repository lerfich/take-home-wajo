import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from mail_agent.core import Agent, Email, Proposal
from mail_agent import skills
from mail_agent.attention import matches, set_rule
from mail_agent.organization import choose as organization_choice, submit as organize
from mail_agent.label_preferences import submit as label_review
from mail_agent.draft_preferences import choose as draft_choice
from mail_agent.multi_labels import resolve, current


class Fixed:
    def __init__(self, proposal):
        self.proposal = proposal

    def propose(self, email):
        return self.proposal


class SkillsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'skills.sqlite3'
        self.p = Proposal('label', 'Booking', label='AI: Travel', label_kind='travel_confirmation',
                          pattern_evidence='Confirmed', attention_cue='personal_commitment', attention_evidence='Confirmed')
        self.provider = Fixed(self.p)
        self.agent = Agent(self.path, self.provider)
        self.email = Email('trip', 'trips@example.test', 'Your booking', 'Confirmed reservation for tomorrow')
        self.row = self.agent.ingest(self.email)
        self.other = self.agent.ingest(Email('other', 'other@example.test', 'Interview', 'Confirmed interview'))
        self.provider.proposal = replace(self.p, label_kind='newsletter', attention_cue='none')
        self.negative = self.agent.ingest(Email('negative', 'offers@example.test', 'Optional offers', 'Confirmed summer offers'))
        self.provider.proposal = replace(self.p, label_kind='unknown', attention_cue='unknown')
        self.unknown = self.agent.ingest(Email('uncertain', 'info@example.test', 'News', 'Confirmed, details later'))
        self.provider.proposal = self.p

    def tearDown(self):
        self.agent.close()
        self.temp.cleanup()

    def suggest(self, family='attention', values=None):
        return skills.suggest(self.agent, family, self.row['id'], values or {'enabled': True}, 'test:' + family)

    def activate(self, ident, **kwargs):
        data = dict(skill_id=ident, scope='similar', exclusions=[], **kwargs)
        result = skills.preview(self.agent, data)
        skills.save(self.agent, dict(data, token=result['token'], reviewed=[e['id'] for e in result['examples']]))
        return result

    def manage(self, ident, operation):
        skills.manage(self.agent, {'skill_id': ident, 'revision': skills.get(self.agent, ident)['revision'], 'operation': operation})

    def test_suggestion_is_inactive_preview_read_only_contrasts_and_unknown(self):
        ident = self.suggest()
        self.assertFalse(matches(self.agent, self.email, self.p))
        before = self.agent.snapshot()
        rows_before = skills.listing(self.agent)
        preview = skills.preview(self.agent, {'skill_id': ident})
        self.assertEqual({e['outcome'] for e in preview['examples']}, {'Applies', 'Does not apply', 'Needs confirmation'})
        self.assertEqual(self.agent.snapshot(), before)
        self.assertEqual(skills.listing(self.agent), rows_before)
        self.assertEqual(self.agent.snapshot()['gmail_operations'], [])

    def test_activation_pause_resume_delete_restart_no_history_reexecution(self):
        ident = self.suggest()
        before = self.agent.snapshot()['actions']
        self.activate(ident)
        self.assertTrue(matches(self.agent, self.email, self.p))
        self.manage(ident, 'pause')
        self.assertFalse(matches(self.agent, self.email, self.p))
        self.agent.close(); self.agent = Agent(self.path, self.provider)
        self.assertEqual(skills.get(self.agent, ident)['status'], 'paused')
        self.manage(ident, 'resume')
        self.assertTrue(matches(self.agent, self.email, self.p))
        self.manage(ident, 'delete')
        self.assertFalse(matches(self.agent, self.email, self.p))
        self.assertEqual(list(self.agent.db.execute('SELECT * FROM skill_examples')), [])
        with self.assertRaises(ValueError):
            self.manage(ident, 'resume')
        self.assertEqual(self.agent.snapshot()['actions'], before)
        self.assertIn('skill_deleted', [r['event'] for r in self.agent.snapshot()['audit']])

    def test_stale_review_cannot_activate_or_manage(self):
        ident = self.suggest(); data = {'skill_id': ident}
        preview = skills.preview(self.agent, data)
        with self.assertRaisesRegex(ValueError, 'every'):
            skills.save(self.agent, dict(data, token=preview['token'], reviewed=[]))
        with self.assertRaisesRegex(ValueError, 'changed'):
            skills.save(self.agent, dict(data, token=preview['token'], reviewed=[e['id'] for e in preview['examples']], changes={'contains': 'other'}))
        self.activate(ident)
        with self.assertRaisesRegex(ValueError, 'changed'):
            skills.manage(self.agent, dict(skill_id=ident, revision=1, operation='delete'))

    def test_refined_meaning_literal_conditions_and_exclusion_use_live_matcher(self):
        ident = self.suggest()
        data = dict(skill_id=ident, scope='similar', exclusions=[self.other['id']], changes={'contains': 'reservation', 'excludes': 'cancelled'})
        preview = skills.preview(self.agent, data)
        skills.save(self.agent, dict(data, token=preview['token'], reviewed=[e['id'] for e in preview['examples']]))
        self.assertTrue(matches(self.agent, self.email, self.p))
        self.assertFalse(matches(self.agent, self.agent.email_for(self.other['id']), self.p))
        self.assertFalse(matches(self.agent, Email('new', self.email.sender, 'Cancelled', 'Confirmed reservation cancelled'), self.p))
        self.assertTrue(matches(self.agent, Email('future', self.email.sender, 'Trip', 'Confirmed RESERVATION'), self.p))

    def test_sender_scope_is_exact_but_ordinary_skill_crosses_accounts(self):
        ident = self.suggest()
        data = dict(skill_id=ident, scope='sender', exclusions=[])
        with self.assertRaisesRegex(ValueError, 'New Skills use similar'):
            skills.preview(self.agent, data)
        # A sender-scoped Skill saved before D keeps its narrow boundary; it is
        # not silently widened when the new-skill control is removed.
        saved = skills.get(self.agent, ident)
        saved['config']['scope'] = 'sender'
        with self.agent.db:
            self.agent.db.execute("UPDATE skills SET config=?,status='active' WHERE id=?",
                                  (json.dumps(saved['config']), ident))
        self.assertFalse(matches(self.agent, self.agent.email_for(self.other['id']), self.p))
        self.assertFalse(matches(self.agent, self.email, replace(self.p, suspicious=True)))
        with self.agent.db:
            self.agent.db.execute('''INSERT INTO gmail_bindings(email_id,account,message_id,label_id,label_name,initial_inbox)
                                     VALUES(?,?,?,?,?,?)''', ('trip', 'other-account@example.test', 'm', 'test', 'Wajo-Test', 1))
        self.assertTrue(matches(self.agent, self.email, self.p))

    def test_organization_and_draft_apply_without_sending(self):
        ident = self.suggest('organization', {'topic': 'Plans', 'subtype': 'Booking', 'important': True})
        self.activate(ident)
        self.assertEqual(organization_choice(self.agent, self.p, self.email)['topic'], 'Plans')
        draft = replace(self.p, action='send', text='Thanks.', recipient=self.email.sender)
        self.provider.proposal = draft
        source = self.agent.ingest(Email('draft', self.email.sender, 'Reply', 'Confirmed reservation'))
        ident = skills.suggest(self.agent, 'draft', source['id'], {'length': 'concise', 'greeting': 'omit', 'signoff': 'omit'}, 'test:draft')
        self.activate(ident)
        self.assertEqual(draft_choice(self.agent, draft, self.email)['length'], 'concise')
        future = self.agent.ingest(Email('next-draft', self.email.sender, 'Reply', 'Confirmed reservation'))
        self.assertEqual(future['status'], 'pending')
        self.assertEqual(self.agent.snapshot()['sent'], [])

    def test_two_labels_transfer_and_third_needs_explicit_resolution(self):
        ident = self.suggest('labels', {'labels': ['AI: Travel', 'AI: Personal']})
        self.activate(ident)
        future = self.agent.ingest(Email('future-labels', self.email.sender, 'Booking', 'Confirmed'))
        self.assertEqual(current(self.agent, future['email_id']), ['AI: Personal', 'AI: Travel'])
        result = label_review(self.agent, future['id'], 1, 'AI: Summer', 'email', 'add')
        self.assertTrue(result['conflict'])
        self.assertEqual(current(self.agent, future['email_id']), ['AI: Personal', 'AI: Travel'])
        with self.assertRaises(ValueError):
            resolve(self.agent, {'action_id': future['id'], 'revision': 1, 'labels': ['AI: Summer']})
        resolve(self.agent, {'action_id': future['id'], 'revision': 1, 'labels': ['AI: Summer', 'AI: Travel']})
        self.assertEqual(current(self.agent, future['email_id']), ['AI: Summer', 'AI: Travel'])

    def test_preview_label_conflict_is_truthful(self):
        with self.agent.db:
            self.agent.db.execute('INSERT INTO labels VALUES(?,?)', (self.email.id, 'AI: Existing'))
        ident = self.suggest('labels', {'labels': ['AI: Extra']})
        result = skills.preview(self.agent, {'skill_id': ident})
        self.assertEqual(result['examples'][0]['outcome'], 'Needs confirmation')
        self.assertEqual(len(current(self.agent, self.email.id)), 2)

    def test_legacy_migration_preserves_preference_and_delete_cannot_revive_it(self):
        organize(self.agent, self.row['id'], 'Plans', 'Travel', True, 'similar')
        set_rule(self.agent, self.row['id'], True, 'similar')
        skills.collect(self.agent)
        org = next(s for s in skills.listing(self.agent) if s['family'] == 'organization' and s['status'] == 'active')
        attention = next(s for s in skills.listing(self.agent) if s['family'] == 'attention' and s['status'] == 'active')
        self.assertEqual(organization_choice(self.agent, self.p, self.email)['topic'], 'Plans')
        self.manage(org['id'], 'pause'); self.manage(attention['id'], 'pause')
        self.assertNotEqual(organization_choice(self.agent, self.p, self.email)['topic'], 'Plans')
        self.assertFalse(matches(self.agent, self.email, self.p))
        self.manage(org['id'], 'delete'); self.manage(attention['id'], 'delete')
        skills.collect(self.agent)
        self.assertEqual(skills.listing(self.agent), [])

    def test_upgrade_does_not_turn_old_one_email_feedback_into_new_suggestions(self):
        organize(self.agent, self.row['id'], 'One email', 'Only', False, 'email')
        with self.agent.db:
            for table in ('skill_draft_seen', 'skill_feedback_seen', 'skill_legacy_links', 'skill_examples', 'skills'):
                self.agent.db.execute('DROP TABLE ' + table)
        self.agent.close(); self.agent = Agent(self.path, self.provider)
        skills.collect(self.agent)
        self.assertEqual(skills.listing(self.agent), [])

    def test_archive_threshold_real_feedback_and_pause(self):
        p = Proposal('archive', 'Receipt', pattern='acknowledgement_only', pattern_evidence='Confirmed',
            requires_action=False, has_deadline=False, significant_change=False, sensitive=False)
        self.provider.proposal = p
        with self.agent.db:
            self.agent.log(None, 'skills_archive_managed', {'account': 'local_simulation'})
        for i in range(3):
            row = self.agent.ingest(Email('archive' + str(i), self.email.sender, 'Receipt', 'Confirmed'))
            self.assertEqual(row['status'], 'pending')
            self.agent.approve(row['id'], 1)
        skills.collect(self.agent)
        skill = next(s for s in skills.listing(self.agent) if s['family'] == 'archive')
        self.assertEqual(skill['status'], 'suggested')
        future = Email('archive-future', self.email.sender, 'Receipt', 'Confirmed')
        self.assertEqual(self.agent.preference(p, future)['mode'], 'ask')
        self.assertEqual(len(self.agent.preference(p, future)['approval_ids']), 3)
        self.activate(skill['id'])
        self.assertEqual(self.agent.preference(p, future)['mode'], 'notify')
        self.assertEqual(self.agent.preference(replace(p, has_deadline=True), future)['mode'], 'ask')
        self.manage(skill['id'], 'pause')
        self.assertEqual(self.agent.preference(p, future)['mode'], 'ask')
        self.manage(skill['id'], 'resume')
        self.agent.correct_archive(row['id'])
        self.assertEqual(self.agent.preference(p, future)['mode'], 'ask')
        self.assertEqual(self.agent.preference(p, future)['approval_ids'], [])

    def test_no_permission_fields_or_manual_constructor(self):
        ident = self.suggest()
        for changes in ({'send': True}, {'scope': '*'}, {'kind': 'made_up'}):
            with self.assertRaises(ValueError):
                skills.preview(self.agent, {'skill_id': ident, 'changes': changes})
        with self.assertRaises(ValueError):
            skills.preview(self.agent, {'action_id': self.row['id']})

    def test_email_override_and_sender_skill_precedence_in_preview(self):
        ident = self.suggest()
        set_rule(self.agent, self.row['id'], False, 'email')
        result = self.activate(ident)
        self.assertEqual(result['examples'][0]['outcome'], 'Does not apply')
        self.assertFalse(matches(self.agent, self.email, self.p))
