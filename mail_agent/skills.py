"""Reviewed portable skills. This module never executes mail operations.

The deterministic matcher is shared by preview and live preference selection.
Free-text refinements are explicitly literal conditions, not unverified LLM rules.
"""
import hashlib
import json
from contextlib import nullcontext

from .label_preferences import LABEL_KINDS, account_for, normalize_label

FAMILIES = {'attention', 'organization', 'labels', 'draft', 'archive'}


def initialize(db):
    upgrading = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='skill_feedback_seen'").fetchone() is None
    db.executescript('''
        CREATE TABLE IF NOT EXISTS skills (
          id INTEGER PRIMARY KEY AUTOINCREMENT, family TEXT NOT NULL,
          account TEXT NOT NULL, source_id INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'suggested', revision INTEGER NOT NULL DEFAULT 1,
          config TEXT NOT NULL, origin TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS skill_examples (
          skill_id INTEGER NOT NULL, action_id INTEGER NOT NULL, excluded INTEGER NOT NULL,
          PRIMARY KEY(skill_id,action_id));
        CREATE TABLE IF NOT EXISTS skill_legacy_links (
          family TEXT NOT NULL, legacy_key TEXT NOT NULL, skill_id INTEGER NOT NULL,
          PRIMARY KEY(family,legacy_key));
        CREATE TABLE IF NOT EXISTS skill_feedback_seen (
          family TEXT NOT NULL, feedback_id INTEGER NOT NULL,
          PRIMARY KEY(family,feedback_id));
        CREATE TABLE IF NOT EXISTS skill_draft_seen (
          action_id INTEGER NOT NULL, revision INTEGER NOT NULL,
          PRIMARY KEY(action_id,revision));
    ''')
    if upgrading:
        for family, table in [('attention', 'attention_feedback'), ('organization', 'organization_feedback'),
                              ('labels', 'label_feedback'), ('archive', 'preference_feedback')]:
            db.execute('INSERT OR IGNORE INTO skill_feedback_seen SELECT ?,id FROM ' + table, (family,))
        db.execute("""INSERT OR IGNORE INTO skill_draft_seen
            SELECT id,revision FROM actions WHERE status='pending' AND revision>1""")


def unpack(row):
    result = dict(row)
    result['config'] = json.loads(result['config'])
    return result


def get(agent, ident):
    if type(ident) is not int:
        raise ValueError('Choose an existing suggested skill')
    row = agent.db.execute('SELECT * FROM skills WHERE id=?', (ident,)).fetchone()
    if not row:
        raise ValueError('This skill no longer exists')
    return unpack(row)


def suggest(agent, family, action_id, values, origin, *, transaction=True):
    """Called only from trusted feedback paths; no mail/model text is an instruction."""
    from .core import Proposal, learnable
    from .attention import cue_for, ATTENTION_CUES
    if family not in FAMILIES:
        raise ValueError('Unsupported skill type')
    existing = agent.db.execute('SELECT id FROM skills WHERE origin=?', (origin,)).fetchone()
    if existing:
        return existing['id']
    action = agent.get(action_id)
    p = Proposal(**action['proposal']); email = agent.email_for(action_id)
    kind = cue_for(email, p) if family == 'attention' else p.pattern if family == 'archive' else p.label_kind
    if family == 'attention':
        eligible = kind in set(ATTENTION_CUES) - {'none'}
    elif family == 'archive':
        eligible = learnable(p, email)
    else:
        eligible = kind in LABEL_KINDS and p.pattern_evidence.strip() and p.pattern_evidence in email.body
    if not eligible or p.suspicious or p.needs_human:
        return None
    config = dict(values, kind=kind, scope='similar', contains='', excludes='')
    with agent.db if transaction else nullcontext():
        agent.db.execute('INSERT OR IGNORE INTO skills(family,account,source_id,config,origin) VALUES(?,?,?,?,?)',
                         (family, account_for(agent, email.id), action_id, json.dumps(config), origin))
    row = agent.db.execute('SELECT id FROM skills WHERE origin=?', (origin,)).fetchone()
    return row['id']


