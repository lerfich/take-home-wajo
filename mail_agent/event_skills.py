"""Durable positive-only learning for local calendar Event Skills.

Calendar mutations live in :mod:`mail_agent.events`.  These functions record
why a proposal may be auto-saved and bind that decision to the exact skill
revision.  Event Skills never send mail and never depend on Superpowers.
"""

from datetime import datetime, timezone
from functools import wraps
import json
from typing import Any, Mapping

from .semantic_matcher import SemanticContext, coerce_context, compare


def _atomic(function):
    @wraps(function)
    def wrapped(db, *args, **kwargs):
        owns_transaction = not db.in_transaction
        if owns_transaction:
            db.execute("BEGIN IMMEDIATE")
        try:
            result = function(db, *args, **kwargs)
        except Exception:
            if owns_transaction:
                db.rollback()
            raise
        if owns_transaction:
            db.commit()
        return result
    return wrapped


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize(db) -> None:
    db.executescript("""
      CREATE TABLE IF NOT EXISTS event_skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        origin_account TEXT NOT NULL, source_proposal_id INTEGER NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'active', revision INTEGER NOT NULL DEFAULT 1,
        context TEXT NOT NULL, qualified INTEGER NOT NULL DEFAULT 0,
        approval_streak INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS event_skill_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id INTEGER NOT NULL UNIQUE,
        event_id INTEGER, decision TEXT NOT NULL, skill_id INTEGER,
        skill_revision INTEGER NOT NULL DEFAULT 0, account TEXT NOT NULL,
        context TEXT NOT NULL, created_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS event_skill_applications (
        proposal_id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL,
        skill_id INTEGER NOT NULL, skill_revision INTEGER NOT NULL,
        account TEXT NOT NULL, automatic INTEGER NOT NULL DEFAULT 1,
        active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS event_skill_revocations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, skill_id INTEGER NOT NULL,
        skill_revision INTEGER NOT NULL, account TEXT NOT NULL,
        event_id INTEGER, reason TEXT NOT NULL, created_at TEXT NOT NULL);
    """)


def context_from_proposal(proposal: Mapping[str, Any] | Any) -> SemanticContext:
    """Adapter for rows returned by events.get_proposal()."""
    context = coerce_context(proposal)
    if isinstance(proposal, Mapping) and proposal.get("safety_blocked"):
        return SemanticContext(context.meaning, context.subtopic, context.subject,
                               context.sender, context.evidence, context.ambiguous, True)
    return context


def _decode(row) -> dict:
    item = dict(row)
    item["context"] = json.loads(item["context"])
    item["qualified"] = bool(item["qualified"])
    return item


def get(db, skill_id: int) -> dict:
    if type(skill_id) is not int:
        raise ValueError("Choose an existing Event Skill")
    row = db.execute("SELECT * FROM event_skills WHERE id=?", (skill_id,)).fetchone()
    if not row:
        raise ValueError("This Event Skill no longer exists")
    return _decode(row)


def list_skills(db) -> list[dict]:
    return [_decode(row) for row in db.execute("SELECT * FROM event_skills ORDER BY id DESC")]


def choose(db, context: SemanticContext | Mapping[str, Any], *, qualified_only: bool = True) -> dict | None:
    query = "SELECT * FROM event_skills WHERE status='active'"
    if qualified_only:
        query += " AND qualified=1"
    candidates = []
    for row in db.execute(query + " ORDER BY id DESC"):
        skill = _decode(row)
        result = compare(skill["context"], context)
        if result.matched:
            candidates.append((result.score, skill["id"], skill, result))
    if not candidates:
        return None
    _, _, skill, result = max(candidates, key=lambda item: (item[0], item[1]))
    skill["match"] = {"outcome": result.outcome, "score": result.score,
                      "reason": result.reason, "signals": list(result.signals)}
    return skill


