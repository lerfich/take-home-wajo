"""User-reviewed label names, separate from archive experience and mail permissions."""
from dataclasses import replace
from datetime import datetime, timezone


LABEL_KINDS = {
    "job_application_receipt": "Job application receipt",
    "job_interview": "Interview invitation or scheduling",
    "job_outcome": "Job application outcome",
    "work_review_request": "Work review or feedback request",
    "work_status": "Work progress update",
    "meeting_change": "Meeting change",
    "newsletter": "Newsletter or digest",
    "service_success": "Successful automated operation",
    "service_failure": "Failed operation requiring attention",
    "account_notice": "Account or access notice",
    "settled_receipt": "Receipt for a completed payment",
    "support_receipt": "Support request acknowledgement",
    "support_response": "Substantive support response",
    "travel_confirmation": "Travel booking confirmation",
    "community_event": "Community event invitation",
}


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS label_reviews (
            action_id INTEGER PRIMARY KEY REFERENCES actions(id),
            original_label TEXT NOT NULL, current_label TEXT NOT NULL,
            kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'needs_review',
            preference_id INTEGER, basis TEXT NOT NULL DEFAULT 'Model suggestion');
        CREATE TABLE IF NOT EXISTS label_feedback (
            id INTEGER PRIMARY KEY, action_id INTEGER NOT NULL REFERENCES actions(id),
            revision INTEGER NOT NULL, old_label TEXT NOT NULL, new_label TEXT NOT NULL,
            scope TEXT NOT NULL, account TEXT NOT NULL, kind TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(action_id, revision));
        CREATE TABLE IF NOT EXISTS label_rules (
            id INTEGER PRIMARY KEY, account TEXT NOT NULL, kind TEXT NOT NULL,
            scope TEXT NOT NULL, label TEXT NOT NULL, feedback_id INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, UNIQUE(account,kind,scope));
    """)


def normalize_label(value):
    if type(value) is not str:
        raise ValueError("Enter a label name")
    name = value.strip()
    if not name or name == "AI:":
        raise ValueError("Enter a label name after AI: ")
    if not name.startswith("AI: "):
        name = "AI: " + name
    if not name[4:].strip() or len(name) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("Use a nonempty label name up to 100 characters, without control characters")
    return name


def account_for(agent, email_id):
    row = agent.db.execute("SELECT account FROM gmail_bindings WHERE email_id=?", (email_id,)).fetchone()
    return row["account"] if row else "local_simulation"


def choose(agent, proposal, email):
    if (proposal.action != "label" or proposal.suspicious or proposal.needs_human
            or proposal.label_kind not in LABEL_KINDS or not proposal.pattern_evidence.strip()
            or proposal.pattern_evidence not in email.body):
        return proposal, None
    row = agent.db.execute("""SELECT * FROM label_rules WHERE account=? AND kind=? AND active=1
                            AND scope IN ('*',?) ORDER BY (scope='*') ASC LIMIT 1""",
                           (account_for(agent, email.id), proposal.label_kind, email.sender.casefold())).fetchone()
    if row:
        return replace(proposal, label=row["label"]), dict(row)
    return proposal, None


def register(agent, action_id, original_label, proposal, preference):
    if proposal.action != "label":
        return
    basis = ("Explicit preference from review #" + str(preference["feedback_id"])) if preference else "Model suggestion"
    agent.db.execute("INSERT INTO label_reviews(action_id,original_label,current_label,kind,preference_id,basis) VALUES(?,?,?,?,?,?)",
                     (action_id, original_label, proposal.label, proposal.label_kind,
                      preference["id"] if preference else None, basis))
    if preference:
        agent.log(action_id, "label_preference_applied", {"rule_id": preference["id"],
                  "feedback_id": preference["feedback_id"], "kind": proposal.label_kind, "label": proposal.label})


def current_rule_valid(agent, action_id):
    review = agent.db.execute("SELECT * FROM label_reviews WHERE action_id=?", (action_id,)).fetchone()
    if not review or review["preference_id"] is None:
        return True
    from .core import Proposal
    action = agent.get(action_id)
    _, rule = choose(agent, Proposal(**action["proposal"]), agent.email_for(action_id))
    return bool(rule and rule["id"] == review["preference_id"] and rule["label"] == review["current_label"])


def submit(agent, action_id, revision, label, scope):
    from .core import Proposal, decide
    if scope not in {"email", "similar", "sender"}:
        raise ValueError("Choose this email, similar emails, or similar emails from this sender")
    name = normalize_label(label)
    with agent.db:
        agent.db.execute("BEGIN IMMEDIATE")
        action = agent.get(action_id)
        review = agent.db.execute("SELECT * FROM label_reviews WHERE action_id=?", (action_id,)).fetchone()
        p = Proposal(**action["proposal"])
        if (not review or action["status"] != "executed" or action["revision"] != revision
                or p.action != "label" or decide(p).status != "ready"):
            raise ValueError("This label is not ready for review, or the displayed version changed")
        email = agent.email_for(action_id)
        if scope != "email" and (p.label_kind not in LABEL_KINDS or not p.pattern_evidence.strip()
                                  or p.pattern_evidence not in email.body):
            raise ValueError("This email has no supported, evidenced situation type. Review this email only.")
        new_revision = revision + 1
        account = account_for(agent, email.id)
        key = "email" if scope == "email" else "*" if scope == "similar" else email.sender.casefold()
        agent.db.execute("""INSERT INTO label_feedback(action_id,revision,old_label,new_label,scope,account,kind,status,created_at)
                            VALUES(?,?,?,?,?,?,?,'pending',?)""",
                         (action_id, new_revision, review["current_label"], name, key, account, review["kind"],
                          datetime.now(timezone.utc).isoformat()))
        # Stop using the superseded rule while Gmail verification is pending.
        if scope != "email":
            agent.db.execute("UPDATE label_rules SET active=0 WHERE account=? AND kind=? AND scope=?",
                             (account, review["kind"], key))
        agent.db.execute("UPDATE actions SET revision=? WHERE id=?", (new_revision, action_id))
        agent.db.execute("UPDATE label_reviews SET status='saving' WHERE action_id=?", (action_id,))
        agent.log(action_id, "label_review_submitted", {"revision": new_revision, "label": name, "scope": key})
        if action["transport"] == "gmail":
            agent.queue_gmail(action_id, "label-review:" + str(new_revision), approved=True)
        else:
            finish(agent, action_id, new_revision)
    return agent.get(action_id)


def finish(agent, action_id, revision):
    import json
    feedback = agent.db.execute("SELECT * FROM label_feedback WHERE action_id=? AND revision=?", (action_id,revision)).fetchone()
    if not feedback or feedback["status"] != "pending":
        raise ValueError("Label feedback is missing or already completed")
    action = agent.get(action_id)
    agent.db.execute("DELETE FROM labels WHERE email_id=? AND label=?", (action["email_id"], feedback["old_label"]))
    agent.db.execute("INSERT OR IGNORE INTO labels VALUES(?,?)", (action["email_id"], feedback["new_label"]))
    proposal = {**action["proposal"], "label": feedback["new_label"]}
    agent.db.execute("UPDATE actions SET proposal=?,status='executed' WHERE id=?", (json.dumps(proposal),action_id))
    agent.db.execute("UPDATE label_reviews SET current_label=?,status='reviewed',preference_id=NULL,basis='Reviewed by you' WHERE action_id=?",
                     (feedback["new_label"],action_id))
    agent.db.execute("UPDATE label_feedback SET status='verified' WHERE id=?", (feedback["id"],))
    if feedback["scope"] != "email":
        agent.db.execute("""INSERT INTO label_rules(account,kind,scope,label,feedback_id,active) VALUES(?,?,?,?,?,1)
            ON CONFLICT(account,kind,scope) DO UPDATE SET label=excluded.label,feedback_id=excluded.feedback_id,active=1""",
                         (feedback["account"],feedback["kind"],feedback["scope"],feedback["new_label"],feedback["id"]))
    agent.log(action_id,"label_reviewed", {"feedback_id":feedback["id"],"label":feedback["new_label"],"scope":feedback["scope"]})


def run_review(agent, executor, candidate, check_only):
    """Exactly versioned replacement of one AI label; preserve all unrelated labels."""
    from .core import Proposal, decide
    from .gmail_executor import ScopeError
    row = dict(candidate)
    allowed = {"unknown", "error"} if check_only else {"queued"}
    if row["status"] not in allowed:
        raise ValueError("Label review operation is not available")
    try:
        with agent.db:
            agent.db.execute("BEGIN IMMEDIATE")
            action = agent.get(row["action_id"])
            p = Proposal(**action["proposal"])
            feedback = agent.db.execute("SELECT * FROM label_feedback WHERE action_id=? AND revision=?",
                                        (action["id"],row["revision"])).fetchone()
            binding = agent.db.execute("SELECT * FROM gmail_bindings WHERE email_id=?",(action["email_id"],)).fetchone()
            if (action["revision"] != row["revision"] or row["operation"] != 'label-review:'+str(row["revision"])
                    or not row["approved"] or action["transport"] != "gmail" or not binding
                    or not feedback or feedback["status"] != "pending" or p.action != "label" or decide(p).status != "ready"):
                raise ScopeError("The label review version or permission is invalid")
            old,new = feedback["old_label"],feedback["new_label"]
            if normalize_label(old) != old or normalize_label(new) != new:
                raise ScopeError("Only AI labels may be replaced")
            agent.db.execute("UPDATE gmail_operations SET status='processing',error='' WHERE id=?",(row["id"],))
        labels,ids = executor.inspect(dict(binding))
        def find(name):
            return next((x["id"] for x in labels if x["name"] == name and x.get("type") == "user"),None)
        old_id,new_id = find(old),find(new)
        verified = new_id in ids and (old == new or old_id not in ids)
        if not verified and not check_only:
            if old_id not in ids:
                raise ScopeError("The previous label changed in Gmail. Review the message there; Wajo will not overwrite it.")
            if new_id is None:
                new_id = executor.api.users().labels().create(userId="me",body={"name":new,
                         "labelListVisibility":"labelShow","messageListVisibility":"show"}).execute()["id"]
            executor.api.users().messages().modify(userId="me",id=binding["message_id"],body={
                "addLabelIds":[new_id],"removeLabelIds":[old_id] if old != new else []}).execute()
            labels,ids = executor.inspect(dict(binding))
            verified = new_id in ids and (old == new or old_id not in ids)
        with agent.db:
            if not verified:
                agent.db.execute("UPDATE gmail_operations SET status='unknown',error=? WHERE id=?",
                                 ("Gmail does not confirm the label change. Check status; no automatic retry.",row["id"]))
                agent.db.execute("UPDATE actions SET status='unknown' WHERE id=?",(action["id"],))
                return True
            finish(agent,action["id"],row["revision"])
            agent.db.execute("UPDATE gmail_operations SET status='done',error='' WHERE id=?",(row["id"],))
    except Exception as exc:
        agent.db.rollback()
        status = "error" if isinstance(exc,ScopeError) else "unknown"
        reason = str(exc) if isinstance(exc,ScopeError) else "Gmail label result is uncertain. Check status without retrying the change."
        with agent.db:
            agent.db.execute("UPDATE gmail_operations SET status=?,error=? WHERE id=?",(status,reason,row["id"]))
            agent.db.execute("UPDATE actions SET status=? WHERE id=?",(status,row["action_id"]))
            agent.log(row["action_id"],"label_review_"+status,{"reason":reason,"exception_type":type(exc).__name__})
    return True