def collect(agent):
    """Derive durable suggestions from verified feedback, including background Gmail results.

    Audit tombstones prevent deletion from recreating a suggestion on refresh.
    """
    migrate_legacy(agent)
    deleted = {json.loads(r['details']).get('origin') for r in agent.db.execute(
        "SELECT details FROM audit WHERE event='skill_deleted'")}
    sources = [
        ('attention', 'SELECT * FROM attention_feedback', lambda r: {'enabled': bool(r['enabled'])}),
        ('organization', 'SELECT * FROM organization_feedback', lambda r: {
            'topic': r['topic'], 'subtype': r['subtype'], 'important': bool(r['important'])}),
        ('labels', "SELECT * FROM label_feedback WHERE status='verified'", lambda r: {
            'labels': [x['label'] for x in agent.db.execute('SELECT label FROM labels WHERE email_id=? ORDER BY label',
                (agent.get(r['action_id'])['email_id'],)) if x['label'].startswith('AI: ')][:2]}),
        ('archive', 'SELECT * FROM preference_feedback', lambda r: {'mode': 'archive' if r['positive'] else 'keep'}),
    ]
    for family, query, values in sources:
        grouped = {}
        seen_ids = {r['feedback_id'] for r in agent.db.execute(
            'SELECT feedback_id FROM skill_feedback_seen WHERE family=?', (family,))}
        unseen = [r for r in agent.db.execute(query) if r['id'] not in seen_ids]
        for row in unseen:
            email = agent.email_for(row['action_id'])
            account = account_for(agent, email.id)
            if family == 'attention':
                key = (account, row['cue'])
            elif family in {'organization', 'labels'}:
                key = (account, row['kind'])
            else:
                key = (account, row['scope'], row['pattern'])
            grouped[key] = row
        for row in grouped.values():
            origin = family + ':' + str(row['id'])
            if origin not in deleted and not agent.db.execute('SELECT 1 FROM skills WHERE origin=?', (origin,)).fetchone():
                suggest(agent, family, row['action_id'], values(row), origin)
        with agent.db:
            agent.db.executemany('INSERT OR IGNORE INTO skill_feedback_seen VALUES(?,?)',
                                 [(family, row['id']) for row in unseen])
    # Editing a saved draft supplies the before/after example; it never approves sending.
    from .draft_preferences import preview as draft_preview
    for row in list(agent.db.execute("SELECT id,revision FROM actions WHERE status='pending' AND revision>1")):
        if agent.db.execute('SELECT 1 FROM skill_draft_seen WHERE action_id=? AND revision=?',
                            (row['id'], row['revision'])).fetchone():
            continue
        origin = 'draft:' + str(row['id']) + ':' + str(row['revision'])
        if origin in deleted:
            continue
        try:
            style = draft_preview(agent, row['id'], row['revision'])
        except ValueError:
            style = None
        if style:
            suggest(agent, 'draft', row['id'], style, origin)
        with agent.db:
            agent.db.execute('INSERT OR IGNORE INTO skill_draft_seen VALUES(?,?)', (row['id'], row['revision']))


def listing(agent):
    result = []
    for row in agent.db.execute('SELECT * FROM skills ORDER BY id DESC'):
        skill = unpack(row)
        skill['examples'] = [dict(r) for r in agent.db.execute(
            'SELECT action_id,excluded FROM skill_examples WHERE skill_id=?', (skill['id'],))]
        skill['title'] = title(skill)
        result.append(skill)
    return result


