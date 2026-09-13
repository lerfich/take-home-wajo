"""User-controlled topic hierarchy and importance, independent of mail actions."""
from datetime import datetime, timezone

from .label_preferences import LABEL_KINDS, account_for


KIND_ORGANIZATION = {
    "job_application_receipt": ("Applications", "Receipt"),
    "job_interview": ("Applications", "Interview"),
    "job_outcome": ("Applications", "Outcome"),
    "work_review_request": ("Work", "Review request"),
    "work_status": ("Work", "Status update"),
    "meeting_change": ("Meetings", "Change"),
    "newsletter": ("Reading", "Newsletter"),
    "service_success": ("Services", "Success"),
    "service_failure": ("Services", "Failure"),
    "account_notice": ("Account", "Notice"),
    "settled_receipt": ("Finance", "Receipt"),
    "support_receipt": ("Support", "Receipt"),
    "support_response": ("Support", "Response"),
    "travel_confirmation": ("Travel", "Booking"),
    "community_event": ("Community", "Event"),
}


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS organization_feedback (
            id INTEGER PRIMARY KEY, action_id INTEGER NOT NULL REFERENCES actions(id),
            account TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL,
            topic TEXT NOT NULL, subtype TEXT NOT NULL, important INTEGER NOT NULL,
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS organization_rules (
            id INTEGER PRIMARY KEY, account TEXT NOT NULL, kind TEXT NOT NULL,
            scope TEXT NOT NULL, topic TEXT NOT NULL, subtype TEXT NOT NULL,
            important INTEGER NOT NULL, feedback_id INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, UNIQUE(account,kind,scope));
        CREATE TABLE IF NOT EXISTS email_organization (
            action_id INTEGER PRIMARY KEY REFERENCES actions(id), kind TEXT NOT NULL,
            topic TEXT NOT NULL, subtype TEXT NOT NULL, important INTEGER NOT NULL,
            source TEXT NOT NULL, rule_id INTEGER);
    """)


def _text(value, name):
    if type(value) is not str:
        raise ValueError(f"Enter a {name}")
    value = value.strip()
    if not value or len(value) > 60 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"Use a nonempty {name} up to 60 characters, without control characters")
    return value


def _eligible(proposal, email):
    return (proposal.label_kind in LABEL_KINDS and bool(proposal.pattern_evidence.strip())
            and proposal.pattern_evidence in email.body)


def suggested(proposal):
    topic, subtype = KIND_ORGANIZATION.get(proposal.label_kind, ("Other", "Uncategorized"))
    return {"kind": proposal.label_kind, "topic": topic, "subtype": subtype,
            "important": False, "source": "Agent suggestion", "rule_id": None}


def choose(agent, proposal, email):
    result = suggested(proposal)
    from .skills import choose as choose_skill
    skill = choose_skill(agent, 'organization', email, proposal)
    if skill:
        result.update({k: skill['config'][k] for k in ('topic', 'subtype', 'important')})
        result.update(source='Reviewed skill', rule_id=-skill['id'])
        return result
    if not _eligible(proposal, email):
        return result
    row = agent.db.execute("""SELECT * FROM organization_rules
        WHERE account=? AND kind=? AND active=1 AND scope IN ('*',?)
        AND NOT EXISTS (SELECT 1 FROM skill_legacy_links l WHERE l.family='organization' AND l.legacy_key=CAST(organization_rules.id AS TEXT))
        ORDER BY (scope='*') ASC LIMIT 1""",
        (account_for(agent, email.id), proposal.label_kind, email.sender.casefold())).fetchone()
    if row:
        result.update(topic=row["topic"], subtype=row["subtype"],
                      important=bool(row["important"]), source="Your preference",
                      rule_id=row["id"])
    return result


def register(agent, action_id, proposal, email):
    choice = choose(agent, proposal, email)
    agent.db.execute("""INSERT OR IGNORE INTO email_organization
        (action_id,kind,topic,subtype,important,source,rule_id) VALUES(?,?,?,?,?,?,?)""",
        (action_id, choice["kind"], choice["topic"], choice["subtype"],
         int(choice["important"]), choice["source"], choice["rule_id"]))
    if choice["rule_id"]:
        agent.log(action_id, "organization_preference_applied", {
            "rule_id": choice["rule_id"], "kind": choice["kind"],
            "topic": choice["topic"], "subtype": choice["subtype"],
            "important": choice["important"]})


def current(agent, action_id):
    row = agent.db.execute("SELECT * FROM email_organization WHERE action_id=?", (action_id,)).fetchone()
    if row:
        result = dict(row)
        result["important"] = bool(result["important"])
        return result
    action = agent.get(action_id)
    from .core import Proposal
    return suggested(Proposal(**action["proposal"]))


def submit(agent, action_id, topic, subtype, important, scope):
    if scope not in {"email", "similar", "sender"}:
        raise ValueError("Invalid preference scope. Refresh the email and try again.")
    if type(important) is not bool:
        raise ValueError("Choose whether this email is important")
    topic, subtype = _text(topic, "topic"), _text(subtype, "subtype")
    with agent.db:
        agent.db.execute("BEGIN IMMEDIATE")
        action = agent.get(action_id)
        from .core import Proposal
        proposal = Proposal(**action["proposal"])
        email = agent.email_for(action_id)
        if scope != "email" and not _eligible(proposal, email):
            raise ValueError("Wajo cannot identify a reliable matching context in this email. A preference for future emails was not created.")
        account = account_for(agent, email.id)
        key = "email:" + email.id if scope == "email" else "*" if scope == "similar" else email.sender.casefold()
        cursor = agent.db.execute("""INSERT INTO organization_feedback
            (action_id,account,kind,scope,topic,subtype,important,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (action_id, account, proposal.label_kind, key, topic, subtype, int(important),
             datetime.now(timezone.utc).isoformat()))
        rule_id = None
        if scope != "email":
            agent.db.execute("""INSERT INTO organization_rules
                (account,kind,scope,topic,subtype,important,feedback_id,active)
                VALUES(?,?,?,?,?,?,?,1)
                ON CONFLICT(account,kind,scope) DO UPDATE SET
                topic=excluded.topic,subtype=excluded.subtype,important=excluded.important,
                feedback_id=excluded.feedback_id,active=1""",
                (account, proposal.label_kind, key, topic, subtype, int(important), cursor.lastrowid))
            rule_id = agent.db.execute("SELECT id FROM organization_rules WHERE account=? AND kind=? AND scope=?",
                                       (account, proposal.label_kind, key)).fetchone()["id"]
        agent.db.execute("""INSERT INTO email_organization
            (action_id,kind,topic,subtype,important,source,rule_id) VALUES(?,?,?,?,?,'Reviewed by you',?)
            ON CONFLICT(action_id) DO UPDATE SET kind=excluded.kind,topic=excluded.topic,
            subtype=excluded.subtype,important=excluded.important,source=excluded.source,rule_id=excluded.rule_id""",
            (action_id, proposal.label_kind, topic, subtype, int(important), rule_id))
        agent.log(action_id, "organization_reviewed", {"feedback_id": cursor.lastrowid,
                  "scope": key, "topic": topic, "subtype": subtype, "important": important})
    return current(agent, action_id)


def pause(agent, rule_id):
    with agent.db:
        cursor = agent.db.execute("UPDATE organization_rules SET active=0 WHERE id=?", (rule_id,))
        if not cursor.rowcount:
            raise ValueError("Unknown organization preference")
        agent.log(None, "organization_rule_paused", {"rule_id": rule_id})
    return {"paused": True}
