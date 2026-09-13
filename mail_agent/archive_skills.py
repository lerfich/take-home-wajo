"""Independent archive/keep decisions and automatically qualified semantic Skills."""
from datetime import datetime, timezone
import json
import re

from .semantic_matcher import SemanticContext, compare


def _now():
    return datetime.now(timezone.utc).isoformat()


def initialize(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS archive_decisions (
        action_id INTEGER PRIMARY KEY REFERENCES actions(id), account TEXT NOT NULL,
        recommendation TEXT NOT NULL, chosen TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL, evidence TEXT NOT NULL, context TEXT NOT NULL,
        status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
        skill_id INTEGER, skill_revision INTEGER NOT NULL DEFAULT 0,
        automatic INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS archive_skill_feedback (
        id INTEGER PRIMARY KEY, action_id INTEGER NOT NULL UNIQUE REFERENCES actions(id),
        account TEXT NOT NULL, recommendation TEXT NOT NULL, chosen TEXT NOT NULL,
        agreed INTEGER NOT NULL, eligible INTEGER NOT NULL DEFAULT 1,
        context TEXT NOT NULL, created_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS archive_skills (
        id INTEGER PRIMARY KEY, account TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
        revision INTEGER NOT NULL DEFAULT 1, result TEXT NOT NULL, context TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    """)
    columns = {row["name"] for row in db.execute("PRAGMA table_info(archive_skill_feedback)")}
    if "eligible" not in columns:
        db.execute("ALTER TABLE archive_skill_feedback ADD COLUMN eligible INTEGER NOT NULL DEFAULT 1")


def evidence_is_exact(body, evidence):
    """Allow MIME wrapping differences, but never invented archive evidence."""
    normalize = lambda value: re.sub(r"\s+", " ", value).strip()
    return bool(normalize(evidence)) and normalize(evidence) in normalize(body)


def context_for(email, proposal):
    meaning = proposal.pattern if proposal.pattern != "unknown" else proposal.label_kind
    if meaning in {"", "unknown"}:
        meaning = "unclassified-mail"
    return SemanticContext(meaning=meaning, subtopic=proposal.label_kind,
                           subject=email.subject, sender=email.sender,
                           evidence=proposal.archive_evidence or proposal.pattern_evidence,
                           suspicious=proposal.suspicious)


def _coerce(row):
    item = dict(row)
    item["context"] = json.loads(item["context"])
    item["automatic"] = bool(item.get("automatic", 0))
    return item


def list_skills(db, account=None):
    query = "SELECT * FROM archive_skills WHERE status IN ('active','paused')"
    args = ()
    if account:
        query += " AND account=?"; args = (account,)
    return [_coerce(row) for row in db.execute(query + " ORDER BY id DESC", args)]


def choose(db, account, context, *, include_paused=False):
    statuses = "('active','paused')" if include_paused else "('active')"
    for row in db.execute(f"SELECT * FROM archive_skills WHERE account=? AND status IN {statuses} ORDER BY id DESC", (account,)):
        skill = _coerce(row)
        if compare(skill["context"], context).matched:
            return skill
    return None


def blockers(agent, email, proposal):
    reasons = []
    if any((proposal.suspicious, proposal.needs_human, proposal.requires_action,
            proposal.has_deadline, proposal.significant_change, proposal.sensitive,
            proposal.notify)):
        reasons.append("risk or attention signals require a current decision")
    rules = agent.db.execute("SELECT 1 FROM archive_rules WHERE scope IN ('*',?) AND pattern IN ('*',?) LIMIT 1",
                             (email.sender.casefold(), proposal.pattern)).fetchone()
    if rules:
        reasons.append("the sender is kept in the inbox")
    from .attention import matches
    if matches(agent, email, proposal):
        reasons.append("a Needs attention preference applies")
    return reasons


def _queue(agent, action_id, operation, *, automatic):
    action = agent.get(action_id)
    agent.db.execute("""INSERT INTO gmail_operations
        (action_id,revision,operation,status,approved,feedback_scope,automatic)
        VALUES(?,?,?,'queued',1,'general',?)""",
        (action_id, action["revision"], operation, int(automatic)))
    agent.log(action_id, "gmail_queued", {"operation": operation, "transport": "gmail",
              "archive_decision": True, "automatic": automatic})


def register(agent, action_id, email, proposal, *, read_before_wajo=False):
    if read_before_wajo or proposal.archive_recommendation == "none":
        return None
    from .label_preferences import account_for
    account = account_for(agent, email.id)
    context = context_for(email, proposal)
    skill = choose(agent.db, account, context)
    result = skill["result"] if skill else proposal.archive_recommendation
    blocked = blockers(agent, email, proposal) if result == "archive" else []
    automatic = bool(skill and not blocked)
    status = "automatic" if automatic and result == "keep" else "executing" if automatic else "awaiting_confirmation"
    now = _now()
    agent.db.execute("""INSERT OR REPLACE INTO archive_decisions
        (action_id,account,recommendation,chosen,reason,evidence,context,status,revision,
         skill_id,skill_revision,automatic,error,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?)""",
        (action_id, account, result, result if automatic else "",
         (f"Archive Skill recommends {result.replace('_', ' ')}."
          if skill else proposal.archive_reason), proposal.archive_evidence,
         json.dumps(context.to_dict(), ensure_ascii=False),
         status, skill["id"] if skill else None, skill["revision"] if skill else 0,
         int(automatic), "", now, now))
    if automatic and result == "archive":
        binding = agent.db.execute("SELECT 1 FROM gmail_bindings WHERE email_id=?", (email.id,)).fetchone()
        if binding:
            _queue(agent, action_id, "archive-independent", automatic=True)
        else:
            agent.db.execute("UPDATE emails SET archived=1 WHERE id=?", (email.id,))
            agent.db.execute("UPDATE archive_decisions SET status='automatic',updated_at=? WHERE action_id=?",
                             (_now(), action_id))
    return public(agent.db, action_id)


def public(db, action_id):
    row = db.execute("SELECT * FROM archive_decisions WHERE action_id=?", (action_id,)).fetchone()
    return _coerce(row) if row else None


def _matching_streak(db, feedback):
    wanted = json.loads(feedback["context"]); result = feedback["chosen"]
    streak = []
    for row in db.execute("SELECT * FROM archive_skill_feedback WHERE account=? ORDER BY id DESC", (feedback["account"],)):
        if not compare(json.loads(row["context"]), wanted).matched:
            continue
        if not row["eligible"] or not row["agreed"] or row["chosen"] != result:
            break
        streak.append(row)
        if len(streak) == 3:
            break
    return streak


def _record_feedback(agent, action_id, recommendation, chosen, context):
    from .label_preferences import account_for
    email = agent.email_for(action_id); account = account_for(agent, email.id)
    agreed = recommendation == chosen
    action = agent.get(action_id)
    from .core import Proposal
    proposal = Proposal(**action["proposal"])
    real_binding = agent.db.execute(
        "SELECT 1 FROM gmail_bindings WHERE email_id=?", (email.id,)
    ).fetchone() is not None
    eligible = bool(real_binding and not blockers(agent, email, proposal))
    agent.db.execute("""INSERT OR REPLACE INTO archive_skill_feedback
        (action_id,account,recommendation,chosen,agreed,eligible,context,created_at)
        VALUES(?,?,?,?,?,?,?,?)""",
        (action_id, account, recommendation, chosen, int(agreed), int(eligible),
         json.dumps(context, ensure_ascii=False), _now()))
    feedback = agent.db.execute("SELECT * FROM archive_skill_feedback WHERE action_id=?", (action_id,)).fetchone()
    streak = _matching_streak(agent.db, feedback)
    if eligible and agreed and len(streak) == 3 and not choose(agent.db, account, context, include_paused=True):
        now = _now()
        cursor = agent.db.execute("""INSERT INTO archive_skills
            (account,status,revision,result,context,created_at,updated_at)
            VALUES(?,'active',1,?,?,?,?)""",
            (account, chosen, json.dumps(context, ensure_ascii=False), now, now))
        skill_id = cursor.lastrowid
        agent.log(action_id, "archive_skill_qualified", {"skill_id": skill_id, "result": chosen,
                  "confirmations": [row["action_id"] for row in reversed(streak)]})
        return skill_id
    return None


def decide(agent, action_id, revision, choice):
    if choice not in {"archive", "keep"}:
        raise ValueError("Choose Archive or Keep in inbox")
    row = agent.db.execute("SELECT * FROM archive_decisions WHERE action_id=?", (action_id,)).fetchone()
    if not row or row["status"] != "awaiting_confirmation" or row["revision"] != revision:
        raise ValueError("This archive decision changed. Refresh the email and review it again.")
    automatic = False; status = "executing" if choice == "archive" else "confirmed"
    agent.db.execute("UPDATE archive_decisions SET chosen=?,status=?,updated_at=? WHERE action_id=?",
                     (choice, status, _now(), action_id))
    if choice == "archive":
        binding = agent.db.execute("SELECT 1 FROM gmail_bindings WHERE email_id=?", (agent.get(action_id)["email_id"],)).fetchone()
        if binding:
            _queue(agent, action_id, "archive-independent", automatic=automatic)
        else:
            agent.db.execute("UPDATE emails SET archived=1 WHERE id=?", (agent.get(action_id)["email_id"],))
            agent.db.execute("UPDATE archive_decisions SET status='confirmed',updated_at=? WHERE action_id=?", (_now(), action_id))
            _record_feedback(agent, action_id, row["recommendation"], choice, json.loads(row["context"]))
    else:
        _record_feedback(agent, action_id, row["recommendation"], choice, json.loads(row["context"]))
    agent.log(action_id, "archive_decision_confirmed", {"recommendation": row["recommendation"],
              "chosen": choice, "agreed": row["recommendation"] == choice})
    return public(agent.db, action_id)


def correct_automatic_archive(agent, action_id):
    row = agent.db.execute("SELECT * FROM archive_decisions WHERE action_id=?", (action_id,)).fetchone()
    if not row or row["status"] != "automatic" or row["chosen"] not in {"archive", "keep"} or not row["skill_id"]:
        raise ValueError("Only an automatic Archive Skill decision can use this correction")
    skill = agent.db.execute("SELECT * FROM archive_skills WHERE id=? AND status='active'", (row["skill_id"],)).fetchone()
    if not skill:
        raise ValueError("The Archive Skill is no longer active")
    agent.db.execute("UPDATE archive_skills SET status='deleted',revision=revision+1,updated_at=? WHERE id=?",
                     (_now(), skill["id"]))
    corrected_choice = "keep" if row["chosen"] == "archive" else "archive"
    _record_feedback(agent, action_id, row["recommendation"], corrected_choice, json.loads(row["context"]))
    agent.db.execute("UPDATE archive_decisions SET chosen=?,status='correcting',automatic=0,updated_at=? WHERE action_id=?",
                     (corrected_choice, _now(), action_id))
    binding = agent.db.execute("SELECT 1 FROM gmail_bindings WHERE email_id=?", (agent.get(action_id)["email_id"],)).fetchone()
    if binding:
        _queue(agent, action_id,
               "restore-independent" if corrected_choice == "keep" else "archive-independent",
               automatic=False)
    else:
        agent.db.execute("UPDATE emails SET archived=? WHERE id=?",
                         (int(corrected_choice == "archive"), agent.get(action_id)["email_id"]))
        agent.db.execute("UPDATE archive_decisions SET status='corrected',updated_at=? WHERE action_id=?", (_now(), action_id))
    agent.log(action_id, "archive_skill_revoked", {"skill_id": skill["id"], "reason": "Automatic archive corrected"})
    return public(agent.db, action_id)


def manage(db, skill_id, revision, operation, account):
    if operation not in {"pause", "resume", "delete"}:
        raise ValueError("Choose Pause, Resume or Delete")
    row = db.execute("SELECT * FROM archive_skills WHERE id=? AND account=?", (skill_id, account)).fetchone()
    if not row or row["revision"] != revision:
        raise ValueError("The Archive Skill changed. Refresh and try again.")
    expected = {"pause": "active", "resume": "paused"}
    if operation in expected and row["status"] != expected[operation]:
        raise ValueError("The Archive Skill is not in the required state")
    status = {"pause": "paused", "resume": "active", "delete": "deleted"}[operation]
    db.execute("UPDATE archive_skills SET status=?,revision=revision+1,updated_at=? WHERE id=?",
               (status, _now(), skill_id))
    if operation == "delete":
        context = json.loads(row["context"])
        feedback_ids = [feedback["id"] for feedback in db.execute(
            "SELECT * FROM archive_skill_feedback WHERE account=?", (account,)
        ) if compare(json.loads(feedback["context"]), context).matched]
        if feedback_ids:
            db.executemany(
                "DELETE FROM archive_skill_feedback WHERE id=?",
                [(feedback_id,) for feedback_id in feedback_ids],
            )
    return {"saved": True, "status": status, "revision": revision + 1}


def run_gmail(agent, executor, operation, check_only=False):
    from .core import Proposal, validate
    from .gmail_executor import ScopeError

    op = dict(operation)
    action = agent.get(op["action_id"])
    decision = agent.db.execute(
        "SELECT * FROM archive_decisions WHERE action_id=?", (action["id"],)
    ).fetchone()
    binding = agent.db.execute(
        "SELECT * FROM gmail_bindings WHERE email_id=?", (action["email_id"],)
    ).fetchone()
    allowed = {"unknown", "error"} if check_only else {"queued"}
    if op["status"] not in allowed:
        raise ValueError("Gmail operation is not available for this request")
    if (not decision or not binding or action["transport"] != "gmail"
            or op["revision"] != action["revision"]):
        raise ScopeError("The independent archive decision is no longer valid")
    actual = "restore" if op["operation"] == "restore-independent" else "archive"
    if actual == "archive" and decision["chosen"] != "archive":
        raise ScopeError("The current archive decision does not permit archiving")
    if actual == "restore" and decision["status"] not in {"correcting", "unknown"}:
        raise ScopeError("The current archive correction does not permit restoring")
    proposal = Proposal(**action["proposal"])
    validate(proposal)
    if actual == "archive" and decision["automatic"]:
        skill = agent.db.execute(
            "SELECT * FROM archive_skills WHERE id=? AND status='active' AND revision=?",
            (decision["skill_id"], decision["skill_revision"]),
        ).fetchone()
        if not skill or blockers(agent, agent.email_for(action["id"]), proposal):
            raise ScopeError("The Archive Skill or its safety conditions changed")
    with agent.db:
        agent.db.execute(
            "UPDATE gmail_operations SET status='processing',error='' WHERE id=?", (op["id"],)
        )
        agent.log(action["id"], "gmail_started", {
            "operation": op["operation"], "check_only": check_only,
            "archive_decision": True,
        })
    try:
        result = executor.apply(dict(binding), actual, check_only=check_only)
    except Exception as exc:
        status = "error" if isinstance(exc, ScopeError) else "unknown"
        reason = (str(exc) if isinstance(exc, ScopeError)
                  else "Gmail result is uncertain. Check Gmail status before any further action.")
        with agent.db:
            agent.db.execute(
                "UPDATE archive_decisions SET status=?,error=?,updated_at=? WHERE action_id=?",
                (status, reason, _now(), action["id"]),
            )
            agent.db.execute(
                "UPDATE gmail_operations SET status=?,error=? WHERE id=?", (status, reason, op["id"])
            )
            agent.log(action["id"], "gmail_" + status, {
                "operation": op["operation"], "reason": reason,
            })
        return True
    with agent.db:
        if result["verified"]:
            archived = actual == "archive"
            agent.db.execute(
                "UPDATE emails SET archived=? WHERE id=?", (int(archived), action["email_id"])
            )
            final = ("corrected" if decision["status"] == "correcting" else
                     "automatic" if decision["automatic"] else "confirmed")
            agent.db.execute(
                "UPDATE archive_decisions SET status=?,error='',updated_at=? WHERE action_id=?",
                (final, _now(), action["id"]),
            )
            agent.db.execute(
                "UPDATE gmail_operations SET status='done',error='' WHERE id=?", (op["id"],)
            )
            if (actual == "archive" and not decision["automatic"]
                    and decision["status"] != "correcting"):
                _record_feedback(agent, action["id"], decision["recommendation"], "archive",
                                 json.loads(decision["context"]))
            agent.log(action["id"], "archive_corrected" if actual == "restore" else "executed", {
                "action": actual, "transport": "gmail", "archive_decision": True,
                **result, "check_only": check_only,
            })
        else:
            status = "error" if check_only else "unknown"
            reason = "Gmail did not confirm this archive change."
            agent.db.execute(
                "UPDATE archive_decisions SET status=?,error=?,updated_at=? WHERE action_id=?",
                (status, reason, _now(), action["id"]),
            )
            agent.db.execute(
                "UPDATE gmail_operations SET status=?,error=? WHERE id=?", (status, reason, op["id"])
            )
            agent.log(action["id"], "gmail_unverified", {
                "operation": op["operation"], "check_only": check_only,
                "archive_decision": True,
            })
    return True