def title(skill):
    c = skill['config']; family = skill['family']
    if family == 'attention':
        return 'Keep in Needs attention' if c['enabled'] else 'Do not surface in Needs attention'
    if family == 'organization':
        return c['topic'] + ' / ' + c['subtype'] + (' · Important' if c['important'] else '')
    if family == 'labels':
        return 'Apply ' + ' + '.join(c['labels'])
    if family == 'draft':
        from .draft_preferences import describe
        return describe(c)
    return 'Keep in inbox' if c['mode'] == 'keep' else 'Archive eligible mail after three approvals'


def match(agent, skill, email, proposal, exclusions=()):
    """Return match/no-match/unknown, never infer authority from sender or email prose."""
    from .core import learnable
    from .attention import cue_for
    c = skill['config']; family = skill['family']
    source = agent.email_for(skill['source_id'])
    if c['scope'] == 'sender' and email.sender.casefold() != source.sender.casefold():
        return 'no-match', 'The sender is outside this skill.'
    if email.id in exclusions:
        return 'no-match', 'You excluded this email from this skill.'
    if proposal.suspicious or proposal.needs_human:
        return 'unknown', 'Safety checks require human review; this skill grants no permission.'
    kind = cue_for(email, proposal) if family == 'attention' else proposal.pattern if family == 'archive' else proposal.label_kind
    if kind == 'unknown' or (family != 'attention' and (
            not proposal.pattern_evidence.strip() or proposal.pattern_evidence not in email.body)):
        return 'unknown', 'No supported, evidenced meaning is available.'
    # New Skills are semantic-first.  Subject and sender only improve an
    # already compatible semantic match; neither can manufacture one or prove
    # that the sender is authentic.  Legacy sender-scoped Skills retain their
    # explicit boundary in the check above.
    from .semantic_matcher import SemanticContext, compare
    semantic = compare(
        SemanticContext(c['kind'], str(c.get('subtopic', '')), source.subject,
                        source.sender, str(c.get('evidence', ''))),
        SemanticContext(kind, str(c.get('subtopic', '')), email.subject,
                        email.sender, proposal.pattern_evidence,
                        suspicious=proposal.suspicious),
    )
    if semantic.outcome == 'unknown':
        return 'unknown', semantic.reason
    if not semantic.matched:
        return 'no-match', semantic.reason
    body = email.body.casefold()
    if c.get('contains') and c['contains'].casefold() not in body:
        return 'no-match', 'The required literal phrase is absent.'
    if c.get('excludes') and c['excludes'].casefold() in body:
        return 'no-match', 'The excluded literal phrase is present.'
    if family == 'archive' and not learnable(proposal, email):
        return 'unknown', 'Archive risk checks require separate approval.'
    if family == 'labels' and proposal.action != 'label':
        return 'no-match', 'This skill only adjusts an eligible label action.'
    if family == 'draft' and proposal.action not in {'draft', 'send'}:
        return 'no-match', 'There is no draft to style.'
    return 'match', semantic.reason


def choose(agent, family, email, proposal, *, candidate=None, candidate_exclusions=()):
    rows = [unpack(r) for r in agent.db.execute("SELECT * FROM skills WHERE family=? AND status='active' ORDER BY id DESC", (family,))]
    if candidate:
        rows = [candidate] + [r for r in rows if r['id'] != candidate['id']]
    rows.sort(key=lambda s: s['config']['scope'] != 'sender')
    for skill in rows:
        exclusions = [agent.email_for(r['action_id']).id for r in agent.db.execute(
            'SELECT action_id FROM skill_examples WHERE skill_id=? AND excluded=1', (skill['id'],))]
        if candidate and skill['id'] == candidate['id']:
            exclusions = candidate_exclusions
        if match(agent, skill, email, proposal, exclusions)[0] == 'match':
            return skill
    return None


def legacy_managed(agent, family, key):
    return agent.db.execute('SELECT 1 FROM skill_legacy_links WHERE family=? AND legacy_key=?',
                            (family, str(key))).fetchone() is not None


