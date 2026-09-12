"""Durable local calendar proposals and events.

This module deliberately does not depend on mail actions: a single email can have
an independently reviewed reply and calendar decision.  It also performs no
external calendar writes.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PROPOSAL_STATUSES = {
    "awaiting_confirmation", "needs_clarification", "approved", "rejected",
    "mistake_removed",
}
EVENT_STATUSES = {"current", "rescheduled", "cancelled"}
EVENT_KINDS = {"calendar_event", "response_deadline"}
CHANGE_KINDS = {"create", "reschedule", "cancel"}


@contextmanager
def _write(db: sqlite3.Connection):
    """Commit only when this operation owns the transaction."""
    owns_transaction = not db.in_transaction
    try:
        yield
    except Exception:
        if owns_transaction:
            db.rollback()
        raise
    else:
        if owns_transaction:
            db.commit()


def system_timezone() -> str:
    """Best-effort IANA timezone for model context and calendar rendering."""
    configured = os.environ.get("WAJO_TIMEZONE", "").strip()
    if configured:
        _zone(configured, "WAJO_TIMEZONE")
        return configured
    for candidate in (Path("/etc/localtime"), Path("/var/db/timezone/localtime")):
        try:
            resolved = str(candidate.resolve())
        except OSError:
            continue
        marker = "/zoneinfo/"
        if marker in resolved:
            name = resolved.split(marker, 1)[1]
            try:
                _zone(name, "local timezone")
                return name
            except ValueError:
                pass
    # Abbreviations such as MSK are not stable IANA identifiers.  UTC is a
    # deterministic safe fallback; deployments can set WAJO_TIMEZONE.
    return "UTC"


def analysis_context(db: sqlite3.Connection, email_id: str) -> dict:
    """Trusted server context used to resolve relative dates in one model call."""
    initialize(db)
    binding = db.execute(
        "SELECT account,thread_id FROM gmail_bindings WHERE email_id=?", (email_id,)
    ).fetchone()
    thread_id = binding["thread_id"] if binding and binding["thread_id"] else email_id
    account = binding["account"] if binding else ""
    cache = None
    if binding:
        cache = db.execute(
            """SELECT internal_date FROM gmail_message_cache
               WHERE account=? AND message_id=(SELECT message_id FROM gmail_bindings WHERE email_id=?)""",
            (account, email_id),
        ).fetchone()
    received_at = ""
    if cache and cache["internal_date"]:
        raw = cache["internal_date"]
        try:
            received_at = datetime.fromtimestamp(int(raw) / 1000, timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            try:
                received_at = _aware(raw, "received_at").astimezone(timezone.utc).isoformat()
            except ValueError:
                received_at = ""
    if not received_at:
        try:
            job = db.execute("SELECT created_at FROM incoming_jobs WHERE id=?", (email_id,)).fetchone()
        except sqlite3.OperationalError:
            job = None
        received_at = (job["created_at"] if job and job["created_at"]
                       else datetime.now(timezone.utc).isoformat())
    current_rows = db.execute(
        """SELECT id,title,kind,semantic_kind,start_utc,end_utc,local_date,all_day,source_timezone
           FROM calendar_events WHERE source_thread_id=? AND account=? AND status='current'
           ORDER BY received_at DESC,id DESC""",
        (thread_id, account),
    ).fetchall()
    current_events = [dict(row) for row in current_rows]
    return {
        "received_at": received_at,
        "local_timezone": system_timezone(),
        "current_same_thread_event": current_events[0] if len(current_events) == 1 else None,
        "current_same_thread_events": current_events,
    }


def register_analysis(db: sqlite3.Connection, email, proposal, context: dict, *,
                      safety_blocked: bool = False) -> dict | None:
    """Persist and, when qualified, auto-apply the model's independent event suggestion."""
    if proposal.event_change == "none":
        return None
    if (proposal.event_original_text not in email.body
            or not proposal.event_evidence.strip()
            or proposal.event_evidence not in email.body):
        raise ValueError("Calendar suggestion requires exact evidence from the email body")
    confidence = proposal.event_confidence
    ambiguity = proposal.event_ambiguity_reason
    safety_blocked = bool(safety_blocked or proposal.suspicious or proposal.needs_human)
    if safety_blocked:
        confidence = "ambiguous"
        ambiguity = ambiguity or "Safety or human review is required before saving this event."
    current_events = context.get("current_same_thread_events") or []
    prior = context.get("current_same_thread_event")
    change = proposal.event_change
    if change in {"reschedule", "cancel"} and not prior and current_events:
        title = proposal.event_title.strip().casefold()
        title_matches = [item for item in current_events
                         if str(item.get("title", "")).strip().casefold() == title]
        semantic = proposal.event_semantic_kind.strip().casefold()
        semantic_matches = [item for item in current_events
                            if semantic and str(item.get("semantic_kind", "")).strip().casefold() == semantic]
        matches = title_matches if len(title_matches) == 1 else semantic_matches
        prior = matches[0] if len(matches) == 1 else None
        if prior is None:
            # Never guess which existing event a later message changes.
            return None
    prior_id = prior["id"] if prior and change in {"reschedule", "cancel"} else None
    if change == "cancel" and prior_id is None:
        return None  # Nothing in Wajo's current calendar can be cancelled.
    if change == "reschedule" and prior_id is None:
        change = "create"  # Preserve the newly stated date without inventing prior state.
    all_day = bool(proposal.event_all_day)
    start_at = "" if all_day else proposal.event_start
    end_at = "" if all_day else proposal.event_end
    local_date = proposal.event_start if all_day else ""
    local_end_date = proposal.event_end if all_day else ""
    saved = propose_event(
        db, source_email_id=email.id, kind=proposal.event_kind,
        title=proposal.event_title, received_at=context["received_at"],
        original_text=proposal.event_original_text,
        semantic_kind=proposal.event_semantic_kind, evidence=proposal.event_evidence,
        start_at=start_at, end_at=end_at, all_day=all_day,
        local_date=local_date, local_end_date=local_end_date,
        source_timezone=proposal.event_timezone,
        local_timezone=context["local_timezone"], confidence=confidence,
        ambiguity_reason=ambiguity, change_kind=change,
        supersedes_event_id=prior_id, _commit=False,
        safety_blocked=safety_blocked,
    )
    if saved["status"] != "awaiting_confirmation":
        return saved
    from . import event_skills
    event_skills.apply_qualified(db, saved["id"], saved["revision"])
    return get_proposal(db, saved["id"])


