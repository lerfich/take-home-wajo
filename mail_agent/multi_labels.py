"""Bounded additional labels and exact user resolution of label-set conflicts."""
import json

from .label_preferences import normalize_label


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS label_targets (
        action_id INTEGER PRIMARY KEY, labels TEXT NOT NULL, skill_id INTEGER);
      CREATE TABLE IF NOT EXISTS label_conflicts (
        action_id INTEGER PRIMARY KEY, candidates TEXT NOT NULL, existing_labels TEXT NOT NULL,
        selected TEXT NOT NULL DEFAULT '[]', revision INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open');
    ''')


def current(agent, email_id):
    return sorted(r['label'] for r in agent.db.execute('SELECT label FROM labels WHERE email_id=?', (email_id,))
                  if r['label'].startswith('AI: '))


def targets(agent, action_id, default):
    row = agent.db.execute('SELECT labels FROM label_targets WHERE action_id=?', (action_id,)).fetchone()
    return json.loads(row['labels']) if row else [default]


def conflict(agent, action_id, wanted, existing=None):
    action = agent.get(action_id)
    existing = current(agent, action['email_id']) if existing is None else existing
    candidates = sorted(set(existing) | set(wanted))
    agent.db.execute('''INSERT INTO label_conflicts(action_id,candidates,existing_labels,revision)
        VALUES(?,?,?,?) ON CONFLICT(action_id) DO UPDATE SET candidates=excluded.candidates,
        existing_labels=excluded.existing_labels,revision=excluded.revision,status='open',selected='[]' ''',
        (action_id, json.dumps(candidates), json.dumps(existing), action['revision']))
    agent.log(action_id, 'label_conflict', {'candidates': candidates, 'existing': existing})
    return {'conflict': True, 'action_id': action_id, 'candidates': candidates}


def resolve(agent, data):
    from .core import Proposal, decide
    selected = data.get('labels')
    if type(selected) is not list or len(selected) != 2 or len(set(selected)) != 2:
        raise ValueError('Choose exactly two additional labels')
    selected = sorted(normalize_label(x) for x in selected)
    with agent.db:
        agent.db.execute('BEGIN IMMEDIATE')
        row = agent.db.execute('SELECT * FROM label_conflicts WHERE action_id=?', (data.get('action_id'),)).fetchone()
        if not row or row['status'] != 'open' or data.get('revision') != row['revision']:
            raise ValueError('The label conflict changed. Refresh and choose again.')
        action = agent.get(row['action_id'])
        if (action['revision'] != row['revision'] or decide(Proposal(**action['proposal'])).status != 'ready'
                or action['proposal']['action'] != 'label' or action['status'] not in {'executed', 'pending', 'error'}):
            raise ValueError('This action is not ready for label resolution')
        if not set(selected) <= set(json.loads(row['candidates'])):
            raise ValueError('Choose labels from the displayed conflict')
        # Local state may have changed since the conflict was displayed.
        if action['transport'] != 'gmail' and current(agent, action['email_id']) != json.loads(row['existing_labels']):
            raise ValueError('Existing labels changed. Review the conflict again.')
        revision = action['revision'] + 1
        agent.db.execute("UPDATE label_conflicts SET selected=?,revision=?,status='saving' WHERE action_id=?",
            (json.dumps(selected), revision, action['id']))
        agent.db.execute('UPDATE actions SET revision=? WHERE id=?', (revision, action['id']))
        if action['transport'] == 'gmail':
            agent.queue_gmail(action['id'], 'labels-set:' + str(revision), approved=True)
        else:
            finish(agent, action['id'])
        agent.log(action['id'], 'label_conflict_resolved', {'selected': selected, 'revision': revision})
    return {'saved': True}


def finish(agent, action_id):
    row = agent.db.execute('SELECT * FROM label_conflicts WHERE action_id=?', (action_id,)).fetchone()
    selected = json.loads(row['selected']); action = agent.get(action_id)
    for label in current(agent, action['email_id']):
        if label not in selected:
            agent.db.execute('DELETE FROM labels WHERE email_id=? AND label=?', (action['email_id'], label))
    agent.db.executemany('INSERT OR IGNORE INTO labels VALUES(?,?)', [(action['email_id'], x) for x in selected])
    proposal = dict(action['proposal'], label=selected[0])
    agent.db.execute("UPDATE actions SET status='executed',proposal=? WHERE id=?", (json.dumps(proposal), action_id))
    agent.db.execute("UPDATE label_reviews SET current_label=?,status='reviewed',preference_id=NULL,basis='Your conflict resolution' WHERE action_id=?", (selected[0], action_id))
    agent.db.execute("UPDATE label_conflicts SET status='resolved' WHERE action_id=?", (action_id,))
    agent.db.execute('INSERT OR REPLACE INTO label_targets VALUES(?,?,NULL)', (action_id, json.dumps(selected)))
    from .skills import suggest
    # Same transaction as the verified result; suggestion itself does not activate.
    suggest(agent, 'labels', action_id, {'labels': selected}, 'labels-set:' + str(action_id) + ':' + str(row['revision']), transaction=False)


def run_resolution(agent, executor, candidate, check_only):
    from .gmail_executor import ScopeError
    from .core import Proposal, decide
    op = dict(candidate)
    if op['status'] not in ({'unknown', 'error'} if check_only else {'queued'}):
        raise ValueError('This label operation is unavailable')
    try:
        with agent.db:
            agent.db.execute('BEGIN IMMEDIATE')
            action = agent.get(op['action_id'])
            row = agent.db.execute('SELECT * FROM label_conflicts WHERE action_id=?', (action['id'],)).fetchone()
            binding = agent.db.execute('SELECT * FROM gmail_bindings WHERE email_id=?', (action['email_id'],)).fetchone()
            if (not row or not binding or not op['approved'] or row['status'] != 'saving' or
                    row['revision'] != op['revision'] or action['revision'] != op['revision'] or
                    op['operation'] != 'labels-set:' + str(op['revision']) or action['transport'] != 'gmail' or
                    action['proposal']['action'] != 'label' or decide(Proposal(**action['proposal'])).status != 'ready'):
                raise ScopeError('The saved label selection or version changed')
            selected = json.loads(row['selected']); previous = json.loads(row['existing_labels'])
            if len(selected) != 2 or any(normalize_label(x) != x for x in selected):
                raise ScopeError('Invalid saved label selection')
            agent.db.execute("UPDATE gmail_operations SET status='processing' WHERE id=?", (op['id'],))
        labels, ids = executor.inspect(dict(binding))
        def names():
            return sorted(x['name'] for x in labels if x.get('type') == 'user' and x['id'] in ids and x['name'].startswith('AI: '))
        verified = names() == selected
        if not verified and not check_only:
            if names() != previous:
                raise ScopeError('Gmail labels changed after your choice. No labels were replaced.')
            add = []
            for name in selected:
                ident = next((x['id'] for x in labels if x['name'] == name and x.get('type') == 'user'), None)
                if ident is None:
                    ident = executor.api.users().labels().create(userId='me', body={
                        'name': name, 'labelListVisibility': 'labelShow', 'messageListVisibility': 'show'}).execute()['id']
                add.append(ident)
            remove = [x['id'] for x in labels if x['name'] in previous and x['name'] not in selected and x['id'] in ids]
            executor.api.users().messages().modify(userId='me', id=binding['message_id'], body={
                'addLabelIds': add, 'removeLabelIds': remove}).execute()
            labels, ids = executor.inspect(dict(binding)); verified = names() == selected
        with agent.db:
            if verified:
                finish(agent, action['id'])
                agent.db.execute("UPDATE gmail_operations SET status='done',error='' WHERE id=?", (op['id'],))
            else:
                agent.db.execute("UPDATE gmail_operations SET status='unknown',error='Label set not confirmed; check without retrying.' WHERE id=?", (op['id'],))
                agent.db.execute("UPDATE actions SET status='unknown' WHERE id=?", (action['id'],))
    except Exception as exc:
        agent.db.rollback()
        status = 'error' if isinstance(exc, ScopeError) else 'unknown'
        reason = str(exc) if isinstance(exc, ScopeError) else 'Label result uncertain. Read-only check required; no automatic retry.'
        with agent.db:
            agent.db.execute('UPDATE gmail_operations SET status=?,error=? WHERE id=?', (status, reason, op['id']))
            agent.db.execute('UPDATE actions SET status=? WHERE id=?', (status, op['action_id']))
    return True