def migrate_legacy(agent):
    """Preserve previously confirmed preferences while giving them the new lifecycle.

    Links remain after deletion so a legacy rule cannot revive a removed skill.
    Per-email attention choices are not future skills and remain explicit overrides.
    """
    specs = [('labels', 'label_rules', 'label_feedback'),
             ('organization', 'organization_rules', 'organization_feedback'),
             ('draft', 'draft_style_rules', 'draft_style_feedback')]
    for family, table, feedback_table in specs:
        for row in list(agent.db.execute('SELECT * FROM ' + table)):
            if legacy_managed(agent, family, row['id']):
                continue
            feedback = agent.db.execute('SELECT * FROM ' + feedback_table + ' WHERE id=?', (row['feedback_id'],)).fetchone()
            if not feedback:
                continue
            if family == 'labels':
                config = {'labels': [row['label']]}
            elif family == 'organization':
                config = {k: row[k] for k in ('topic', 'subtype')}; config['important'] = bool(row['important'])
            else:
                config = {k: row[k] for k in ('length', 'greeting', 'signoff')}
                versions = list(agent.db.execute('SELECT text FROM draft_edit_versions WHERE action_id=? AND revision<=? ORDER BY revision', (feedback['action_id'], feedback['revision'])))
                if versions:
                    config.update(example_before=versions[0]['text'][:1000], example_after=versions[-1]['text'][:1000])
            ident = suggest(agent, family, feedback['action_id'], config, family + ':' + str(feedback['id']))
            if ident:
                skill = get(agent, ident); config = skill['config']; config['scope'] = 'similar' if row['scope'] == '*' else 'sender'
                with agent.db:
                    agent.db.execute('UPDATE skills SET config=?,status=? WHERE id=?', (json.dumps(config), 'active' if row['active'] else 'paused', ident))
                    agent.db.execute('INSERT INTO skill_legacy_links VALUES(?,?,?)', (family, str(row['id']), ident))
                    agent.db.execute('INSERT OR IGNORE INTO skill_examples VALUES(?,?,0)', (ident, feedback['action_id']))
    for row in list(agent.db.execute("SELECT rowid AS legacy_id,* FROM attention_rules WHERE scope NOT LIKE 'email:%'")):
        key = json.dumps([row['account'], row['kind'], row['scope']])
        if legacy_managed(agent, 'attention', key):
            continue
        feedback = agent.db.execute('SELECT * FROM attention_feedback WHERE account=? AND cue=? AND scope=? ORDER BY id DESC LIMIT 1',
            (row['account'], row['cue'], row['scope'])).fetchone()
        if not feedback:
            continue
        ident = suggest(agent, 'attention', feedback['action_id'], {'enabled': bool(row['enabled'])}, 'attention:' + str(feedback['id']))
        if ident:
            config = get(agent, ident)['config']; config['scope'] = 'similar' if row['scope'] == '*' else 'sender'
            with agent.db:
                agent.db.execute("UPDATE skills SET config=?,status='active' WHERE id=?", (json.dumps(config), ident))
                agent.db.execute('INSERT INTO skill_legacy_links VALUES(?,?,?)', ('attention', key, ident))
                agent.db.execute('INSERT OR IGNORE INTO skill_examples VALUES(?,?,0)', (ident, feedback['action_id']))
    archive_groups = {}
    for row in agent.db.execute('SELECT * FROM preference_feedback ORDER BY id'):
        account = account_for(agent, agent.email_for(row['action_id']).id)
        marker = json.dumps({'account': account}, ensure_ascii=False)
        if agent.db.execute("SELECT 1 FROM audit WHERE event='skills_archive_managed' AND details=?", (marker,)).fetchone():
            continue
        archive_groups[(account, row['scope'], row['pattern'])] = row
    for (account, scope, pattern), row in archive_groups.items():
        ident = suggest(agent, 'archive', row['action_id'], {'mode': 'archive'}, 'archive:' + str(row['id']))
        if ident:
            config = get(agent, ident)['config']; config['scope'] = 'similar' if scope == '*' else 'sender'
            with agent.db:
                agent.db.execute("UPDATE skills SET config=?,status='active' WHERE id=?", (json.dumps(config), ident))
                agent.db.execute('INSERT OR IGNORE INTO skill_examples VALUES(?,?,0)', (ident, row['action_id']))
    for account in {key[0] for key in archive_groups}:
        with agent.db:
            agent.log(None, 'skills_archive_managed', {'account': account})


