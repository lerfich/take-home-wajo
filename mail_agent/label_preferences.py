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

    columns = {r[1] for r in db.execute("PRAGMA table_info(label_feedback)")}
    if "mode" not in columns:
        db.execute("ALTER TABLE label_feedback ADD COLUMN mode TEXT NOT NULL DEFAULT 'replace'")


def initialize_independent(db):
    """A separate, versioned label decision for new unread Gmail messages."""
    db.execute("""CREATE TABLE IF NOT EXISTS independent_label_decisions (
        action_id INTEGER PRIMARY KEY REFERENCES actions(id),
        recommendation TEXT NOT NULL, current_label TEXT NOT NULL,
        kind TEXT NOT NULL, basis TEXT NOT NULL,
        status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
        error TEXT NOT NULL DEFAULT ''
    )""")


def register_independent(agent, action_id, proposal, preference, *, enabled):
    if not enabled:
        return
    label = normalize_label(proposal.label)
    basis = ("Reviewed Label Skill" if preference and preference.get("id", 0) < 0 else
             "Saved label preference" if preference else "Model suggestion")
    agent.db.execute("""INSERT OR IGNORE INTO independent_label_decisions
        (action_id,recommendation,current_label,kind,basis,status)
        VALUES(?,?,?,?,?,'awaiting_confirmation')""",
        (action_id, label, label, proposal.label_kind, basis))
    if agent.db.execute("SELECT changes()").fetchone()[0]:
        agent.log(action_id, "independent_label_proposed", {"label": label, "basis": basis})


def public_independent(db, action_id):
    row = db.execute("SELECT * FROM independent_label_decisions WHERE action_id=?", (action_id,)).fetchone()
    return dict(row) if row else None


def decide_independent(agent, action_id, revision, choice, label=""):
    """Confirm/change or skip this label without touching Reply, Archive or Event."""
    if choice not in {"confirm", "skip"}:
        raise ValueError("Choose Confirm label or No label")
    with agent.db:
        agent.db.execute("BEGIN IMMEDIATE")
        action = agent.get(action_id)
        row = public_independent(agent.db, action_id)
        if (not row or row["revision"] != revision or row["status"] != "awaiting_confirmation"
                or action["transport"] != "gmail"):
            raise ValueError("This label decision changed. Refresh the email before deciding")
        binding = agent.db.execute("SELECT * FROM gmail_bindings WHERE email_id=?", (action["email_id"],)).fetchone()
        if not binding or not binding["initial_unread"]:
            raise ValueError("Only an unread connected Gmail message has this label decision")
        if choice == "skip":
            agent.db.execute("UPDATE independent_label_decisions SET status='skipped' WHERE action_id=?", (action_id,))
            agent.log(action_id, "independent_label_skipped", {"revision": revision})
            return public_independent(agent.db, action_id)
        target = normalize_label(label or row["current_label"])
        if target != row["current_label"]:
            revision += 1
        # One durable external operation; never mutate the primary action's
        # status/revision, since a reply or event may be pending concurrently.
        agent.db.execute("""UPDATE independent_label_decisions
            SET current_label=?,status='executing',revision=?,error='' WHERE action_id=?""",
            (target, revision, action_id))
        agent.db.execute("""INSERT INTO gmail_operations
            (action_id,revision,operation,status,approved,feedback_scope,automatic)
            VALUES(?,?,'label-independent','queued',1,'general',0)""", (action_id, revision))
        agent.log(action_id, "independent_label_queued", {"label": target, "revision": revision})
        return public_independent(agent.db, action_id)


def _finish_independent(agent, row):
    action = agent.get(row["action_id"])
    email = agent.email_for(action["id"])
    agent.db.execute("INSERT OR IGNORE INTO labels VALUES(?,?)", (email.id, row["current_label"]))
    agent.db.execute("""UPDATE independent_label_decisions
        SET status='confirmed',error='' WHERE action_id=?""", (action["id"],))
    # Existing Label Skill collection consumes this verified feedback. Its
    # source is the independent decision, not approval of the primary action.
    agent.db.execute("""INSERT OR IGNORE INTO label_feedback
        (action_id,revision,old_label,new_label,scope,account,kind,status,created_at,mode)
        VALUES(?,?,?,?,?,?,?,'verified',?,'add')""",
        (action["id"], row["revision"], row["recommendation"], row["current_label"], "*",
         account_for(agent, email.id), row["kind"], datetime.now(timezone.utc).isoformat()))
    # A verified choice may suggest a Label Skill, but confirmation of one
    # message must not silently activate a rule for later messages.
    agent.log(action["id"], "independent_label_confirmed", {"label": row["current_label"]})