def initialize(db: sqlite3.Connection) -> None:
    """Install the additive schema. Safe to run for every application start."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS event_proposals (
            id INTEGER PRIMARY KEY,
            source_email_id TEXT NOT NULL REFERENCES emails(id),
            source_thread_id TEXT NOT NULL,
            account TEXT NOT NULL DEFAULT '',
            candidate_key TEXT NOT NULL,
            kind TEXT NOT NULL,
            semantic_kind TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            sender TEXT NOT NULL,
            subject TEXT NOT NULL,
            evidence TEXT NOT NULL DEFAULT '',
            original_text TEXT NOT NULL,
            received_at TEXT NOT NULL,
            start_utc TEXT NOT NULL DEFAULT '',
            end_utc TEXT NOT NULL DEFAULT '',
            local_date TEXT NOT NULL DEFAULT '',
            local_end_date TEXT NOT NULL DEFAULT '',
            all_day INTEGER NOT NULL,
            source_timezone TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL,
            ambiguity_reason TEXT NOT NULL DEFAULT '',
            change_kind TEXT NOT NULL DEFAULT 'create',
            supersedes_event_id INTEGER REFERENCES calendar_events(id),
            status TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            automatic INTEGER NOT NULL DEFAULT 0,
            safety_blocked INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            decided_at TEXT NOT NULL DEFAULT '',
            UNIQUE(source_email_id, candidate_key)
        );
        CREATE TABLE IF NOT EXISTS calendar_events (
            id INTEGER PRIMARY KEY,
            proposal_id INTEGER NOT NULL UNIQUE REFERENCES event_proposals(id),
            source_email_id TEXT NOT NULL REFERENCES emails(id),
            source_thread_id TEXT NOT NULL,
            account TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,
            semantic_kind TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            original_text TEXT NOT NULL,
            received_at TEXT NOT NULL,
            start_utc TEXT NOT NULL DEFAULT '',
            end_utc TEXT NOT NULL DEFAULT '',
            local_date TEXT NOT NULL DEFAULT '',
            local_end_date TEXT NOT NULL DEFAULT '',
            all_day INTEGER NOT NULL,
            source_timezone TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'current',
            supersedes_event_id INTEGER REFERENCES calendar_events(id),
            superseded_by_event_id INTEGER REFERENCES calendar_events(id),
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS calendar_events_current_timed
            ON calendar_events(status,start_utc);
        CREATE INDEX IF NOT EXISTS calendar_events_current_all_day
            ON calendar_events(status,local_date);
        CREATE INDEX IF NOT EXISTS event_proposals_source
            ON event_proposals(source_email_id,status);
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(event_proposals)")}
    if "safety_blocked" not in columns:
        db.execute("ALTER TABLE event_proposals ADD COLUMN safety_blocked INTEGER NOT NULL DEFAULT 0")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    for key in ("all_day", "automatic", "safety_blocked"):
        if key in result:
            result[key] = bool(result[key])
    return result


def _aware(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def _calendar_date(value: str, field: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field}") from exc


def _zone(name: str, field: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid {field}") from exc


def _normalize_timed(start_at: str, end_at: str, source_timezone: str,
                     local_timezone: str) -> tuple[str, str, str]:
    """Return UTC start/end and the actual source zone used.

    Naive values use the user's explicit local zone. An explicit source zone is
    retained even though timestamps are normalized to UTC.
    """
    if not start_at:
        raise ValueError("A timed event requires start_at")
    used_zone = source_timezone or local_timezone
    if not used_zone:
        raise ValueError("A timed event without an offset requires local_timezone")
    zone = _zone(used_zone, "timezone")

    def parse(value: str, field: str) -> datetime:
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid {field}") from exc
        if result.tzinfo is None:
            candidates = []
            for fold in (0, 1):
                candidate = result.replace(tzinfo=zone, fold=fold)
                utc = candidate.astimezone(timezone.utc)
                back = utc.astimezone(zone)
                if back.replace(tzinfo=None) == result and back.fold == fold:
                    candidates.append(utc)
            unique = {candidate.isoformat(): candidate for candidate in candidates}
            if not unique:
                raise ValueError(f"{field} is not a real local time in {used_zone}")
            if len(unique) > 1:
                raise ValueError(f"{field} is ambiguous in {used_zone}; include an explicit UTC offset")
            return next(iter(unique.values()))
        if source_timezone:
            in_zone = result.astimezone(zone)
            if (in_zone.replace(tzinfo=None) != result.replace(tzinfo=None)
                    or in_zone.utcoffset() != result.utcoffset()):
                raise ValueError(f"{field} offset does not match {source_timezone}")
        return result.astimezone(timezone.utc)

    start = parse(start_at, "start_at")
    end = parse(end_at, "end_at") if end_at else None
    if end is not None and end <= start:
        raise ValueError("end_at must be later than start_at")
    return start.isoformat(), end.isoformat() if end else "", used_zone


def _safe_source(db: sqlite3.Connection, email_id: str,
                 requested_thread_id: str = "") -> tuple[str, str, str, str]:
    email = db.execute("SELECT sender,subject FROM emails WHERE id=?", (email_id,)).fetchone()
    if email is None:
        raise ValueError("Unknown source email")
    binding = db.execute(
        "SELECT account,thread_id FROM gmail_bindings WHERE email_id=?", (email_id,)
    ).fetchone()
    known_thread = (binding["thread_id"] if binding and binding["thread_id"] else email_id)
    if requested_thread_id and requested_thread_id != known_thread:
        raise ValueError("Source thread does not match the stored email binding")
    return known_thread, binding["account"] if binding else "", email["sender"], email["subject"]


def propose_event(db: sqlite3.Connection, *, source_email_id: str, kind: str,
                  title: str, received_at: str, original_text: str,
                  candidate_key: str = "", semantic_kind: str = "",
                  evidence: str = "", source_thread_id: str = "",
                  start_at: str = "", end_at: str = "", all_day: bool = False,
                  local_date: str = "", local_end_date: str = "",
                  source_timezone: str = "", local_timezone: str = "",
                  confidence: str = "clear", ambiguity_reason: str = "",
                  change_kind: str = "create", supersedes_event_id: int | None = None,
                  safety_blocked: bool = False,
                  _commit: bool = True) -> dict:
    """Persist an idempotent candidate; ambiguous candidates can never be approved."""
    if kind not in EVENT_KINDS or change_kind not in CHANGE_KINDS:
        raise ValueError("Invalid event kind or change kind")
    if confidence not in {"clear", "ambiguous"}:
        raise ValueError("Invalid confidence")
    if type(all_day) is not bool or type(safety_blocked) is not bool or not title.strip() or not original_text.strip():
        raise ValueError("Event title, original text and boolean all_day are required")
    received = _aware(received_at, "received_at").astimezone(timezone.utc).isoformat()
    thread_id, account, sender, subject = _safe_source(db, source_email_id, source_thread_id)
    if change_kind in {"reschedule", "cancel"}:
        prior = db.execute("SELECT * FROM calendar_events WHERE id=?", (supersedes_event_id,)).fetchone()
        if prior is None or prior["status"] != "current":
            raise ValueError("A current event is required for reschedule or cancellation")
        if prior["source_thread_id"] != thread_id or prior["account"] != account:
            raise ValueError("A later message may supersede only an event in its thread")
        if prior["source_email_id"] == source_email_id or received <= prior["received_at"]:
            raise ValueError("Only a later source message may supersede an event")
    elif supersedes_event_id is not None:
        raise ValueError("A new event cannot supersede another event")

    reason = ambiguity_reason.strip()
    start_utc = end_utc = normalized_date = normalized_end_date = ""
    used_zone = source_timezone
    if confidence == "ambiguous":
        if not reason:
            raise ValueError("Ambiguous proposals require a clarification reason")
    elif change_kind != "cancel":
        try:
            if all_day:
                normalized_date = _calendar_date(local_date, "local_date")
                normalized_end_date = _calendar_date(local_end_date, "local_end_date") if local_end_date else ""
                if normalized_end_date and normalized_end_date < normalized_date:
                    raise ValueError("local_end_date cannot precede local_date")
                used_zone = ""
            else:
                start_utc, end_utc, used_zone = _normalize_timed(
                    start_at, end_at, source_timezone, local_timezone)
        except ValueError as exc:
            confidence, reason = "ambiguous", str(exc)
            start_utc = end_utc = normalized_date = normalized_end_date = ""

    if not candidate_key:
        raw_key = "\x1f".join((kind, semantic_kind, original_text, change_kind))
        candidate_key = hashlib.sha256(raw_key.encode()).hexdigest()[:24]
    values = {
        "source_email_id": source_email_id, "source_thread_id": thread_id, "account": account,
        "candidate_key": candidate_key, "kind": kind, "semantic_kind": semantic_kind,
        "title": title.strip(), "sender": sender, "subject": subject, "evidence": evidence,
        "original_text": original_text, "received_at": received, "start_utc": start_utc,
        "end_utc": end_utc, "local_date": normalized_date, "local_end_date": normalized_end_date,
        "all_day": int(all_day), "source_timezone": used_zone, "confidence": confidence,
        "ambiguity_reason": reason, "change_kind": change_kind,
        "supersedes_event_id": supersedes_event_id,
        "safety_blocked": int(safety_blocked),
        "status": "needs_clarification" if confidence == "ambiguous" else "awaiting_confirmation",
    }
    existing = db.execute(
        "SELECT * FROM event_proposals WHERE source_email_id=? AND candidate_key=?",
        (source_email_id, candidate_key),
    ).fetchone()
    if existing:
        comparable = dict(existing)
        for key, value in values.items():
            if comparable[key] != value:
                raise ValueError("Candidate key already belongs to a different proposal")
        if _commit:
            db.commit()
        return _row(existing)
    columns = ",".join(values)
    placeholders = ",".join("?" for _ in values)
    with _write(db):
        cursor = db.execute(
            f"INSERT INTO event_proposals({columns},created_at) VALUES({placeholders},?)",
            (*values.values(), _now()),
        )
    if _commit:
        db.commit()
    return get_proposal(db, cursor.lastrowid)


def get_proposal(db: sqlite3.Connection, proposal_id: int) -> dict:
    row = db.execute("SELECT * FROM event_proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown event proposal")
    return _row(row)


def get_event(db: sqlite3.Connection, event_id: int) -> dict:
    row = db.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown calendar event")
    return _row(row)


def approve_event(db: sqlite3.Connection, proposal_id: int, revision: int,
                  *, automatic: bool = False) -> dict:
    """Apply one exact proposal revision once; returns proposal and event (if any)."""
    proposal = get_proposal(db, proposal_id)
    if proposal["status"] == "approved":
        event = db.execute("SELECT * FROM calendar_events WHERE proposal_id=?", (proposal_id,)).fetchone()
        return {"proposal": proposal, "event": _row(event), "idempotent": True}
    if proposal["status"] != "awaiting_confirmation" or proposal["revision"] != revision:
        raise ValueError("Proposal is not approvable at this revision")
    stamp = _now()
    with _write(db):
        prior_id = proposal["supersedes_event_id"]
        if proposal["change_kind"] == "cancel":
            changed = db.execute(
                "UPDATE calendar_events SET status='cancelled' WHERE id=? AND status='current'", (prior_id,)
            ).rowcount
            if changed != 1:
                raise ValueError("Superseded event is no longer current")
            event = None
        else:
            cursor = db.execute("""INSERT INTO calendar_events(
                proposal_id,source_email_id,source_thread_id,account,kind,semantic_kind,title,
                original_text,received_at,start_utc,end_utc,local_date,local_end_date,all_day,
                source_timezone,status,supersedes_event_id,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'current',?,?)""", (
                proposal_id, proposal["source_email_id"], proposal["source_thread_id"], proposal["account"],
                proposal["kind"], proposal["semantic_kind"], proposal["title"], proposal["original_text"],
                proposal["received_at"], proposal["start_utc"], proposal["end_utc"], proposal["local_date"],
                proposal["local_end_date"], int(proposal["all_day"]), proposal["source_timezone"], prior_id, stamp,
            ))
            event_id = cursor.lastrowid
            if prior_id is not None:
                changed = db.execute("""UPDATE calendar_events SET status='rescheduled',superseded_by_event_id=?
                                        WHERE id=? AND status='current'""", (event_id, prior_id)).rowcount
                if changed != 1:
                    raise ValueError("Superseded event is no longer current")
            event = get_event(db, event_id)
        db.execute("""UPDATE event_proposals SET status='approved',automatic=?,decided_at=?
                      WHERE id=?""", (int(automatic), stamp, proposal_id))
    return {"proposal": get_proposal(db, proposal_id), "event": event, "idempotent": False}


def reject_event(db: sqlite3.Connection, proposal_id: int, revision: int) -> dict:
    proposal = get_proposal(db, proposal_id)
    if proposal["status"] == "rejected":
        return proposal
    if proposal["status"] not in {"awaiting_confirmation", "needs_clarification"} or proposal["revision"] != revision:
        raise ValueError("Proposal is not rejectable at this revision")
    with _write(db):
        db.execute("UPDATE event_proposals SET status='rejected',decided_at=? WHERE id=?", (_now(), proposal_id))
    return get_proposal(db, proposal_id)


def clarify_event(db: sqlite3.Connection, proposal_id: int, revision: int, *,
                  start_at: str = "", end_at: str = "", all_day: bool = False,
                  local_date: str = "", local_end_date: str = "",
                  source_timezone: str = "", local_timezone: str = "") -> dict:
    """Resolve an ambiguous proposal without approving the revised value."""
    proposal = get_proposal(db, proposal_id)
    if proposal["status"] != "needs_clarification" or proposal["revision"] != revision:
        raise ValueError("Proposal does not need clarification at this revision")
    if proposal["change_kind"] == "cancel":
        normalized = ("", "", "", "", "")
    elif all_day:
        day = _calendar_date(local_date, "local_date")
        end_day = _calendar_date(local_end_date, "local_end_date") if local_end_date else ""
        if end_day and end_day < day:
            raise ValueError("local_end_date cannot precede local_date")
        normalized = ("", "", day, end_day, "")
    else:
        start, end, zone = _normalize_timed(start_at, end_at, source_timezone, local_timezone)
        normalized = (start, end, "", "", zone)
    with _write(db):
        db.execute("""UPDATE event_proposals SET start_utc=?,end_utc=?,local_date=?,local_end_date=?,
                      all_day=?,source_timezone=?,confidence='clear',ambiguity_reason='',
                      status='awaiting_confirmation',revision=revision+1 WHERE id=?""",
                   (*normalized[:4], int(all_day), normalized[4], proposal_id))
    return get_proposal(db, proposal_id)


def remove_mistaken_event(db: sqlite3.Connection, event_id: int) -> dict:
    """Undo an automatic addition while retaining an auditable tombstone."""
    event = get_event(db, event_id)
    if event["status"] != "current":
        raise ValueError("Only a current event can be marked as a mistake")
    proposal = get_proposal(db, event["proposal_id"])
    if not proposal["automatic"]:
        raise ValueError("Only an automatically added event uses this correction")
    with _write(db):
        db.execute("UPDATE calendar_events SET status='cancelled' WHERE id=?", (event_id,))
        db.execute("UPDATE event_proposals SET status='mistake_removed',decided_at=? WHERE id=?",
                   (_now(), proposal["id"]))
    return get_event(db, event_id)


def _shift_year(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:  # February 29
        return value.replace(year=value.year + years, day=28)


def list_events(db: sqlite3.Connection, *, now: datetime | None = None,
                local_timezone: str = "UTC", include_history: bool = False) -> list[dict]:
    """List the supported calendar window and render timed values in local time."""
    zone = _zone(local_timezone, "local_timezone")
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("now must include a timezone")
    today = instant.astimezone(zone).date()
    first, last = _shift_year(today, -1), _shift_year(today, 3)
    status_filter = "" if include_history else "AND status='current'"
    rows = db.execute(f"SELECT * FROM calendar_events WHERE 1=1 {status_filter} ORDER BY all_day DESC,local_date,start_utc,id").fetchall()
    result = []
    for raw in rows:
        item = _row(raw)
        if item["all_day"]:
            item_date = date.fromisoformat(item["local_date"])
            item["local_start"] = item["local_date"]
            item["local_end"] = item["local_end_date"]
        else:
            start = _aware(item["start_utc"], "stored start").astimezone(zone)
            item_date = start.date()
            item["local_start"] = start.isoformat()
            item["local_end"] = (_aware(item["end_utc"], "stored end").astimezone(zone).isoformat()
                                 if item["end_utc"] else "")
        if first <= item_date <= last:
            result.append(item)
    return result


def proposals_for_source(db: sqlite3.Connection, source_email_id: str) -> list[dict]:
    return [_row(row) for row in db.execute(
        "SELECT * FROM event_proposals WHERE source_email_id=? ORDER BY id", (source_email_id,)
    )]