def auto_decision(db, account: str, context: SemanticContext | Mapping[str, Any]) -> dict:
    candidate = coerce_context(context)
    if candidate.ambiguous or candidate.suspicious:
        return {"mode": "ask", "reason": compare(candidate, candidate).reason}
    skill = choose(db, candidate)
    if not skill:
        return {"mode": "ask", "reason": "No qualified Event Skill matches."}
    # Ordinary Event Skills are portable across kept accounts.  The receiving
    # account is still persisted on every application for auditability.
    return {"mode": "auto_save", "skill_id": skill["id"], "skill_revision": skill["revision"],
            "account": account, "reason": skill["match"]["reason"]}


def _last_feedback(db):
    return db.execute("SELECT * FROM event_skill_feedback ORDER BY id DESC LIMIT 1").fetchone()


@_atomic
def record_approval(db, proposal_id: int, event_id: int, account: str,
                    context: SemanticContext | Mapping[str, Any]) -> dict:
    """Record ✓ after the caller saved the current event.

    The first ✓ starts a 1/2 streak.  Only an immediately preceding ✓ for the
    same skill revision and similar semantic context completes qualification.
    """
    if type(proposal_id) is not int or type(event_id) is not int or not isinstance(account, str) or not account:
        raise ValueError("Invalid Event Skill approval")
    ctx = coerce_context(context)
    if ctx.ambiguous or ctx.suspicious or not ctx.meaning.strip():
        raise ValueError("Ambiguous or unsafe events cannot train an Event Skill")
    existing = db.execute("SELECT * FROM event_skill_feedback WHERE proposal_id=?", (proposal_id,)).fetchone()
    if existing:
        if existing["decision"] != "approved" or existing["event_id"] != event_id:
            raise ValueError("This event proposal already has different feedback")
        skill = get(db, existing["skill_id"])
        return {"applied": True, "skill_id": skill["id"], "revision": skill["revision"],
                "count": skill["approval_streak"], "qualified": skill["qualified"], "replayed": True}

    matched = choose(db, ctx, qualified_only=False)
    now = _now()
    if matched is None:
        cursor = db.execute("""INSERT INTO event_skills
            (origin_account,source_proposal_id,status,revision,context,qualified,approval_streak,created_at,updated_at)
            VALUES(?,?,'active',1,?,0,1,?,?)""",
            (account, proposal_id, json.dumps(ctx.to_dict(), ensure_ascii=False), now, now))
        skill_id, revision, streak, qualified = cursor.lastrowid, 1, 1, False
    else:
        skill_id, revision = matched["id"], matched["revision"]
        previous = _last_feedback(db)
        consecutive = bool(previous and previous["decision"] == "approved" and
                           previous["skill_id"] == skill_id and previous["skill_revision"] == revision and
                           compare(json.loads(previous["context"]), ctx).matched)
        streak = min(2, matched["approval_streak"] + 1) if consecutive else 1
        qualified = streak >= 2
        db.execute("UPDATE event_skills SET approval_streak=?,qualified=?,updated_at=? WHERE id=?",
                   (streak, int(qualified), now, skill_id))
    db.execute("""INSERT INTO event_skill_feedback
        (proposal_id,event_id,decision,skill_id,skill_revision,account,context,created_at)
        VALUES(?,?,'approved',?,?,?,?,?)""",
        (proposal_id, event_id, skill_id, revision, account, json.dumps(ctx.to_dict(), ensure_ascii=False), now))
    return {"applied": True, "skill_id": skill_id, "revision": revision,
            "count": streak, "qualified": qualified, "replayed": False}


def _revoke(db, skill: dict, account: str, reason: str, event_id: int | None = None) -> None:
    now = _now()
    db.execute("UPDATE event_skills SET qualified=0,approval_streak=0,updated_at=? WHERE id=?", (now, skill["id"]))
    db.execute("""INSERT INTO event_skill_revocations
        (skill_id,skill_revision,account,event_id,reason,created_at) VALUES(?,?,?,?,?,?)""",
        (skill["id"], skill["revision"], account, event_id, reason, now))


