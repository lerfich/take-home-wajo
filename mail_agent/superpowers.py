"""Explicit, account-bound permission for narrowly learned Gmail replies.

This module deliberately does not send mail.  ``authorize_auto`` records a durable,
idempotent authorization which the Gmail integration may subsequently queue.  A
caller must run the normal Gmail executor and record its result separately.
"""
from datetime import datetime, timezone
import hashlib
import json
import re


QUALIFICATION_THRESHOLD = 2


def _now():
    return datetime.now(timezone.utc).isoformat()


def initialize(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS superpower_settings (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), enabled INTEGER NOT NULL DEFAULT 0,
        account TEXT NOT NULL DEFAULT '', reviewed_at TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS superpower_applications (
        action_id INTEGER PRIMARY KEY REFERENCES actions(id), skill_id INTEGER NOT NULL,
        skill_revision INTEGER NOT NULL, account TEXT NOT NULL, applied_at TEXT NOT NULL,
        revoked_at TEXT NOT NULL DEFAULT '');
      CREATE TABLE IF NOT EXISTS superpower_confirmations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, action_id INTEGER NOT NULL UNIQUE REFERENCES actions(id),
        action_revision INTEGER NOT NULL, skill_id INTEGER NOT NULL,
        skill_revision INTEGER NOT NULL, account TEXT NOT NULL,
        recipient TEXT NOT NULL, subject TEXT NOT NULL, text_hash TEXT NOT NULL,
        sent_id TEXT NOT NULL, confirmed_at TEXT NOT NULL, revoked_at TEXT NOT NULL DEFAULT '');
      CREATE TABLE IF NOT EXISTS superpower_revocations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, skill_id INTEGER NOT NULL, account TEXT NOT NULL,
        skill_revision INTEGER NOT NULL DEFAULT 0, action_id INTEGER,
        cutoff_confirmation_id INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, UNIQUE(skill_id,account,action_id,cutoff_confirmation_id));
      CREATE TABLE IF NOT EXISTS autosent_journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT, action_id INTEGER NOT NULL,
        action_revision INTEGER NOT NULL, skill_id INTEGER NOT NULL,
        skill_revision INTEGER NOT NULL, account TEXT NOT NULL,
        recipient TEXT NOT NULL, subject TEXT NOT NULL, text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'authorized', delivery_status TEXT NOT NULL DEFAULT 'pending',
        sent_id TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL,
        seen INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
        UNIQUE(action_id,action_revision));
    """)
    columns = {r["name"] for r in db.execute("PRAGMA table_info(superpower_revocations)")}
    for name, declaration in (("skill_revision", "INTEGER NOT NULL DEFAULT 0"),
                              ("reason", "TEXT NOT NULL DEFAULT ''")):
        if name not in columns:
            db.execute(f"ALTER TABLE superpower_revocations ADD COLUMN {name} {declaration}")
    columns = {r["name"] for r in db.execute("PRAGMA table_info(autosent_journal)")}
    for name, declaration in (("delivery_status", "TEXT NOT NULL DEFAULT 'pending'"),
                              ("sent_id", "TEXT NOT NULL DEFAULT ''")):
        if name not in columns:
            db.execute(f"ALTER TABLE autosent_journal ADD COLUMN {name} {declaration}")


def _dict(value):
    return dict(value) if value is not None else None


def _account_for_action(agent, action):
    binding = agent.db.execute(
        "SELECT * FROM gmail_bindings WHERE email_id=?", (action["email_id"],)).fetchone()
    if not binding:
        return None, None
    return str(binding["account"]).strip().casefold(), binding


def _application_skill_id(application):
    if application is None:
        return None
    if type(application) is not dict:
        raise ValueError("Invalid Draft Skill application")
    if application.get("status", "applied") != "applied":
        return None
    candidate = application.get("skill", application.get("rule", application))
    if type(candidate) is not dict:
        raise ValueError("Invalid Draft Skill application")
    ident = candidate.get("id", application.get("skill_id"))
    if type(ident) is not int:
        raise ValueError("A saved Draft Skill is required")
    # Managed Skills are intentionally encoded as negative rule IDs by
    # draft_preferences.choose. Positive rule IDs are legacy style preferences
    # and never carry automatic-send authority.
    if "rule" in application:
        return abs(ident) if ident < 0 else None
    return abs(ident)


def bind_application(agent, action_id, application):
    """Bind an applied action to the saved Draft Skill version and Gmail account.

    ``application`` may be the selected skill itself or contain it under ``skill``
    (``rule`` is accepted for compatibility with existing application objects).
    Values supplied by the caller are never trusted for revision/account state.
    """
    initialize(agent.db)
    ident = _application_skill_id(application)
    if ident is None:
        return None
    action = agent.get(action_id)
    if action["proposal"].get("action") not in {"draft", "send"}:
        raise ValueError("Superpowers only apply to reply drafts")
    account, _ = _account_for_action(agent, action)
    if not account:
        raise ValueError("A Gmail account binding is required")
    skill = agent.db.execute("SELECT * FROM skills WHERE id=?", (ident,)).fetchone()
    if not skill or skill["family"] != "draft" or skill["status"] != "active":
        raise ValueError("An active reviewed Draft Skill is required")
    with agent.db:
        current = agent.db.execute(
            "SELECT * FROM superpower_applications WHERE action_id=?", (action_id,)).fetchone()
        values = (ident, skill["revision"], account)
        if current and (current["skill_id"], current["skill_revision"], current["account"]) != values:
            raise ValueError("The action already has a different Draft Skill binding")
        agent.db.execute("""INSERT OR IGNORE INTO superpower_applications
            (action_id,skill_id,skill_revision,account,applied_at) VALUES(?,?,?,?,?)""",
            (action_id, ident, skill["revision"], account, _now()))
    return _dict(agent.db.execute(
        "SELECT * FROM superpower_applications WHERE action_id=?", (action_id,)).fetchone())


def _successful_unchanged_manual_send(agent, action, reply):
    if action["transport"] != "gmail" or action["status"] != "executed":
        return False, "Only a successful manual Gmail send can confirm a rule"
    if action["proposal"].get("action") not in {"draft", "send"}:
        return False, "Only a reply can confirm a Draft Skill"
    if not reply or not reply.get("sent_id") or not reply.get("raw") or not reply.get("approved_hash"):
        return False, "The exact sent Gmail reply was not verified"
    if hashlib.sha256(reply["raw"].encode()).hexdigest() != reply["approved_hash"]:
        return False, "The sent reply differs from the manually approved version"
    operation = agent.db.execute("""SELECT * FROM gmail_operations
        WHERE action_id=? AND revision=? AND operation=?""",
        (action["id"], action["revision"], "send:" + str(action["revision"]))).fetchone()
    if not operation or operation["status"] != "done" or not operation["approved"]:
        return False, "A completed exact manual send approval is required"
    return True, ""


def record_manual_send(agent, action, reply):
    """Record one training confirmation after Gmail verified an unchanged send."""
    initialize(agent.db)
    action = dict(action)
    reply = dict(reply) if reply is not None else None
    ok, reason = _successful_unchanged_manual_send(agent, action, reply)
    if not ok:
        raise ValueError(reason)
    application = agent.db.execute(
        "SELECT * FROM superpower_applications WHERE action_id=?", (action["id"],)).fetchone()
    if not application or application["revoked_at"]:
        raise ValueError("No current Draft Skill application is bound to this reply")
    account, binding = _account_for_action(agent, action)
    skill = agent.db.execute("SELECT * FROM skills WHERE id=?", (application["skill_id"],)).fetchone()
    if (not skill or skill["family"] != "draft" or skill["status"] != "active" or
            skill["revision"] != application["skill_revision"] or account != application["account"]):
        raise ValueError("The applied Draft Skill or Gmail account changed")
    email = agent.email_for(action["id"])
    if str(binding["source_role"]) != "incoming" or reply["recipient"].strip().casefold() != email.sender.strip().casefold():
        raise ValueError("The recipient must be exactly the incoming sender")
    with agent.db:
        agent.db.execute("""INSERT OR IGNORE INTO superpower_confirmations
            (action_id,action_revision,skill_id,skill_revision,account,recipient,subject,text_hash,sent_id,confirmed_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""", (action["id"], action["revision"], skill["id"], skill["revision"],
            account, reply["recipient"], reply["subject"], hashlib.sha256(reply["text"].encode()).hexdigest(),
            reply["sent_id"], _now()))
    return _dict(agent.db.execute(
        "SELECT * FROM superpower_confirmations WHERE action_id=?", (action["id"],)).fetchone())


def _revoke(agent, skill_id, account, action_id=None, skill_revision=0, reason=""):
    cutoff = agent.db.execute("SELECT coalesce(max(id),0) FROM superpower_confirmations").fetchone()[0]
    with agent.db:
        if action_id is not None:
            agent.db.execute("UPDATE superpower_applications SET revoked_at=? WHERE action_id=?", (_now(), action_id))
            agent.db.execute("UPDATE superpower_confirmations SET revoked_at=? WHERE action_id=?", (_now(), action_id))
        agent.db.execute("""INSERT OR IGNORE INTO superpower_revocations
            (skill_id,account,skill_revision,action_id,cutoff_confirmation_id,reason,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            (skill_id, account, skill_revision, action_id, cutoff, reason, _now()))
    return {"revoked": True, "skill_id": skill_id, "account": account, "cutoff": cutoff}


def revoke_for_action(agent, action_id, reason=""):
    initialize(agent.db)
    row = agent.db.execute("SELECT * FROM superpower_applications WHERE action_id=?", (action_id,)).fetchone()
    if not row:
        return {"revoked": False}
    return _revoke(agent, row["skill_id"], row["account"], action_id,
                   row["skill_revision"], reason)


def revoke_for_skill(agent, skill_id, skill_revision=None, reason="", current_account=None):
    initialize(agent.db)
    if type(skill_id) is not int:
        raise ValueError("Choose a Draft Skill")
    account = (current_account or "").strip().casefold()
    rows = ([{"account": account}] if account else list(agent.db.execute(
        "SELECT DISTINCT account FROM superpower_applications WHERE skill_id=?", (skill_id,))))
    results = [_revoke(agent, skill_id, row["account"], None,
                       skill_revision or 0, reason) for row in rows]
    return {"revoked": bool(results), "results": results}


def set_global(agent, data, current_account):
    initialize(agent.db)
    if type(data) is not dict or set(data) - {"enabled", "reviewed_rules"} or type(data.get("enabled")) is not bool:
        raise ValueError("Choose whether to enable Superpowers")
    account = (current_account or "").strip().casefold()
    if data["enabled"] and (data.get("reviewed_rules") is not True or not account):
        raise ValueError("Review the qualified rules for the current Gmail account before enabling")
    now = _now()
    with agent.db:
        agent.db.execute("""INSERT INTO superpower_settings(singleton,enabled,account,reviewed_at,updated_at)
            VALUES(1,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET enabled=excluded.enabled,
            account=excluded.account,reviewed_at=excluded.reviewed_at,updated_at=excluded.updated_at""",
            (int(data["enabled"]), account if data["enabled"] else "",
             now if data["enabled"] else "", now))
    return state(agent, current_account)


def _cutoff(agent, skill_id, account):
    row = agent.db.execute("""SELECT coalesce(max(cutoff_confirmation_id),0) AS cutoff
        FROM superpower_revocations WHERE skill_id=? AND account=?""", (skill_id, account)).fetchone()
    return row["cutoff"]


def _qualification(agent, skill, account):
    cutoff = _cutoff(agent, skill["id"], account)
    rows = list(agent.db.execute("""SELECT id,action_id FROM superpower_confirmations
        WHERE skill_id=? AND skill_revision=? AND account=? AND id>? AND revoked_at=''
        ORDER BY id""", (skill["id"], skill["revision"], account, cutoff)))
    return {"count": len(rows), "threshold": QUALIFICATION_THRESHOLD,
            "qualified": len(rows) >= QUALIFICATION_THRESHOLD,
            "confirmation_ids": [r["id"] for r in rows]}


def state(agent, current_account):
    initialize(agent.db)
    account = (current_account or "").strip().casefold()
    setting = agent.db.execute("SELECT * FROM superpower_settings WHERE singleton=1").fetchone()
    enabled = bool(setting and setting["enabled"] and setting["account"] == account)
    rules = []
    for skill in agent.db.execute("SELECT * FROM skills WHERE family='draft' AND status='active' ORDER BY id"):
        applications = agent.db.execute("""SELECT count(*) FROM superpower_applications
            WHERE skill_id=? AND skill_revision=? AND account=? AND revoked_at=''""",
            (skill["id"], skill["revision"], account)).fetchone()[0]
        if not applications:
            continue
        q = _qualification(agent, skill, account)
        from .skills import title as skill_title
        decoded = dict(skill); decoded["config"] = json.loads(decoded["config"])
        rules.append({"skill_id": skill["id"], "skill_revision": skill["revision"],
                      "revision": skill["revision"], "title": skill_title(decoded),
                      "confirmations": q["count"], **q})
    journal = []
    for row in agent.db.execute("SELECT * FROM autosent_journal WHERE account=? ORDER BY id DESC", (account,)):
        item = dict(row); item["seen"] = bool(item["seen"]); item["body"] = item["text"]
        skill = agent.db.execute("SELECT * FROM skills WHERE id=?", (item["skill_id"],)).fetchone()
        if skill:
            from .skills import title as skill_title
            decoded = dict(skill); decoded["config"] = json.loads(decoded["config"])
            item["skill_title"] = skill_title(decoded)
        else:
            item["skill_title"] = f"Draft Skill {item['skill_id']} · revision {item['skill_revision']}"
        journal.append(item)
    unseen = sum(not x["seen"] for x in journal)
    qualified = [rule for rule in rules if rule["qualified"]]
    return {"available": bool(account), "current_account": account, "enabled": enabled,
            "special_powers": qualified, "autosent": journal, "unread_count": unseen,
            # Compatibility aliases for code that consumes the lower-level model.
            "account": account, "rules": rules, "journal": journal, "unseen": unseen}


def mark_seen(agent, ident, current_account=None):
    initialize(agent.db)
    if type(ident) is not int:
        raise ValueError("Choose an Autosent record")
    account = (current_account or "").strip().casefold()
    with agent.db:
        if account:
            changed = agent.db.execute("UPDATE autosent_journal SET seen=1 WHERE id=? AND account=?",
                                       (ident, account)).rowcount
        else:
            changed = agent.db.execute("UPDATE autosent_journal SET seen=1 WHERE id=?", (ident,)).rowcount
    if not changed:
        raise ValueError("Autosent record not found")
    return {"seen": True, "id": ident}


def validate_skill_request(agent, data, current_account):
    if type(data) is not dict or set(data) != {"skill_id", "revision"}:
        raise ValueError("Choose the displayed Draft Skill revision")
    if type(data["skill_id"]) is not int or type(data["revision"]) is not int:
        raise ValueError("Choose the displayed Draft Skill revision")
    skill = agent.db.execute("SELECT * FROM skills WHERE id=?", (data["skill_id"],)).fetchone()
    if not skill or skill["family"] != "draft" or skill["status"] != "active" or skill["revision"] != data["revision"]:
        raise ValueError("The Draft Skill changed. Refresh and try again")
    account = (current_account or "").strip().casefold()
    if not account or not _qualification(agent, skill, account)["qualified"]:
        raise ValueError("This Draft Skill has no current auto-send permission")
    return dict(skill)


_RISK = re.compile(
    r"(?:\b(?:pay(?:ment)?|invoice|wire|bank|transfer|refund|purchase|buy|price|cost|fee|salary|"
    r"contract|agreement|legal|lawsuit|court|attorney|lawyer|nda|terms|liability|"
    r"password|passcode|otp|verification code|ssn|passport|medical|diagnosis|tax|crypto|bitcoin)\b|"
    r"\b(?:usd|eur|gbp|rub|dollars?|euros?|pounds?|rubles?)\b|[$€£¥₽]|"
    r"\b(?:оплат\w*|покуп\w*|купить|сч[её]т\w*|перевод\w*|банк\w*|договор\w*|юрид\w*|суд\w*|"
    r"доллар\w*|евро|рубл\w*|"
    r"парол\w*|код подтверждения|паспорт\w*|медицин\w*|диагноз\w*|налог\w*)\b)", re.I)

_PROMPT_INJECTION = re.compile(
    r"(?:\b(?:ignore|disregard|override|forget|bypass)\b.{0,80}"
    r"\b(?:previous|prior|above|system|developer|instructions?|rules?|prompt)\b|"
    r"\b(?:system|developer)\s+(?:message|prompt|instructions?)\b|"
    r"\byou\s+are\s+(?:chatgpt|an?\s+(?:ai|assistant|agent))\b|"
    r"\b(?:игнорир\w*|забуд\w*|обойди\w*|отмени\w*)\b.{0,80}"
    r"\b(?:предыдущ\w*|системн\w*|инструкц\w*|правил\w*|промпт\w*)\b|"
    r"\bсистемн\w*\s+(?:сообщен\w*|промпт\w*|инструкц\w*)\b)", re.I | re.S)


def _hard_risk_reason(*parts):
    content = "\n".join(str(part or "") for part in parts)
    if re.search(r"\[\s*(?:(?:your|user|sender|recipient|full|first|last)[ _-]+)?name\s*\]", content, re.I):
        return "Unresolved name placeholder requires review"
    if _PROMPT_INJECTION.search(content):
        return "Possible prompt injection requires review"
    if _RISK.search(content):
        return "Money, legal, or sensitive content requires review"
    return ""


def _attachment_present(binding):
    keys = set(binding.keys())
    for key in ("has_attachments", "has_attachment", "attachment"):
        if key in keys and bool(binding[key]):
            return True
    return False


def _deny(reason):
    return {"eligible": False, "queued": False, "reason": reason}


def authorize_auto(agent, action_id):
    """Prepare, but do not execute, one automatic-send authorization."""
    initialize(agent.db)
    try:
        action = agent.get(action_id)
    except ValueError:
        return _deny("Unsupported or missing action")
    if action["transport"] != "gmail" or action["proposal"].get("action") not in {"draft", "send"}:
        return _deny("Only a supported Gmail reply can be auto-sent")
    if action["status"] not in {"pending", "ready"}:
        return _deny("The reply is stale or is not ready")
    account, binding = _account_for_action(agent, action)
    if not binding or str(binding["source_role"]) != "incoming":
        return _deny("Only incoming Gmail messages are supported")
    setting = agent.db.execute("SELECT * FROM superpower_settings WHERE singleton=1").fetchone()
    if not setting or not setting["enabled"] or setting["account"] != account:
        return _deny("Superpowers are not enabled for the current Gmail account")
    p = action["proposal"]
    if p.get("suspicious") or p.get("needs_human") or p.get("sensitive"):
        return _deny("Safety review is required")
    if _attachment_present(binding):
        return _deny("Messages with attachments require review")
    reply = action.get("reply")
    if not reply:
        return _deny("A saved reply is required")
    email = agent.email_for(action_id)
    if reply["recipient"].strip().casefold() != email.sender.strip().casefold():
        return _deny("The recipient is not exactly the incoming sender")
    hard_risk = _hard_risk_reason(email.subject, email.body, reply["subject"], reply["text"])
    if hard_risk:
        return _deny(hard_risk)
    application = agent.db.execute("""SELECT * FROM superpower_applications
        WHERE action_id=? AND revoked_at=''""", (action_id,)).fetchone()
    if not application or application["account"] != account:
        return _deny("No account-bound Draft Skill application exists")
    skill = agent.db.execute("SELECT * FROM skills WHERE id=?", (application["skill_id"],)).fetchone()
    if (not skill or skill["family"] != "draft" or skill["status"] != "active" or
            skill["revision"] != application["skill_revision"]):
        return _deny("The exact Draft Skill revision is no longer active")
    qualification = _qualification(agent, skill, account)
    if not qualification["qualified"]:
        return _deny(f"Draft Skill needs {QUALIFICATION_THRESHOLD} successful manual confirmations")
    with agent.db:
        agent.db.execute("""INSERT OR IGNORE INTO autosent_journal
            (action_id,action_revision,skill_id,skill_revision,account,recipient,subject,text,reason,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""", (action_id, action["revision"], skill["id"], skill["revision"],
            account, reply["recipient"], reply["subject"], reply["text"],
            "Qualified Draft Skill; Gmail execution still pending", _now()))
        journal = agent.db.execute("""SELECT * FROM autosent_journal
            WHERE action_id=? AND action_revision=?""", (action_id, action["revision"])).fetchone()
    return {"eligible": True, "queued": True, "reason": journal["reason"],
            "authorization_id": journal["id"], "skill_id": skill["id"],
            "skill_revision": skill["revision"]}


def maybe_queue_auto_send(agent, action_id):
    """Authorize an exact verified draft and put its send into the durable queue."""
    result = authorize_auto(agent, action_id)
    if not result["eligible"]:
        return False
    action = agent.get(action_id)
    reply = agent.db.execute("SELECT * FROM gmail_replies WHERE action_id=? AND revision=?",
                             (action_id, action["revision"])).fetchone()
    if not reply or not reply["raw"] or not reply["draft_id"] or reply["sent_id"]:
        # Authorization is retained as an honest audit record, but it was never
        # queued and must not appear as delivered.
        with agent.db:
            agent.db.execute("""UPDATE autosent_journal SET status='blocked',delivery_status='not_queued',
                reason='The verified Gmail draft is missing or stale' WHERE id=?""",
                (result["authorization_id"],))
        return False
    digest = hashlib.sha256(reply["raw"].encode()).hexdigest()
    with agent.db:
        agent.db.execute("UPDATE gmail_replies SET approved_hash=? WHERE action_id=? AND revision=?",
                         (digest, action_id, action["revision"]))
        agent.db.execute("""UPDATE autosent_journal SET status='queued',delivery_status='pending'
            WHERE id=?""", (result["authorization_id"],))
        try:
            agent.queue_gmail(action_id, "send:" + str(action["revision"]), approved=True,
                              automatic=True)
        except Exception:
            agent.db.execute("""UPDATE autosent_journal SET status='blocked',delivery_status='not_queued',
                reason='Automatic send could not be queued' WHERE id=?""",
                (result["authorization_id"],))
            raise
    return True


def validate_automatic_send(agent, action, reply, binding):
    """Revalidate automatic authority immediately before the one Gmail mutation."""
    initialize(agent.db)
    action = dict(action); reply = dict(reply); binding = dict(binding)
    operation = agent.db.execute("""SELECT * FROM gmail_operations WHERE action_id=? AND revision=?
        AND operation=?""", (action["id"], action["revision"],
        "send:" + str(action["revision"]))).fetchone()
    if not operation or not operation["automatic"] or not operation["approved"]:
        raise ValueError("Automatic-send queue authorization is missing")
    journal = agent.db.execute("""SELECT * FROM autosent_journal
        WHERE action_id=? AND action_revision=?""", (action["id"], action["revision"])).fetchone()
    if not journal or journal["status"] not in {"queued", "processing"}:
        raise ValueError("Automatic-send authorization is stale")
    account = str(binding["account"]).strip().casefold()
    setting = agent.db.execute("SELECT * FROM superpower_settings WHERE singleton=1").fetchone()
    if not setting or not setting["enabled"] or setting["account"] != account or journal["account"] != account:
        raise ValueError("Superpowers are no longer enabled for this Gmail account")
    if str(binding["source_role"]) != "incoming" or _attachment_present(binding):
        raise ValueError("The source message is not eligible for automatic reply")
    p = action["proposal"]
    if action["transport"] != "gmail" or p.get("action") not in {"draft", "send"} or \
            p.get("suspicious") or p.get("needs_human") or p.get("sensitive"):
        raise ValueError("Safety policy blocks automatic sending")
    email = agent.email_for(action["id"])
    if reply["recipient"].strip().casefold() != email.sender.strip().casefold():
        raise ValueError("The recipient changed from the exact incoming sender")
    if any(reply[key] != journal[key] for key in ("recipient", "subject", "text")):
        raise ValueError("The reply changed after automatic authorization")
    if not reply.get("raw") or hashlib.sha256(reply["raw"].encode()).hexdigest() != reply.get("approved_hash"):
        raise ValueError("The exact automatic reply bytes are not authorized")
    hard_risk = _hard_risk_reason(email.subject, email.body, reply["subject"], reply["text"])
    if hard_risk:
        raise ValueError(hard_risk)
    skill = agent.db.execute("SELECT * FROM skills WHERE id=?", (journal["skill_id"],)).fetchone()
    if (not skill or skill["family"] != "draft" or skill["status"] != "active" or
            skill["revision"] != journal["skill_revision"] or not _qualification(agent, skill, account)["qualified"]):
        raise ValueError("Draft Skill permission changed or is no longer qualified")
    with agent.db:
        agent.db.execute("UPDATE autosent_journal SET status='processing' WHERE id=?", (journal["id"],))
    return True


def record_successful_send(agent, action, reply, sent_id, automatic):
    """Finalize Autosent, or learn from one explicitly approved successful send."""
    initialize(agent.db)
    action = dict(action); reply = dict(reply)
    if type(automatic) is not bool or not sent_id:
        raise ValueError("Invalid successful send result")
    if automatic:
        with agent.db:
            changed = agent.db.execute("""UPDATE autosent_journal
                SET status='sent',delivery_status='delivered',sent_id=?,reason='Sent automatically using a qualified Draft Skill'
                WHERE action_id=? AND action_revision=? AND status IN ('queued','processing')""",
                (sent_id, action["id"], action["revision"])).rowcount
        if not changed:
            raise ValueError("Autosent authorization not found")
        return _dict(agent.db.execute("""SELECT * FROM autosent_journal
            WHERE action_id=? AND action_revision=?""", (action["id"], action["revision"])).fetchone())
    # The executor calls this only after Gmail readback has confirmed SENT. Make
    # that fact durable before applying the stricter public recording checks.
    with agent.db:
        agent.db.execute("UPDATE gmail_replies SET sent_id=? WHERE action_id=? AND revision=?",
                         (sent_id, action["id"], action["revision"]))
        agent.db.execute("UPDATE gmail_operations SET status='done' WHERE action_id=? AND revision=? AND operation=?",
                         (action["id"], action["revision"], "send:" + str(action["revision"])))
        agent.db.execute("UPDATE actions SET status='executed' WHERE id=?", (action["id"],))
    fresh_action = agent.get(action["id"])
    fresh_reply = dict(agent.db.execute("SELECT * FROM gmail_replies WHERE action_id=? AND revision=?",
                                        (action["id"], action["revision"])).fetchone())
    return record_manual_send(agent, fresh_action, fresh_reply)


def record_failed_auto(agent, action_id, status):
    initialize(agent.db)
    if status not in {"error", "unknown"}:
        raise ValueError("Automatic send failure must be error or unknown")
    with agent.db:
        changed = agent.db.execute("""UPDATE autosent_journal SET status=?,delivery_status=?,
            reason=? WHERE action_id=? AND status IN ('authorized','queued','processing')""",
            (status, status, "Automatic Gmail send failed" if status == "error" else
             "Automatic Gmail send outcome is unknown; Wajo will not retry", action_id)).rowcount
    return bool(changed)
