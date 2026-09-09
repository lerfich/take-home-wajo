"""Durable, bounded Gmail label operations. No delivery or deletion endpoints."""
import json

from .core import Agent, Proposal, decide, validate


class ScopeError(ValueError):
    """A trusted binding or a current mailbox condition does not permit a write."""


class GmailExecutor:
    def __init__(self, api):
        self.api = api

    def inspect(self, binding):
        if binding["label_name"] != "Wajo-Test":
            raise ScopeError("Live writes are restricted to Wajo-Test")
        account = self.api.users().getProfile(userId="me").execute()["emailAddress"].casefold()
        if account != binding["account"]:
            raise ScopeError("Connected Gmail account differs from the imported account")
        labels = self.api.users().labels().list(userId="me").execute().get("labels", [])
        if not any(x["id"] == binding["label_id"] and x["name"] == binding["label_name"] for x in labels):
            raise ScopeError("The selected test label was removed or renamed")
        message = self.api.users().messages().get(
            userId="me", id=binding["message_id"], format="minimal").execute()
        ids = set(message.get("labelIds", []))
        if binding["label_id"] not in ids or ids.intersection({"TRASH", "SPAM", "DRAFT"}):
            raise ScopeError("Message is outside the selected test label or is in Trash, Spam or Drafts")
        return labels, ids

    @staticmethod
    def desired(operation, label, labels, ids):
        if operation == "archive":
            return "INBOX" not in ids
        if operation == "restore":
            return "INBOX" in ids
        target = next((x["id"] for x in labels if x["name"] == label and x.get("type") == "user"), None)
        return target is not None and target in ids

    def apply(self, binding, operation, label="", check_only=False):
        if operation not in {"label", "archive", "restore"}:
            raise ScopeError("Only labels, archive and restore are implemented")
        if operation == "label" and (not label.startswith("AI: ") or not label[4:].strip()
                                      or len(label) > 100 or any(ord(c) < 32 for c in label)):
            raise ScopeError("Invalid AI label")
        labels, ids = self.inspect(binding)
        if self.desired(operation, label, labels, ids):
            return {"verified": True, "already_present": True}
        if check_only:
            return {"verified": False}
        if operation == "label":
            target = next((x["id"] for x in labels if x["name"] == label and x.get("type") == "user"), None)
            if target is None:
                target = self.api.users().labels().create(userId="me", body={
                    "name": label, "labelListVisibility": "labelShow",
                    "messageListVisibility": "show"}).execute()["id"]
            body = {"addLabelIds": [target]}
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
                    pref = agent.preference(p, agent.email_for(action["id"]))
                    if pref["mode"] == "keep" or (not row["approved"] and pref["mode"] != "notify"):
                        raise ScopeError("Current archive preference does not permit execution")
            agent.db.execute("UPDATE gmail_operations SET status='processing',error='' WHERE id=?", (row["id"],))
            agent.log(action["id"], "gmail_started", {"operation": row["operation"], "check_only": check_only})
        # Read-only status checks deliberately do not require old learning permission.
        result = executor.apply(dict(binding), row["operation"], p.label, check_only=check_only)
        with agent.db:
            agent.db.execute("BEGIN IMMEDIATE")
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
                agent.db.execute("INSERT OR IGNORE INTO labels VALUES(?,?)", (action["email_id"], p.label))
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
