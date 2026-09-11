"""Durable Gmail operations with exact reply approval and no mutation retries."""
import json

from .core import Agent, Proposal, decide, validate


class ScopeError(ValueError):
    """A trusted binding or a current mailbox condition does not permit a write."""


class GmailExecutor:
    def __init__(self, api):
        self.api = api

    def inspect(self, binding):
        account = self.api.users().getProfile(userId="me").execute()["emailAddress"].casefold()
        if account != binding["account"]:
            raise ScopeError("Connected Gmail account differs from the imported account")
        labels = self.api.users().labels().list(userId="me").execute().get("labels", [])
        # Legacy imports remain bound to their exact synthetic test label. New
        # stage-C bindings deliberately use an empty label scope and rely on the
        # exact account/message binding plus current special-folder checks.
        if binding["label_name"]:
            if binding["label_name"] != "Wajo-Test":
                raise ScopeError("Legacy live writes are restricted to Wajo-Test")
            if not any(x["id"] == binding["label_id"] and x["name"] == binding["label_name"] for x in labels):
                raise ScopeError("The selected test label was removed or renamed")
        message = self.api.users().messages().get(
            userId="me", id=binding["message_id"], format="minimal").execute()
        ids = set(message.get("labelIds", []))
        if ((binding["label_id"] and binding["label_id"] not in ids)
                or ids.intersection({"TRASH", "SPAM", "DRAFT", "SENT"})):
            raise ScopeError("Message is outside its saved scope or is in Sent, Trash, Spam or Drafts")
        return labels, ids

    @staticmethod
    def desired(operation, label, labels, ids):
        if operation == "archive":
            return "INBOX" not in ids
        if operation == "restore":
            return "INBOX" in ids
        names = label if isinstance(label, list) else [label]
        return all(any(x['id'] in ids and x['name'] == name and x.get('type') == 'user' for x in labels) for name in names)

    def apply(self, binding, operation, label="", check_only=False):
        if operation not in {"label", "archive", "restore"}:
            raise ScopeError("Only labels, archive and restore are implemented")
        names = label if isinstance(label, list) else [label]
        if operation == 'label':
            from .label_preferences import normalize_label
            if not 1 <= len(names) <= 2 or any(normalize_label(x) != x for x in names):
                raise ScopeError('Invalid AI label set')
        labels, ids = self.inspect(binding)
        if operation == 'label':
            existing = sorted(x['name'] for x in labels if x.get('type') == 'user'
                              and x['id'] in ids and x['name'].startswith('AI: '))
            if len(set(existing) | set(names)) > 2:
                return {'verified': False, 'conflict': True, 'existing': existing}
        if self.desired(operation, label, labels, ids):
            return {"verified": True, "already_present": True}
        if check_only:
            return {"verified": False}
        if operation == "label":
            targets = []
            for name in names:
                target = next((x['id'] for x in labels if x['name'] == name and x.get('type') == 'user'), None)
                if target is None:
                    target = self.api.users().labels().create(userId='me', body={
                        'name': name, 'labelListVisibility': 'labelShow', 'messageListVisibility': 'show'}).execute()['id']
                targets.append(target)
            body = {'addLabelIds': targets}
        elif operation == "archive":
            body = {"removeLabelIds": ["INBOX"]}
        else:
            body = {"addLabelIds": ["INBOX"]}
        # Never blindly retry an external mutation. Read back even after success.
        self.api.users().messages().modify(userId="me", id=binding["message_id"], body=body).execute()
        labels, ids = self.inspect(binding)
        return {"verified": self.desired(operation, label, labels, ids), "already_present": False}


def recover(db_path):
    """After a stopped process, inspect uncertain work manually; never replay it."""
    agent = Agent(db_path, None)
    try:
        with agent.db:
            rows = list(agent.db.execute("SELECT * FROM gmail_operations WHERE status='processing'"))
            for row in rows:
                agent.db.execute("UPDATE gmail_operations SET status='unknown',error=? WHERE id=?",
                                 ("Server stopped during Gmail operation. Check Gmail status.", row["id"]))
                agent.db.execute("UPDATE actions SET status='unknown' WHERE id=?", (row["action_id"],))
                agent.log(row["action_id"], "gmail_unknown", {"operation": row["operation"], "reason": "Interrupted operation"})
    finally:
        agent.close()