def archive_evidence(agent, skill):
    """Real approvals only, bound to the source account; last correction resets count."""
    source = agent.email_for(skill['source_id']); c = skill['config']
    rows = []
    for row in agent.db.execute('SELECT * FROM preference_feedback WHERE pattern=? ORDER BY id', (c['kind'],)):
        email = agent.email_for(row['action_id'])
        if account_for(agent, email.id) != skill['account']:
            continue
        if c['scope'] == 'sender' and email.sender.casefold() != source.sender.casefold():
            continue
        from .core import Proposal
        if match(agent, skill, email, Proposal(**agent.get(row['action_id'])['proposal']))[0] != 'match':
            continue
        rows.append(row)
    cutoff = max((r['id'] for r in rows if not r['positive']), default=0)
    return list({r['action_id'] for r in rows if r['positive'] and r['id'] > cutoff})


def validated_config(skill, data):
    from .attention import ATTENTION_CUES
    from .core import PATTERNS
    from .organization import _text
    c = dict(skill['config'])
    changes = data.get('changes', {})
    if type(changes) is not dict:
        raise ValueError('Invalid skill refinement')
    allowed = {'kind', 'contains', 'excludes'} | {
        'attention': {'enabled'}, 'organization': {'topic', 'subtype', 'important'},
        'labels': {'labels'}, 'draft': {'length', 'greeting', 'signoff'}, 'archive': {'mode'}}[skill['family']]
    if set(changes) - allowed:
        raise ValueError('This refinement cannot change permissions')
    c.update(changes)
    requested_scope = data.get('scope', c['scope'])
    if requested_scope != c['scope']:
        raise ValueError('Skill scope is fixed. New Skills use similar situations by default')
    c['scope'] = requested_scope
    kinds = set(ATTENTION_CUES) - {'none'} if skill['family'] == 'attention' else PATTERNS if skill['family'] == 'archive' else set(LABEL_KINDS)
    if c['scope'] not in {'similar', 'sender'} or c['kind'] not in kinds:
        raise ValueError('Choose a supported meaning and scope')
    for key in ('contains', 'excludes'):
        if type(c.get(key)) is not str or len(c[key]) > 200:
            raise ValueError('Literal conditions must be text up to 200 characters')
        c[key] = c[key].strip()
    family = skill['family']
    if family == 'attention' and type(c['enabled']) is not bool:
        raise ValueError('Choose whether to surface matching emails')
    if family == 'organization':
        c['topic'] = _text(c['topic'], 'topic'); c['subtype'] = _text(c['subtype'], 'subtype')
        if type(c['important']) is not bool:
            raise ValueError('Choose an importance setting')
    if family == 'labels':
        if type(c['labels']) is not list or not 1 <= len(c['labels']) <= 2:
            raise ValueError('Choose one or two additional labels')
        c['labels'] = list(dict.fromkeys(normalize_label(x) for x in c['labels']))
    if family == 'draft' and (c['length'] not in {'concise', 'brief', 'standard'} or
            c['greeting'] not in {'include', 'omit'} or c['signoff'] not in {'include', 'omit'}):
        raise ValueError('Choose a supported draft style')
    if family == 'archive' and c['mode'] not in {'archive', 'keep'}:
        raise ValueError('Choose archive or keep')
    return c