@_atomic
def record_rejection(db, proposal_id: int, account: str,
                     context: SemanticContext | Mapping[str, Any]) -> dict:
    """Record ✕.  It never creates an auto-ignore rule or qualification."""
    if type(proposal_id) is not int or not isinstance(account, str) or not account:
        raise ValueError("Invalid Event Skill rejection")
    existing = db.execute("SELECT * FROM event_skill_feedback WHERE proposal_id=?", (proposal_id,)).fetchone()
    if existing:
        if existing["decision"] != "rejected":
            raise ValueError("This event proposal already has different feedback")
        return {"applied": True, "mode": "ask", "replayed": True}
    ctx = coerce_context(context); skill = choose(db, ctx, qualified_only=False)
    now = _now()
    if skill:
        _revoke(db, skill, account, "Event proposal rejected")
    db.execute("""INSERT INTO event_skill_feedback
        (proposal_id,event_id,decision,skill_id,skill_revision,account,context,created_at)
        VALUES(?,NULL,'rejected',?,?,?,?,?)""",
        (proposal_id, skill["id"] if skill else None, skill["revision"] if skill else 0,
         account, json.dumps(ctx.to_dict(), ensure_ascii=False), now))
    return {"applied": True, "mode": "ask", "skill_id": skill["id"] if skill else None,
            "qualified": False, "replayed": False}


@_atomic
def record_auto_save(db, proposal_id: int, event_id: int, account: str, skill_id: int,
                     skill_revision: int) -> dict:
    """Bind an auto-save to the exact active, qualified revision."""
    skill = get(db, skill_id)
    if (skill["status"] != "active" or not skill["qualified"] or skill["revision"] != skill_revision):
        raise ValueError("Event Skill qualification changed; ask for confirmation")
    existing = db.execute("SELECT * FROM event_skill_applications WHERE proposal_id=?", (proposal_id,)).fetchone()
    if existing:
        if (existing["event_id"], existing["skill_id"], existing["skill_revision"], existing["account"]) != (
                event_id, skill_id, skill_revision, account):
            raise ValueError("This proposal is already bound to another Event Skill application")
        return dict(existing)
    db.execute("""INSERT INTO event_skill_applications
        (proposal_id,event_id,skill_id,skill_revision,account,automatic,active,created_at)
        VALUES(?,?,?,?,?,1,1,?)""", (proposal_id, event_id, skill_id, skill_revision, account, _now()))
    return dict(db.execute("SELECT * FROM event_skill_applications WHERE proposal_id=?", (proposal_id,)).fetchone())


@_atomic
def revoke_mistake(db, event_id: int, account: str) -> dict:
    """Handle “This shouldn't have been added” after events removes the item."""
    row = db.execute("SELECT * FROM event_skill_applications WHERE event_id=? AND active=1", (event_id,)).fetchone()
    if not row:
        raise ValueError("Only an automatically added event can use this correction")
    if account != row["account"]:
        raise ValueError("The event belongs to another account")
    skill = get(db, row["skill_id"])
    # The explicit correction always returns this learned behavior to asking,
    # even if the application came from an older revision of the same Skill.
    _revoke(db, skill, account, "This shouldn't have been added", event_id)
    qualified = False
    db.execute("UPDATE event_skill_applications SET active=0 WHERE event_id=?", (event_id,))
    return {"removed": True, "skill_id": skill["id"], "qualified": qualified,
            "mode": "ask" if not qualified else "auto_save"}


