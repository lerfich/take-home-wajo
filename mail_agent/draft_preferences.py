"""Explicit draft-style learning; never grants permission to send."""
from dataclasses import replace
from datetime import datetime, timezone
import re

from .label_preferences import LABEL_KINDS, account_for


GREETINGS = re.compile(r"^(hi|hello|hey|dear|привет|здравствуйте)\b", re.I)
SIGNOFFS = re.compile(r"^(best|thanks|thank you|regards|sincerely|спасибо|с уважением)[,!.]?$", re.I)


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS draft_style_feedback (
            id INTEGER PRIMARY KEY, action_id INTEGER NOT NULL REFERENCES actions(id),
            revision INTEGER NOT NULL, account TEXT NOT NULL, kind TEXT NOT NULL,
            scope TEXT NOT NULL, length TEXT NOT NULL, greeting TEXT NOT NULL,
            signoff TEXT NOT NULL, original_words INTEGER NOT NULL,
            edited_words INTEGER NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(action_id,revision,scope));
        CREATE TABLE IF NOT EXISTS draft_style_rules (
            id INTEGER PRIMARY KEY, account TEXT NOT NULL, kind TEXT NOT NULL,
            scope TEXT NOT NULL, length TEXT NOT NULL, greeting TEXT NOT NULL,
            signoff TEXT NOT NULL, feedback_id INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, UNIQUE(account,kind,scope));
        CREATE TABLE IF NOT EXISTS draft_style_applications (
            action_id INTEGER PRIMARY KEY REFERENCES actions(id), rule_id INTEGER NOT NULL,
            base_words INTEGER NOT NULL, styled_words INTEGER NOT NULL,
            status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS draft_edit_versions (
            action_id INTEGER NOT NULL REFERENCES actions(id), revision INTEGER NOT NULL,
            text TEXT NOT NULL, PRIMARY KEY(action_id,revision));
    """)


def _words(text):
    return len(re.findall(r"\b[\w'-]+\b", text, re.UNICODE))


def _lines(text):
    return [line.strip() for line in text.strip().splitlines() if line.strip()]


def derive(original, edited):
    original_words, edited_words = _words(original), _words(edited)
    if edited_words <= 35:
        length = "concise"
    elif edited_words <= 70:
        length = "brief"
    else:
        length = "standard"
    lines = _lines(edited)
    greeting = "include" if lines and GREETINGS.search(lines[0]) else "omit"
    signoff = "include" if lines and SIGNOFFS.search(lines[-1]) else "omit"
    return {"length": length, "greeting": greeting, "signoff": signoff,
            "original_words": original_words, "edited_words": edited_words}


def describe(style):
    length = {"concise": "concise (up to about 35 words)",
              "brief": "brief (up to about 70 words)",
              "standard": "standard length"}[style["length"]]
    greeting = "with a short greeting" if style["greeting"] == "include" else "without a greeting"
    signoff = "with a short sign-off" if style["signoff"] == "include" else "without a sign-off"
    return f"Write {length}, {greeting}, and {signoff}."


def preview(agent, action_id, revision):
    action = agent.get(action_id)
    if (action["status"] != "pending" or action["revision"] != revision
            or action["proposal"]["action"] not in {"draft", "send"} or revision < 2):
        raise ValueError("Edit and save a draft before learning its style")
    from .core import Proposal
    proposal = Proposal(**action["proposal"])
    email = agent.email_for(action_id)
    if (proposal.label_kind not in LABEL_KINDS or not proposal.pattern_evidence.strip()
            or proposal.pattern_evidence not in email.body):
        raise ValueError("This draft has no supported, evidenced situation type")
    first = agent.db.execute("SELECT text FROM draft_edit_versions WHERE action_id=? ORDER BY revision LIMIT 1",
                             (action_id,)).fetchone()
    edited = agent.db.execute("SELECT text FROM draft_edit_versions WHERE action_id=? AND revision=?",
                              (action_id, revision)).fetchone()
    if not first or not edited:
        raise ValueError("The saved draft version is unavailable for style review.")
    style = derive(first["text"], edited["text"])
    return {**style, "summary": describe(style),
            "basis": "confirmed" if first["text"] == edited["text"] else "edited"}


def record_version(agent, action_id, revision, text):
    agent.db.execute("INSERT OR IGNORE INTO draft_edit_versions VALUES(?,?,?)",
                     (action_id, revision, text))


def save(agent, action_id, revision, scope):
    if scope not in {"similar", "sender"}:
        raise ValueError("Choose similar drafts or this sender")
    with agent.db:
        agent.db.execute("BEGIN IMMEDIATE")
        action = agent.get(action_id)
        from .core import Proposal
        proposal = Proposal(**action["proposal"])
        email = agent.email_for(action_id)
        if (proposal.label_kind not in LABEL_KINDS or not proposal.pattern_evidence.strip()
                or proposal.pattern_evidence not in email.body):
            raise ValueError("This draft has no supported, evidenced situation type. Its style cannot train a future rule.")
        style = preview(agent, action_id, revision)
        account = account_for(agent, email.id)
        key = "*" if scope == "similar" else email.sender.casefold()
        cursor = agent.db.execute("""INSERT INTO draft_style_feedback
            (action_id,revision,account,kind,scope,length,greeting,signoff,
             original_words,edited_words,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (action_id, revision, account, proposal.label_kind, key, style["length"],
             style["greeting"], style["signoff"], style["original_words"],
             style["edited_words"], datetime.now(timezone.utc).isoformat()))
        agent.db.execute("""INSERT INTO draft_style_rules
            (account,kind,scope,length,greeting,signoff,feedback_id,active)
            VALUES(?,?,?,?,?,?,?,1) ON CONFLICT(account,kind,scope) DO UPDATE SET
            length=excluded.length,greeting=excluded.greeting,signoff=excluded.signoff,
            feedback_id=excluded.feedback_id,active=1""",
            (account, proposal.label_kind, key, style["length"], style["greeting"],
             style["signoff"], cursor.lastrowid))
        agent.log(action_id, "draft_style_saved", {"feedback_id": cursor.lastrowid,
                  "scope": key, "kind": proposal.label_kind, "summary": style["summary"]})
    return {**style, "scope": key, "saved": True}


def choose(agent, proposal, email):
    if (proposal.action not in {"draft", "send"} or proposal.suspicious or proposal.needs_human
            or proposal.label_kind not in LABEL_KINDS or not proposal.pattern_evidence.strip()
            or proposal.pattern_evidence not in email.body):
        return None
    row = agent.db.execute("""SELECT * FROM draft_style_rules WHERE account=? AND kind=?
        AND active=1 AND scope IN ('*',?) ORDER BY (scope='*') ASC LIMIT 1""",
        (account_for(agent, email.id), proposal.label_kind, email.sender.casefold())).fetchone()
    return dict(row) if row else None


def apply(agent, proposal, email):
    rule = choose(agent, proposal, email)
    if not rule or not hasattr(agent.proposer, "rewrite_draft"):
        return proposal, None
    base = proposal.text
    try:
        rewritten = agent.proposer.rewrite_draft(email, proposal, rule)
        if type(rewritten) is not str or not rewritten.strip() or len(rewritten) > 20000 or "\x00" in rewritten:
            raise ValueError("Invalid styled draft")
        return replace(proposal, text=rewritten), {"rule": rule, "base_words": _words(base),
                                                   "styled_words": _words(rewritten), "status": "applied"}
    except Exception:
        return proposal, {"rule": rule, "base_words": _words(base),
                          "styled_words": _words(base), "status": "fallback"}


def register_application(agent, action_id, application):
    if not application:
        return
    agent.db.execute("INSERT INTO draft_style_applications VALUES(?,?,?,?,?)",
                     (action_id, application["rule"]["id"], application["base_words"],
                      application["styled_words"], application["status"]))
    agent.log(action_id, "draft_style_" + application["status"], {
        "rule_id": application["rule"]["id"], "base_words": application["base_words"],
        "styled_words": application["styled_words"]})


def pause(agent, rule_id):
    with agent.db:
        cursor = agent.db.execute("UPDATE draft_style_rules SET active=0 WHERE id=?", (rule_id,))
        if not cursor.rowcount:
            raise ValueError("Unknown draft style preference")
        agent.log(None, "draft_style_rule_paused", {"rule_id": rule_id})
    return {"paused": True}