def preview(agent, data):
    from .core import Proposal
    skill = get(agent, data.get('skill_id'))
    skill['config'] = validated_config(skill, data)
    exclusions = data.get('exclusions', [])
    if type(exclusions) is not list or any(type(x) is not int for x in exclusions):
        raise ValueError('Invalid examples')
    rows = list(agent.db.execute('SELECT id FROM actions ORDER BY id DESC'))
    groups = {'match': [], 'no-match': [], 'unknown': []}
    candidates = [skill['source_id']]
    for row in rows:
        email = agent.email_for(row['id'])
        if row['id'] == skill['source_id']:
            continue
        outcome, _ = match(agent, skill, email, Proposal(**agent.get(row['id'])['proposal']))
        if len(groups[outcome]) < 2:
            groups[outcome].append(row['id'])
    candidates += groups['match'] + groups['no-match'] + groups['unknown'][:1]
    saved_examples = [r['action_id'] for r in agent.db.execute('SELECT action_id FROM skill_examples WHERE skill_id=? AND excluded=1', (skill['id'],))]
    candidates = list(dict.fromkeys(candidates + saved_examples))
    if not set(exclusions) <= set(candidates):
        raise ValueError('Choose exceptions from the displayed examples')
    excluded_emails = [agent.email_for(x).id for x in exclusions]
    examples = []
    for ident in candidates:
        email = agent.email_for(ident); action = agent.get(ident)
        outcome, reason = match(agent, skill, email, Proposal(**action['proposal']), excluded_emails)
        if outcome == 'match':
            chosen = choose(agent, skill['family'], email, Proposal(**action['proposal']), candidate=skill, candidate_exclusions=excluded_emails)
            if chosen and chosen['id'] != skill['id']:
                outcome, reason = 'no-match', 'A more specific active skill takes precedence.'
        if outcome == 'match' and skill['family'] == 'attention':
            rule = agent.db.execute("SELECT enabled FROM attention_rules WHERE account=? AND scope=? ORDER BY rowid DESC LIMIT 1",
                (skill['account'], 'email:' + email.id)).fetchone()
            if rule and bool(rule['enabled']) != skill['config']['enabled']:
                outcome, reason = 'no-match', 'Your explicit choice for this email overrides future skills.'
        if outcome == 'match' and skill['family'] == 'archive':
            keep = agent.db.execute("SELECT 1 FROM archive_rules WHERE scope IN ('*',?) AND pattern IN ('*',?)",
                (email.sender.casefold(), action['proposal']['pattern'])).fetchone()
            from .attention import matches
            if keep or matches(agent, email, Proposal(**action['proposal'])):
                outcome, reason = 'unknown', 'An existing keep or attention preference prevents automatic archiving.'
        if outcome == 'match' and skill['family'] == 'archive' and skill['config']['mode'] == 'archive':
            count = len(archive_evidence(agent, skill))
            if count < 3:
                outcome, reason = 'unknown', f'{count} of 3 real approvals. The agent will keep asking.'
        if outcome == 'match' and skill['family'] == 'labels':
            existing = {r['label'] for r in agent.db.execute('SELECT label FROM labels WHERE email_id=?', (email.id,)) if r['label'].startswith('AI: ')}
            if len(existing | set(skill['config']['labels'])) > 2:
                outcome, reason = 'unknown', 'A third additional label would conflict. Choose two; existing labels will not be replaced automatically.'
        examples.append(dict(id=ident, sender=email.sender, subject=email.subject, body=email.body,
            outcome={'match': 'Applies', 'no-match': 'Does not apply', 'unknown': 'Needs confirmation'}[outcome],
            reason=reason, excluded=ident in exclusions, result=title(skill)))
    from .attention import ATTENTION_CUES
    fingerprint = {'skill': skill, 'examples': examples, 'exclusions': exclusions,
                   'approvals': archive_evidence(agent, skill) if skill['family'] == 'archive' else [],
                   'source': agent.get(skill['source_id'])}
    fingerprint['rules'] = {table: [dict(r) for r in agent.db.execute('SELECT * FROM ' + table)]
                            for table in ('skills', 'skill_examples', 'attention_rules', 'archive_rules')}
    token = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
    return dict(token=token, examples=examples, title=title(skill), config=skill['config'], family=skill['family'],
        cue=ATTENTION_CUES.get(skill['config']['kind'], LABEL_KINDS.get(skill['config']['kind'], skill['config']['kind'])),
        scope='All senders' if skill['config']['scope'] == 'similar' else agent.email_for(skill['source_id']).sender,
        note='Real processed examples across saved accounts; saved classifications, no model calls or mail changes. Missing contrasts are not invented. This is not an evaluation.')