@_atomic
def manage(db, skill_id: int, revision: int, operation: str,
           *, account: str, context: SemanticContext | Mapping[str, Any] | None = None) -> dict:
    """Pause/delete/edit/correction revoke qualification; resume starts at 0/2."""
    if operation not in {"pause", "resume", "delete", "edit", "correction"}:
        raise ValueError("Choose Pause, Resume, Delete, Edit or Correction")
    skill = get(db, skill_id)
    if type(revision) is not int or revision != skill["revision"]:
        raise ValueError("The Event Skill changed. Refresh before managing it.")
    expected = {"pause": "active", "resume": "paused"}
    if operation in expected and skill["status"] != expected[operation]:
        raise ValueError("The Event Skill is not in the required state")
    if operation == "edit" and context is None:
        raise ValueError("Edited Event Skills require a semantic context")
    status = {"pause": "paused", "resume": "active", "delete": "deleted"}.get(operation, skill["status"])
    payload = coerce_context(context).to_dict() if context is not None else skill["context"]
    _revoke(db, skill, account, "Event Skill " + operation)
    db.execute("UPDATE event_skills SET status=?,revision=revision+1,context=?,qualified=0,approval_streak=0,updated_at=? WHERE id=?",
               (status, json.dumps(payload, ensure_ascii=False), _now(), skill_id))
    return {"saved": True, "skill_id": skill_id, "revision": revision + 1,
            "status": status, "qualified": False, "count": 0}


@_atomic
def approve_proposal(db, proposal_id: int, revision: int) -> dict:
    """Save a user-approved Events proposal and learn only from a new event.

    Reschedules and cancellation messages update the calendar but are ordinary
    calendar maintenance, not Event Skill feedback.
    """
    from . import events
    proposal = events.get_proposal(db, proposal_id)
    result = events.approve_event(db, proposal_id, revision)
    if result["idempotent"]:
        feedback = db.execute("SELECT * FROM event_skill_feedback WHERE proposal_id=?", (proposal_id,)).fetchone()
        return dict(result, training=dict(feedback) if feedback else None)
    if (proposal["change_kind"] != "create" or result["event"] is None
            or proposal.get("safety_blocked")):
        return dict(result, training=None)
    account = proposal["account"] or "local_simulation"
    training = record_approval(db, proposal_id, result["event"]["id"], account,
                               context_from_proposal(proposal))
    return dict(result, training=training)


@_atomic
def reject_proposal(db, proposal_id: int, revision: int) -> dict:
    """Reject this proposal; ✕ records no automatic-ignore permission."""
    from . import events
    proposal = events.get_proposal(db, proposal_id)
    rejected = events.reject_event(db, proposal_id, revision)
    if proposal["change_kind"] != "create":
        return {"proposal": rejected, "training": None}
    return {"proposal": rejected, "training": record_rejection(
        db, proposal_id, proposal["account"] or "local_simulation", context_from_proposal(proposal))}


@_atomic
def apply_qualified(db, proposal_id: int, revision: int) -> dict:
    """Auto-save one clear proposal after rechecking exact qualification."""
    from . import events
    proposal = events.get_proposal(db, proposal_id)
    if proposal["change_kind"] != "create":
        return {"saved": False, "mode": "ask", "reason": "Calendar changes require their own current-state checks."}
    account = proposal["account"] or "local_simulation"
    decision = auto_decision(db, account, context_from_proposal(proposal))
    if decision["mode"] != "auto_save":
        return dict(decision, saved=False)
    result = events.approve_event(db, proposal_id, revision, automatic=True)
    if result["event"] is None:
        raise ValueError("An automatically saved proposal must create an event")
    application = record_auto_save(db, proposal_id, result["event"]["id"], account,
                                   decision["skill_id"], decision["skill_revision"])
    return dict(result, mode="auto_save", saved=True, application=application)


@_atomic
def remove_mistaken_event(db, event_id: int, account: str | None = None) -> dict:
    """Atomically expose the explicit mistake correction to an Events caller."""
    from . import events
    application = db.execute(
        "SELECT * FROM event_skill_applications WHERE event_id=? AND active=1", (event_id,)
    ).fetchone()
    if not application:
        raise ValueError("Only an automatically added event can use this correction")
    expected_account = application["account"]
    if account is not None and account != expected_account:
        raise ValueError("The event belongs to another account")
    removed = events.remove_mistaken_event(db, event_id)
    correction = revoke_mistake(db, event_id, expected_account)
    return {"event": removed, "correction": correction}
