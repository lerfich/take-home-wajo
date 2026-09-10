"""Explicit user requests to surface mail, independent of organization and autonomy."""
from datetime import datetime, timezone

from .label_preferences import account_for, LABEL_KINDS


def initialize(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS attention_rules (
        account TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL,
        enabled INTEGER NOT NULL, PRIMARY KEY(account,kind,scope));
      CREATE TABLE IF NOT EXISTS attention_items (
        action_id INTEGER PRIMARY KEY, reason TEXT NOT NULL, seen INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS attention_feedback (
        id INTEGER PRIMARY KEY, action_id INTEGER NOT NULL, account TEXT NOT NULL,
        kind TEXT NOT NULL, scope TEXT NOT NULL, enabled INTEGER NOT NULL,
        created_at TEXT NOT NULL);
    """)


def _evidenced(email, proposal):
    evidence = proposal.pattern_evidence.strip()
    return proposal.label_kind in LABEL_KINDS and bool(evidence) and evidence in email.body


def matches(agent, email, proposal):
    account=account_for(agent,email.id)
    rules=list(agent.db.execute("SELECT * FROM attention_rules WHERE account=? AND enabled=1",(account,)))
    for r in rules:
        if r['scope']=='email:'+email.id:
            return True
        if r['kind']==proposal.label_kind and _evidenced(email, proposal):
            if r['scope'] in ('*',email.sender.casefold()):
                return True
    return False


def set_rule(agent, action_id, enabled, scope):
    from .core import Proposal
    if type(enabled) is not bool or scope not in {'email','similar','sender'}:
        raise ValueError('Choose a valid attention setting and scope')
    action=agent.get(action_id);email=agent.email_for(action_id);p=Proposal(**action['proposal'])
    if scope!='email' and not _evidenced(email, p):
        raise ValueError('This email has no evidenced situation type; choose this email only')
    key='email:'+email.id if scope=='email' else '*' if scope=='similar' else email.sender.casefold()
    with agent.db:
        agent.db.execute('INSERT OR REPLACE INTO attention_rules VALUES(?,?,?,?)',
                         (account_for(agent,email.id),p.label_kind,key,int(enabled)))
        cursor=agent.db.execute(
            'INSERT INTO attention_feedback(action_id,account,kind,scope,enabled,created_at) VALUES(?,?,?,?,?,?)',
            (action_id,account_for(agent,email.id),p.label_kind,key,int(enabled),datetime.now(timezone.utc).isoformat()))
        if enabled:
            agent.db.execute("INSERT OR REPLACE INTO attention_items VALUES(?,'You asked to see this email',0)",(action_id,))
        else:
            agent.db.execute('UPDATE attention_items SET seen=1 WHERE action_id=?',(action_id,))
        agent.log(action_id,'attention_preference_changed',
                  {'enabled':enabled,'scope':key,'kind':p.label_kind,'feedback_id':cursor.lastrowid})
    return {'saved':True,'feedback_id':cursor.lastrowid}
