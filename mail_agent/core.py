"""Shared policy and execution core. Only the local mailbox is implemented."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Protocol


@dataclass(frozen=True)
class Email:
    id: str
    sender: str
    subject: str
    body: str


@dataclass(frozen=True)
class Proposal:
    action: str
    reason: str
    label: str = ""
    text: str = ""
    recipient: str = ""
    notify: bool = False
    suspicious: bool = False
    needs_human: bool = False
    pattern: str = "unknown"
    requires_action: bool = True
    has_deadline: bool = True
    significant_change: bool = True
    sensitive: bool = True
    pattern_evidence: str = ""


class Proposer(Protocol):
    def propose(self, email: Email) -> Proposal: ...


@dataclass(frozen=True)
class Decision:
    autonomy: str
    safety: str
    status: str
    reason: str


def decide(p: Proposal) -> Decision:
    """Policy never accepts permissions or autonomy from the proposer."""
    if p.suspicious:
        return Decision("notify", "blocked", "blocked", "Suspected instruction injection")
    if p.action not in {"label", "archive", "draft", "send", "none"}:
        return Decision("escalate", "blocked", "blocked", "Unsupported action; manual review required")
    if p.needs_human:
        return Decision("escalate", "review_required", "escalated", "Human judgment required")
    if p.action == "label" and (not p.label.startswith("AI: ") or not p.label[4:].strip()):
        return Decision("notify", "blocked", "blocked", "Agent labels must start with AI: ")
    if p.action in {"draft", "send"} and not p.text.strip():
        return Decision("notify", "blocked", "blocked", "Empty response text")
    if p.action == "send" and (not p.recipient or "@" not in p.recipient or any(c in p.recipient for c in "\r\n,;")):
        return Decision("notify", "blocked", "blocked", "A single explicit recipient is required")
    if p.action in {"archive", "send"}:
        return Decision("ask", "confirmation_required", "pending", "Explicit approval required in baseline mode")
    return Decision("notify" if p.notify else "silent", "allowed", "ready", "Initial permission")


def validate(proposal: Proposal) -> None:
    if type(proposal) is not Proposal:
        raise ValueError("Invalid proposal type")
    for name in ("action", "reason", "label", "text", "recipient", "pattern", "pattern_evidence"):
        if type(getattr(proposal, name)) is not str:
            raise ValueError(f"Invalid {name}")
    for name in ("notify", "suspicious", "needs_human", "requires_action", "has_deadline", "significant_change", "sensitive"):
        if type(getattr(proposal, name)) is not bool:
            raise ValueError(f"Invalid {name}")
    if proposal.pattern not in PATTERNS | {"unknown"}:
        raise ValueError("Invalid semantic pattern")


# Communicative purpose, not sender/domain, subject keywords or job-search templates.
PATTERNS = {"acknowledgement_only", "periodic_digest", "routine_success", "informational_reference"}


def learnable(p: Proposal, email: Email) -> bool:
    return (p.action == "archive" and p.pattern in PATTERNS and not any((
        p.suspicious, p.needs_human, p.requires_action, p.has_deadline,
        p.significant_change, p.sensitive, p.notify))
        and bool(p.pattern_evidence.strip()) and p.pattern_evidence in email.body)


class Agent:
    def __init__(self, db_path: str, proposer: Proposer):
        self.proposer = proposer
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS emails (
                id TEXT PRIMARY KEY, sender TEXT NOT NULL, subject TEXT NOT NULL,
                body TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY, email_id TEXT NOT NULL UNIQUE REFERENCES emails(id),
                proposal TEXT NOT NULL, autonomy TEXT NOT NULL, safety TEXT NOT NULL,
                status TEXT NOT NULL, reason TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS labels (
                email_id TEXT REFERENCES emails(id), label TEXT, PRIMARY KEY(email_id,label));
            CREATE TABLE IF NOT EXISTS drafts (
                action_id INTEGER PRIMARY KEY REFERENCES actions(id), email_id TEXT, text TEXT);
            CREATE TABLE IF NOT EXISTS sent (
                action_id INTEGER PRIMARY KEY REFERENCES actions(id), email_id TEXT,
                recipient TEXT, subject TEXT, text TEXT);
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY, action_id INTEGER REFERENCES actions(id),
                event TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS preference_feedback (
                id INTEGER PRIMARY KEY, action_id INTEGER NOT NULL REFERENCES actions(id),
                scope TEXT NOT NULL, pattern TEXT NOT NULL, positive INTEGER NOT NULL,
                created_at TEXT NOT NULL, UNIQUE(action_id, positive));
            CREATE TABLE IF NOT EXISTS archive_rules (
                scope TEXT NOT NULL, pattern TEXT NOT NULL, mode TEXT NOT NULL,
                PRIMARY KEY(scope, pattern));
        """)

    def email_for(self, action_id):
        row = self.db.execute("SELECT e.id,e.sender,e.subject,e.body FROM emails e JOIN actions a ON a.email_id=e.id WHERE a.id=?", (action_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown action")
        return Email(**dict(row))

    def preference(self, p, email):
        """Explicit keep rules override learning; sender experience overrides general."""
        sender = email.sender.strip().casefold()
        rules = list(self.db.execute("SELECT * FROM archive_rules WHERE scope IN ('*',?) AND pattern IN ('*',?)", (sender, p.pattern)))
        if rules:
            return {"mode": "keep", "rules": [dict(r) for r in rules]}
        if not learnable(p, email):
            return {"mode": "ask", "reason": "Not eligible for archive learning"}
        rows = list(self.db.execute("SELECT * FROM preference_feedback WHERE scope IN ('*',?) AND pattern=? ORDER BY id", (sender, p.pattern)))
        scope = sender if any(r["scope"] == sender for r in rows) else "*"
        scoped = [r for r in rows if r["scope"] == scope]
        last_negative = max((r["id"] for r in scoped if not r["positive"]), default=0)
        evidence = [r["id"] for r in scoped if r["positive"] and r["id"] > last_negative]
        return {"mode": "notify" if len(evidence) >= 3 else "ask", "scope": scope,
                "pattern": p.pattern, "approval_ids": evidence, "threshold": 3}

    def set_archive_rule(self, sender="*", pattern="*", keep=True):
        """Trusted local user only. Exact sender, never inferred company affiliation."""
        if not sender.strip() or pattern not in PATTERNS | {"*"}:
            raise ValueError("Invalid rule scope or pattern")
        scope = sender.strip().casefold()
        with self.db:
            if keep:
                self.db.execute("INSERT OR REPLACE INTO archive_rules VALUES(?,?,'keep')", (scope, pattern))
            else:
                self.db.execute("DELETE FROM archive_rules WHERE scope=? AND pattern=?", (scope, pattern))
            self.log(None, "archive_rule_changed", {"scope": scope, "pattern": pattern, "keep": keep})
        return {"scope": scope, "pattern": pattern, "keep": keep}

    def record_feedback(self, action_id, positive, scope):
        if scope not in {"general", "sender"}:
            raise ValueError("Feedback scope must be general or sender")
        row = self.get(action_id)
        email = self.email_for(action_id)
        p = Proposal(**row["proposal"])
        if not learnable(p, email):
            return  # Approval still works, but uncertain/risky proposals cannot train.
        key = "*" if scope == "general" else email.sender.strip().casefold()
        self.db.execute("INSERT INTO preference_feedback(action_id,scope,pattern,positive,created_at) VALUES(?,?,?,?,?)",
                        (action_id, key, p.pattern, int(positive), datetime.now(timezone.utc).isoformat()))
        self.log(action_id, "preference_feedback", {"scope": key, "pattern": p.pattern, "positive": positive})

    def correct_archive(self, action_id, scope="general"):
        """Restore the local inbox and suspend learning in the selected scope."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.get(action_id)
            if row["status"] != "executed" or row["proposal"]["action"] != "archive":
                raise ValueError("Only an executed archive can be corrected")
            self.record_feedback(action_id, False, scope)
            self.db.execute("UPDATE emails SET archived=0 WHERE id=?", (row["email_id"],))
            self.db.execute("UPDATE actions SET status='corrected' WHERE id=?", (action_id,))
            self.log(action_id, "archive_corrected", {"scope": scope})
        return self.get(action_id)

    def close(self):
        self.db.close()

    def log(self, action_id: int, event: str, details: dict):
        self.db.execute("INSERT INTO audit(action_id,event,details,created_at) VALUES(?,?,?,?)",
                        (action_id, event, json.dumps(details, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))

    def get(self, action_id: int) -> dict:
        row = self.db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown action")
        result = dict(row)
        result["proposal"] = json.loads(result["proposal"])
        return result

    def ingest(self, email: Email) -> dict:
        """An incoming event triggers processing; no separate 'analyze' command."""
        existing = self.db.execute("SELECT id FROM actions WHERE email_id=?", (email.id,)).fetchone()
        if existing:
            saved = self.db.execute("SELECT id,sender,subject,body FROM emails WHERE id=?", (email.id,)).fetchone()
            if dict(saved) != asdict(email):
                raise ValueError("An existing email ID cannot be reused for different content")
            return self.get(existing["id"])
        try:
            proposal = self.proposer.propose(email)
            validate(proposal)
            decision = decide(proposal)
            if decision.status == "pending" and proposal.action == "archive":
                pref = self.preference(proposal, email)
                if pref["mode"] == "keep":
                    decision = Decision("silent", "allowed", "skipped", "Explicit preference: keep in inbox")
                elif pref["mode"] == "notify":
                    decision = Decision("notify", "allowed", "ready", "Learned archive preference")
        except Exception:
            # Do not log arbitrary provider exceptions: they can contain secrets.
            proposal = Proposal("none", "Proposal unavailable or invalid")
            decision = Decision("escalate", "review_required", "error", "Model/provider failure; no action executed")
        with self.db:
            self.db.execute("INSERT INTO emails(id,sender,subject,body) VALUES(?,?,?,?)",
                            (email.id, email.sender, email.subject, email.body))
            cursor = self.db.execute("""INSERT INTO actions(email_id,proposal,autonomy,safety,status,reason)
                                      VALUES(?,?,?,?,?,?)""",
                                     (email.id, json.dumps(asdict(proposal), ensure_ascii=False),
                                      decision.autonomy, decision.safety, decision.status, decision.reason))
            action_id = cursor.lastrowid
            self.log(action_id, "decision", asdict(decision))
            if decision.status == "ready":
                self._execute(action_id)
            elif decision.status in {"blocked", "escalated", "error"}:
                self.log(action_id, "notification", {"reason": decision.reason, "requires_response": decision.autonomy == "escalate"})
        return self.get(action_id)

    def approve(self, action_id: int, revision: int, scope="general") -> dict:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.get(action_id)
            if row["status"] != "pending" or row["revision"] != revision:
                raise ValueError("Approval is stale or action is not pending")
            proposal = Proposal(**row["proposal"])
            validate(proposal)
            if decide(proposal).status != "pending":
                raise ValueError("Policy no longer permits approval")
            self.log(action_id, "approved", {"revision": revision, "proposal": row["proposal"]})
            self._execute(action_id, approved=True)
            self.record_feedback(action_id, True, scope)
        return self.get(action_id)

    def reject(self, action_id: int, revision: int, scope="general") -> dict:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.get(action_id)
            if row["status"] != "pending" or row["revision"] != revision:
                raise ValueError("Rejection is stale or action is not pending")
            self.db.execute("UPDATE actions SET status='rejected' WHERE id=?", (action_id,))
            self.log(action_id, "rejected", {"revision": revision})
            self.record_feedback(action_id, False, scope)
        return self.get(action_id)

    def revise_send(self, action_id: int, text: str, recipient: str) -> dict:
        """A user edit creates a new version; old displayed approvals cannot apply."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.get(action_id)
            if row["status"] != "pending" or row["proposal"]["action"] != "send":
                raise ValueError("Only pending sends can be edited")
            updated = {**row["proposal"], "text": text, "recipient": recipient}
            proposal = Proposal(**updated)
            validate(proposal)
            if decide(proposal).status != "pending":
                raise ValueError("Invalid edited response")
            self.db.execute("UPDATE actions SET proposal=?,revision=revision+1 WHERE id=?",
                            (json.dumps(updated, ensure_ascii=False), action_id))
            self.log(action_id, "revised", {"revision": row["revision"] + 1})
        return self.get(action_id)

    def _execute(self, action_id: int, approved: bool = False):
        # Internal only. This local executor shares the SQLite transaction;
        # a future Gmail executor must handle uncertain external outcomes separately.
        row = self.get(action_id)
        p = Proposal(**row["proposal"])
        validate(p)
        d = decide(p)
        if row["status"] not in {"ready", "pending"} or d.status not in {"ready", "pending"}:
            raise ValueError("Action cannot execute")
        if d.status == "pending" and not approved:
            pref = self.preference(p, self.email_for(action_id))
            if p.action != "archive" or pref["mode"] != "notify":
                raise ValueError("Approval required")
            self.log(action_id, "learned_permission", pref)
        if p.action == "archive" and self.preference(p, self.email_for(action_id))["mode"] == "keep":
            raise ValueError("Explicit preference requires keeping this email in inbox")
        email_id = row["email_id"]  # Server-bound scope, never chosen by the model.
        if p.action == "label":
            self.db.execute("INSERT OR IGNORE INTO labels VALUES(?,?)", (email_id, p.label))
        elif p.action == "archive":
            self.db.execute("UPDATE emails SET archived=1 WHERE id=?", (email_id,))
        elif p.action == "draft":
            self.db.execute("INSERT INTO drafts VALUES(?,?,?)", (action_id, email_id, p.text))
        elif p.action == "send":
            subject = self.db.execute("SELECT subject FROM emails WHERE id=?", (email_id,)).fetchone()[0]
            self.db.execute("INSERT INTO sent VALUES(?,?,?,?,?)", (action_id, email_id, p.recipient, "Re: " + subject, p.text))
        self.db.execute("UPDATE actions SET status='executed' WHERE id=?", (action_id,))
        self.log(action_id, "executed", {"action": p.action, "transport": "local_simulation"})
        if row["autonomy"] == "notify" or p.action == "send":
            self.log(action_id, "notification", {"action": p.action, "recipient": p.recipient, "text": p.text})

    def snapshot(self) -> dict:
        return {table: [dict(row) for row in self.db.execute(f"SELECT * FROM {table}")]
                for table in ("emails", "actions", "labels", "drafts", "sent", "audit", "preference_feedback", "archive_rules")}