def run_one(db_path, executor, operation_id=None, check_only=False):
    """Claim durably, validate policy, perform network I/O, then commit observed result."""
    agent = Agent(db_path, None)
    row = None
    eligible = False
    try:
        candidate = (agent.db.execute("SELECT * FROM gmail_operations WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
                     if operation_id is None else agent.db.execute("SELECT * FROM gmail_operations WHERE id=?", (operation_id,)).fetchone())
        if candidate is not None and candidate["operation"].startswith("label-review:"):
            from .label_preferences import run_review
            return run_review(agent, executor, candidate, check_only)
        if candidate is not None and candidate['operation'].startswith('labels-set:'):
            from .multi_labels import run_resolution
            return run_resolution(agent, executor, candidate, check_only)
        if candidate is not None and candidate["operation"].split(":")[0] in {"draft", "send"}:
            from .gmail_replies import run_operation
            return run_operation(agent, executor, candidate, check_only)
        with agent.db:
            agent.db.execute("BEGIN IMMEDIATE")
            if operation_id is None:
                row = agent.db.execute("SELECT * FROM gmail_operations WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            else:
                row = agent.db.execute("SELECT * FROM gmail_operations WHERE id=?", (operation_id,)).fetchone()
            if row is None:
                return False
            row = dict(row)
            allowed = {"unknown", "error"} if check_only else {"queued"}
            if row["status"] not in allowed:
                raise ValueError("Gmail operation is not available for this request")
            eligible = True
            action = agent.get(row["action_id"])
            p = Proposal(**action["proposal"])
            validate(p)
            binding = agent.db.execute("SELECT * FROM gmail_bindings WHERE email_id=?", (action["email_id"],)).fetchone()
            if binding is None or action["transport"] != "gmail" or action["revision"] != row["revision"]:
                raise ScopeError("Gmail operation binding is invalid")
            if row["operation"] == "restore":
                if p.action != "archive":
                    raise ScopeError("Only archived messages can be restored")
            else:
                if p.action not in {"archive", "label"} or row["operation"] != p.action:
                    raise ScopeError("Unsupported Gmail action")
                d = decide(p)
                if d.status not in {"ready", "pending"}:
                    raise ScopeError("Policy blocks Gmail execution")
                if p.action == "archive" and not check_only:
                    from .attention import matches
                    if not row["approved"] and matches(agent,agent.email_for(action['id']),p):
                        raise ScopeError("Your attention preference requires review before archiving")
                    pref = agent.preference(p, agent.email_for(action["id"]))
                    if pref["mode"] == "keep" or (not row["approved"] and pref["mode"] != "notify"):
                        raise ScopeError("Current archive preference does not permit execution")
                if p.action == "label" and not check_only:
                    from .label_preferences import current_rule_valid
                    if not current_rule_valid(agent, action["id"]):
                        raise ScopeError("The label preference changed before execution")
            agent.db.execute("UPDATE gmail_operations SET status='processing',error='' WHERE id=?", (row["id"],))
            agent.log(action["id"], "gmail_started", {"operation": row["operation"], "check_only": check_only})
        # Read-only status checks deliberately do not require old learning permission.
        from .multi_labels import targets, conflict
        wanted = targets(agent, action['id'], p.label) if p.action == 'label' else [p.label]
        result = executor.apply(dict(binding), row["operation"], wanted if len(wanted) > 1 else wanted[0], check_only=check_only)
        with agent.db:
            agent.db.execute("BEGIN IMMEDIATE")
            if result.get('conflict'):
                conflict(agent, action['id'], wanted, result['existing'])
                agent.db.execute("UPDATE gmail_operations SET status='error',error='Choose two additional labels' WHERE id=?", (row['id'],))
                agent.db.execute("UPDATE actions SET status='error' WHERE id=?", (action['id'],))
                return True
            if not result["verified"]:
                status = "error" if check_only else "unknown"
                agent.db.execute("UPDATE gmail_operations SET status=?,error=? WHERE id=?",
                                 (status, "Gmail does not confirm the requested state. Review in Gmail.", row["id"]))
                agent.db.execute("UPDATE actions SET status=? WHERE id=?", (status, action["id"]))
                agent.log(action["id"], "gmail_unverified", {"operation": row["operation"], "check_only": check_only})
                return True
            operation = row["operation"]
            if operation in {"archive", "restore"}:
                agent.db.execute("UPDATE emails SET archived=? WHERE id=?", (int(operation == "archive"), action["email_id"]))
            else:
                agent.db.executemany("INSERT OR IGNORE INTO labels VALUES(?,?)", [(action['email_id'], x) for x in wanted])
            status = "corrected" if operation == "restore" else "executed"
            agent.db.execute("UPDATE actions SET status=? WHERE id=?", (status, action["id"]))
            agent.db.execute("UPDATE gmail_operations SET status='done',error='' WHERE id=?", (row["id"],))
            if operation == "archive" and row["approved"]:
                agent.record_feedback(action["id"], True, row["feedback_scope"])
            agent.log(action["id"], "archive_corrected" if operation == "restore" else "executed",
                      {"action": operation, "transport": "gmail", **result, "check_only": check_only})
            if action["autonomy"] == "notify" or operation == "restore":
                agent.log(action["id"], "notification", {"action": operation, "transport": "gmail"})
        return True
    except Exception as exc:
        if row is None or not eligible:
            raise
        # Roll back failed preflight work. No arbitrary Google exception bodies in UI/logs.
        agent.db.rollback()
        status = "error" if isinstance(exc, ScopeError) else "unknown"
        reason = str(exc) if isinstance(exc, ScopeError) else "Gmail result is uncertain. Check Gmail status before any further action."
        with agent.db:
            agent.db.execute("UPDATE gmail_operations SET status=?,error=? WHERE id=?", (status, reason, row["id"]))
            agent.db.execute("UPDATE actions SET status=? WHERE id=?", (status, row["action_id"]))
            agent.log(row["action_id"], "gmail_" + status, {"reason": reason, "operation": row["operation"]})
        return True
    finally:
        agent.close()