def save(agent, data):
    with agent.db:
        agent.db.execute('BEGIN IMMEDIATE')
        result = preview(agent, data)
        reviewed = data.get('reviewed')
        if data.get('token') != result['token']:
            raise ValueError('The skill or examples changed. Review them again.')
        if type(reviewed) is not list or any(type(x) is not int for x in reviewed) or set(reviewed) != {e['id'] for e in result['examples']}:
            raise ValueError('Review every displayed example before activation')
        skill = get(agent, data['skill_id'])
        if skill['family'] == 'draft':
            from .superpowers import revoke_for_skill
            revoke_for_skill(agent, skill['id'], skill['revision'], 'Draft Skill changed')
        agent.db.execute("UPDATE skills SET config=?,status='active',revision=revision+1 WHERE id=?",
                         (json.dumps(result['config']), skill['id']))
        agent.db.execute('DELETE FROM skill_examples WHERE skill_id=?', (skill['id'],))
        agent.db.executemany('INSERT INTO skill_examples VALUES(?,?,?)',
            [(skill['id'], e['id'], int(e['excluded'])) for e in result['examples']])
        agent.log(skill['source_id'], 'skill_activated', {'skill_id': skill['id'], 'config': result['config']})
    return {'saved': True}


def manage(agent, data):
    operation = data.get('operation')
    if operation not in {'pause', 'resume', 'delete'}:
        raise ValueError('Choose Pause, Resume or Delete')
    with agent.db:
        agent.db.execute('BEGIN IMMEDIATE')
        skill = get(agent, data.get('skill_id'))
        if data.get('revision') != skill['revision']:
            raise ValueError('The skill changed. Refresh before managing it.')
        if operation == 'delete':
            if skill['family'] == 'draft':
                from .superpowers import revoke_for_skill
                revoke_for_skill(agent, skill['id'], skill['revision'], 'Draft Skill deleted')
            agent.db.execute('DELETE FROM skill_examples WHERE skill_id=?', (skill['id'],))
            agent.db.execute('DELETE FROM skills WHERE id=?', (skill['id'],))
            # Remove the source feedback from active training, retaining the
            # original action/version and its append-only audit history.
            tables = {'archive': 'preference_feedback', 'attention': 'attention_feedback',
                      'labels': 'label_feedback', 'organization': 'organization_feedback', 'draft': 'draft_style_feedback'}
            parts = skill['origin'].split(':')
            if len(parts) == 2 and parts[1].isdigit() and parts[0] in tables:
                agent.db.execute('DELETE FROM ' + tables[parts[0]] + ' WHERE id=?', (int(parts[1]),))
            agent.log(skill['source_id'], 'skill_deleted', {'skill_id': skill['id'], 'origin': skill['origin']})
        else:
            expected = 'active' if operation == 'pause' else 'paused'
            if skill['status'] != expected:
                raise ValueError('Only a reviewed skill can be paused or resumed')
            if skill['family'] == 'draft' and operation == 'pause':
                from .superpowers import revoke_for_skill
                revoke_for_skill(agent, skill['id'], skill['revision'], 'Draft Skill paused')
            agent.db.execute('UPDATE skills SET status=?,revision=revision+1 WHERE id=?',
                ('paused' if operation == 'pause' else 'active', skill['id']))
            agent.log(skill['source_id'], 'skill_' + operation, {'skill_id': skill['id']})
    return {'saved': True}
