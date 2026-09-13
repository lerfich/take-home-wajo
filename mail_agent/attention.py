"""Explicit user requests to surface mail, independent of organization and autonomy."""
from datetime import datetime, timezone
from contextlib import nullcontext

from .label_preferences import account_for


ATTENTION_CUES = {
    "personal_commitment": "Personal future plan or commitment",
    "account_security": "Account security or access change",
    "decision_required": "Decision or substantive response required",
    "deadline_consequence": "Deadline with a consequence if missed",
    "financial_commitment": "Money or financial commitment",
    "service_impact": "Service failure affecting current work",
    "schedule_change": "Change to an existing meeting or schedule",
    "none": "No distinct attention cue",
}

LEGACY_KIND_CUES = {
    "travel_confirmation": "personal_commitment",
    "job_interview": "personal_commitment",
    "account_notice": "account_security",
    "work_review_request": "decision_required",
    "service_failure": "service_impact",
    "meeting_change": "schedule_change",
}


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
    for table in ("attention_rules", "attention_feedback"):
        columns = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        if "cue" not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN cue TEXT NOT NULL DEFAULT ''")
    for kind, cue in LEGACY_KIND_CUES.items():
        db.execute("UPDATE attention_rules SET cue=? WHERE cue='' AND kind=?", (cue, kind))
        db.execute("UPDATE attention_feedback SET cue=? WHERE cue='' AND kind=?", (cue, kind))


def cue_for(email, proposal):
    """Prefer the model's semantic cue; map old v7 examples without rewriting history."""
    evidence = proposal.attention_evidence.strip()
    if proposal.attention_cue in {"none", "unknown"}:
        return proposal.attention_cue
    if proposal.attention_cue in ATTENTION_CUES and proposal.attention_cue != "none":
        if evidence and evidence in email.body:
            return proposal.attention_cue
        return "unknown"
    legacy_evidence = proposal.pattern_evidence.strip()
    if (proposal.label_kind in LEGACY_KIND_CUES and legacy_evidence
            and legacy_evidence in email.body):
        return LEGACY_KIND_CUES[proposal.label_kind]
    return "unknown"


def effective_rule(agent, email, proposal):
    account=account_for(agent,email.id)
    rules=list(agent.db.execute("SELECT * FROM attention_rules WHERE account=? ORDER BY rowid DESC",(account,)))
    cue = cue_for(email, proposal)
    # An explicit per-email choice remains more specific than a future skill.
    for r in rules:
        if r['scope'] == 'email:' + email.id:
            return dict(r)
    from .skills import choose
    skill = choose(agent, 'attention', email, proposal)
    if skill:
        return {'account': account, 'cue': cue, 'kind': proposal.label_kind,
                'scope': '*' if skill['config']['scope'] == 'similar' else email.sender.casefold(),
                'enabled': int(skill['config']['enabled']), 'skill_id': skill['id']}
    for scope in ('email:'+email.id, email.sender.casefold(), '*'):
        for r in rules:
            from .skills import legacy_managed
            import json
            if not scope.startswith('email:') and legacy_managed(agent, 'attention', json.dumps([r['account'], r['kind'], r['scope']])):
                continue
            if r['scope'] == scope and (scope.startswith('email:') or r['cue'] == cue):
                return dict(r)
    return None


def matches(agent, email, proposal):
    rule = effective_rule(agent, email, proposal)
    return bool(rule and rule['enabled'])


def set_rule(agent, action_id, enabled, scope, *, transaction=True):
    from .core import Proposal
    if type(enabled) is not bool or scope not in {'email','similar','sender'}:
        raise ValueError('Choose a valid attention setting and scope')
    action=agent.get(action_id);email=agent.email_for(action_id);p=Proposal(**action['proposal'])
    cue=cue_for(email,p)
    if scope!='email' and cue not in set(ATTENTION_CUES) - {'none'}:
        raise ValueError('This email has no evidenced attention cue. A preference for future emails was not created.')
    key='email:'+email.id if scope=='email' else '*' if scope=='similar' else email.sender.casefold()
    with agent.db if transaction else nullcontext():
        account=account_for(agent,email.id)
        duplicate=agent.db.execute(
            '''SELECT id, action_id, enabled FROM attention_feedback
               WHERE account=? AND scope=? AND (cue=? OR scope LIKE 'email:%')
               ORDER BY id DESC LIMIT 1''', (account,key,cue)).fetchone()
        if duplicate is not None and (duplicate['action_id'] != action_id or duplicate['enabled'] != int(enabled)):
            duplicate = None
        # Several concrete situations can express the same user-facing reason
        # (for example, travel and an interview are both personal commitments).
        # Keep their effective rule state aligned even though legacy rows retain
        # the original kind for auditability.
        agent.db.execute(
            "UPDATE attention_rules SET enabled=? WHERE account=? AND scope=? AND (cue=? OR scope LIKE 'email:%')",
            (int(enabled),account,key,cue))
        agent.db.execute('INSERT OR REPLACE INTO attention_rules(account,kind,scope,enabled,cue) VALUES(?,?,?,?,?)',
                         (account,p.label_kind,key,int(enabled),cue))
        if duplicate is not None:
            if enabled:
                agent.db.execute("INSERT OR REPLACE INTO attention_items VALUES(?,'You asked to see this email',0)",(action_id,))
            else:
                agent.db.execute('UPDATE attention_items SET seen=1 WHERE action_id=?',(action_id,))
            return {'saved':True,'unchanged':True,'feedback_id':duplicate['id']}
        cursor=agent.db.execute(
            'INSERT INTO attention_feedback(action_id,account,kind,scope,enabled,created_at,cue) VALUES(?,?,?,?,?,?,?)',
            (action_id,account,p.label_kind,key,int(enabled),datetime.now(timezone.utc).isoformat(),cue))
        if enabled:
            agent.db.execute("INSERT OR REPLACE INTO attention_items VALUES(?,'You asked to see this email',0)",(action_id,))
        else:
            agent.db.execute('UPDATE attention_items SET seen=1 WHERE action_id=?',(action_id,))
        agent.log(action_id,'attention_preference_changed',
                  {'enabled':enabled,'scope':key,'kind':p.label_kind,'cue':cue,'feedback_id':cursor.lastrowid})
    return {'saved':True,'feedback_id':cursor.lastrowid}