def run_independent(agent, executor, candidate, check_only):
    """Versioned Gmail label write; uncertain results get read-only reconciliation."""
    from .gmail_executor import ScopeError
    operation = dict(candidate)
    allowed = {"unknown", "error"} if check_only else {"queued"}
    if operation["status"] not in allowed:
        raise ValueError("Label operation is not available")
    try:
        with agent.db:
            agent.db.execute("BEGIN IMMEDIATE")
            action = agent.get(operation["action_id"])
            row = public_independent(agent.db, action["id"])
            binding = agent.db.execute("SELECT * FROM gmail_bindings WHERE email_id=?", (action["email_id"],)).fetchone()
            if (not row or row["revision"] != operation["revision"] or not operation["approved"]
                    or operation["operation"] != "label-independent" or action["transport"] != "gmail"
                    or not binding or not binding["initial_unread"] or row["status"] not in
                    ({"unknown", "error"} if check_only else {"executing"})):
                raise ScopeError("The label decision version or permission is invalid")
            label = normalize_label(row["current_label"])
            agent.db.execute("UPDATE gmail_operations SET status='processing',error='' WHERE id=?", (operation["id"],))
        result = executor.apply(dict(binding), "label", label, check_only=check_only)
        with agent.db:
            agent.db.execute("BEGIN IMMEDIATE")
            if result.get("conflict"):
                status, reason = "error", "Two AI labels already exist. Choose labels before trying again."
            elif not result["verified"]:
                status = "error" if check_only else "unknown"
                reason = "Gmail does not confirm the label. Check status without repeating the write."
            else:
                _finish_independent(agent, row)
                agent.db.execute("UPDATE gmail_operations SET status='done',error='' WHERE id=?", (operation["id"],))
                return True
            agent.db.execute("UPDATE gmail_operations SET status=?,error=? WHERE id=?", (status, reason, operation["id"]))
            agent.db.execute("UPDATE independent_label_decisions SET status=?,error=? WHERE action_id=?",
                             (status, reason, action["id"]))
        return True
    except Exception as exc:
        agent.db.rollback()
        status = "error" if isinstance(exc, ScopeError) else "unknown"
        reason = (str(exc) if isinstance(exc, ScopeError) else
                  "Gmail label result is uncertain. Check status without repeating the write.")
        with agent.db:
            agent.db.execute("UPDATE gmail_operations SET status=?,error=? WHERE id=?",
                             (status, reason, operation["id"]))
            agent.db.execute("UPDATE independent_label_decisions SET status=?,error=? WHERE action_id=?",
                             (status, reason, operation["action_id"]))
            agent.log(operation["action_id"], "independent_label_" + status,
                      {"reason": reason, "exception_type": type(exc).__name__})
        return True


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
    from .skills import choose as choose_skill
    skill = choose_skill(agent, 'labels', email, proposal)
    if skill:
        names = skill['config']['labels']
        return replace(proposal, label=names[0]), {'id': -skill['id'], 'feedback_id': skill['source_id'],
            'kind': proposal.label_kind, 'label': names[0], 'labels': names, 'skill_revision': skill['revision']}
    if (proposal.action != "label" or proposal.suspicious or proposal.needs_human
            or proposal.label_kind not in LABEL_KINDS or not proposal.pattern_evidence.strip()
            or proposal.pattern_evidence not in email.body):
        return proposal, None
    row = agent.db.execute("""SELECT * FROM label_rules WHERE account=? AND kind=? AND active=1
                            AND NOT EXISTS (SELECT 1 FROM skill_legacy_links l WHERE l.family='labels' AND l.legacy_key=CAST(label_rules.id AS TEXT))
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
        if preference.get('labels'):
            import json
            agent.db.execute('INSERT OR REPLACE INTO label_targets VALUES(?,?,?)',
                (action_id, json.dumps(preference['labels']), -preference['id']))
        agent.log(action_id, "label_preference_applied", {"rule_id": preference["id"],
                  "feedback_id": preference["feedback_id"], "kind": proposal.label_kind, "label": proposal.label})


def current_rule_valid(agent, action_id):
    review = agent.db.execute("SELECT * FROM label_reviews WHERE action_id=?", (action_id,)).fetchone()
    if not review or review["preference_id"] is None:
        return True
    from .core import Proposal
    action = agent.get(action_id)
    _, rule = choose(agent, Proposal(**action["proposal"]), agent.email_for(action_id))
    from .multi_labels import targets
    return bool(rule and rule["id"] == review["preference_id"] and rule["label"] == review["current_label"]
                and rule.get('labels', [rule['label']]) == targets(agent, action_id, rule['label']))


def submit(agent, action_id, revision, label, scope, mode="replace"):
    from .core import Proposal, decide
    if scope not in {"email", "similar", "sender"}:
        raise ValueError("Invalid preference scope. Refresh the email and try again.")
    if mode not in {"replace", "add"}:
        raise ValueError("Choose Replace or Add")
    if mode == "add" and scope != "email":
        raise ValueError("Save the additional label on this email before reviewing its suggested Skill.")
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
        from .multi_labels import current, conflict
        existing = current(agent, email.id)
        wanted = set(existing) | {name}
        if mode == 'replace' and review['current_label'] != name:
            wanted.discard(review['current_label'])
        if len(wanted) > 2:
            return conflict(agent, action_id, [name], existing)
        if scope != "email" and (p.label_kind not in LABEL_KINDS or not p.pattern_evidence.strip()
                                  or p.pattern_evidence not in email.body):
            raise ValueError("Wajo cannot identify a reliable matching context in this email. A preference for future emails was not created.")
        new_revision = revision + 1
        account = account_for(agent, email.id)
        key = "email" if scope == "email" else "*" if scope == "similar" else email.sender.casefold()
        agent.db.execute("""INSERT INTO label_feedback(action_id,revision,old_label,new_label,scope,account,kind,status,created_at,mode)
                            VALUES(?,?,?,?,?,?,?,'pending',?,?)""",
                         (action_id, new_revision, review["current_label"], name, key, account, review["kind"],
                          datetime.now(timezone.utc).isoformat(), mode))
        # Stop using the superseded rule while Gmail verification is pending.
        if scope != "email":
            agent.db.execute("UPDATE label_rules SET active=0 WHERE account=? AND kind=? AND scope=?",
                             (account, review["kind"], key))
        agent.db.execute("UPDATE actions SET revision=? WHERE id=?", (new_revision, action_id))
        agent.db.execute("UPDATE label_reviews SET status='saving' WHERE action_id=?", (action_id,))
        agent.log(action_id, "label_review_submitted", {"revision": new_revision, "label": name, "scope": key, "mode": mode})
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
    if feedback["mode"] == "replace":
        agent.db.execute("DELETE FROM labels WHERE email_id=? AND label=?", (action["email_id"], feedback["old_label"]))
    agent.db.execute("INSERT OR IGNORE INTO labels VALUES(?,?)", (action["email_id"], feedback["new_label"]))
    current_label = feedback["old_label"] if feedback["mode"] == "add" else feedback["new_label"]
    proposal = {**action["proposal"], "label": current_label}
    agent.db.execute("UPDATE actions SET proposal=?,status='executed' WHERE id=?", (json.dumps(proposal),action_id))
    agent.db.execute("UPDATE label_reviews SET current_label=?,status='reviewed',preference_id=NULL,basis='Reviewed by you' WHERE action_id=?",
                     (current_label,action_id))
    agent.db.execute("UPDATE label_feedback SET status='verified' WHERE id=?", (feedback["id"],))
    if feedback["scope"] != "email":
        agent.db.execute("""INSERT INTO label_rules(account,kind,scope,label,feedback_id,active) VALUES(?,?,?,?,?,1)
            ON CONFLICT(account,kind,scope) DO UPDATE SET label=excluded.label,feedback_id=excluded.feedback_id,active=1""",
                         (feedback["account"],feedback["kind"],feedback["scope"],feedback["new_label"],feedback["id"]))
    agent.log(action_id,"label_reviewed", {"feedback_id":feedback["id"],"label":feedback["new_label"],"scope":feedback["scope"],"mode":feedback["mode"]})


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
        existing = sorted(x['name'] for x in labels if x.get('type') == 'user' and x['id'] in ids and x['name'].startswith('AI: '))
        wanted = set(existing) | {new}
        if feedback['mode'] == 'replace' and old != new:
            wanted.discard(old)
        if len(wanted) > 2:
            from .multi_labels import conflict
            with agent.db:
                conflict(agent, action['id'], [new], existing)
                agent.db.execute("UPDATE gmail_operations SET status='error',error='Choose two additional labels' WHERE id=?", (row['id'],))
                agent.db.execute("UPDATE actions SET status='error' WHERE id=?", (action['id'],))
                agent.db.execute("UPDATE label_feedback SET status='conflict' WHERE id=?", (feedback['id'],))
            return True
        verified = new_id in ids and (feedback["mode"] == "add" or old == new or old_id not in ids)
        if not verified and not check_only:
            if old_id not in ids:
                raise ScopeError("The previous label changed in Gmail. Review the message there; Wajo will not overwrite it.")
            if new_id is None:
                new_id = executor.api.users().labels().create(userId="me",body={"name":new,
                         "labelListVisibility":"labelShow","messageListVisibility":"show"}).execute()["id"]
            executor.api.users().messages().modify(userId="me",id=binding["message_id"],body={
                "addLabelIds":[new_id],"removeLabelIds":[old_id] if feedback["mode"] == "replace" and old != new else []}).execute()
            labels,ids = executor.inspect(dict(binding))
            verified = new_id in ids and (feedback["mode"] == "add" or old == new or old_id not in ids)
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
