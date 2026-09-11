"""Versioned Gmail replies: durable drafts, exact approvals, no send retries."""
import base64
from dataclasses import replace
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid

from .core import Proposal, decide, validate


def validate_reply(recipient, subject, text):
    if (type(recipient) is not str or
            not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", recipient)):
        raise ValueError("Enter one email address without a display name, Cc or Bcc")
    if type(subject) is not str or not subject.strip() or len(subject) > 300 or any(ord(c) < 32 for c in subject):
        raise ValueError("Subject must contain 1–300 characters without control characters")
    if type(text) is not str or not text.strip() or len(text) > 20000 or "\x00" in text:
        raise ValueError("Reply body must contain 1–20000 characters")


def stage(agent, action_id, recipient=None, subject=None, text=None):
    """Called inside the core transaction. Draft approval is not send approval."""
    action = agent.get(action_id)
    p = Proposal(**action["proposal"])
    email = agent.email_for(action_id)
    previous = agent.db.execute("SELECT * FROM gmail_replies WHERE action_id=? ORDER BY revision DESC LIMIT 1",
                                (action_id,)).fetchone()
    recipient = recipient if recipient is not None else (p.recipient or email.sender)
    subject = subject if subject is not None else ("Re: " + email.subject if not email.subject.lower().startswith("re:") else email.subject)
    text = p.text if text is None else text
    validate_reply(recipient, subject, text)
    agent.db.execute("""INSERT INTO gmail_replies(action_id,revision,recipient,subject,text,message_key,draft_id)
                        VALUES(?,?,?,?,?,?,?)""",
                    (action_id, action["revision"], recipient, subject, text,
                     uuid.uuid4().hex + "@wajo.local", previous["draft_id"] if previous else ""))
    agent.queue_gmail(action_id, "draft:" + str(action["revision"]))


def approve(agent, action_id, revision):
    action = agent.get(action_id)
    if action["status"] != "pending" or action["revision"] != revision:
        raise ValueError("Approval is stale or draft is not ready")
    p = Proposal(**action["proposal"])
    validate(p)
    if p.action not in {"send", "draft"} or decide(p).status not in {"ready", "pending"}:
        raise ValueError("Policy blocks sending")
    reply = agent.db.execute("SELECT * FROM gmail_replies WHERE action_id=? AND revision=?", (action_id, revision)).fetchone()
    if reply is None or not reply["draft_id"] or not reply["raw"]:
        raise ValueError("Save and verify the Gmail draft before approving")
    validate_reply(reply["recipient"], reply["subject"], reply["text"])
    digest = hashlib.sha256(reply["raw"].encode()).hexdigest()
    agent.db.execute("UPDATE gmail_replies SET approved_hash=? WHERE action_id=? AND revision=?",
                    (digest, action_id, revision))
    agent.log(action_id, "approved", {"revision": revision, "recipient": reply["recipient"],
              "subject": reply["subject"], "text": reply["text"], "payload_sha256": digest, "transport": "gmail"})
    agent.queue_gmail(action_id, "send:" + str(revision), approved=True)


def revise(agent, action_id, text, recipient, subject=None):
    action = agent.get(action_id)
    if action["status"] != "pending" or action["proposal"]["action"] not in {"send", "draft"}:
        raise ValueError("Only a verified, unsent Gmail draft can be edited")
    previous = agent.db.execute("SELECT * FROM gmail_replies WHERE action_id=? AND revision=?",
                                (action_id, action["revision"])).fetchone()
    if previous is None:
        raise ValueError("No editable Gmail reply")
    subject = previous["subject"] if subject is None else subject
    validate_reply(recipient, subject, text)
    p = replace(Proposal(**action["proposal"]), text=text, recipient=recipient)
    validate(p)
    if decide(p).status not in {"ready", "pending"}:
        raise ValueError("Policy blocks this reply")
    from dataclasses import asdict
    agent.db.execute("UPDATE actions SET proposal=?,revision=revision+1 WHERE id=?",
                     (json.dumps(asdict(p)), action_id))
    from .draft_preferences import record_version
    record_version(agent, action_id, action["revision"] + 1, text)
    stage(agent, action_id, recipient, subject, text)
    agent.log(action_id, "revised", {"revision": action["revision"] + 1, "transport": "gmail"})


def encode_message(reply, binding, original):
    headers = {x["name"].lower(): x["value"] for x in original.get("payload", {}).get("headers", [])}
    msg = EmailMessage(policy=policy.SMTP)
    msg["From"] = binding["account"]
    msg["To"] = reply["recipient"]
    msg["Subject"] = reply["subject"]
    msg["Message-ID"] = "<" + reply["message_key"] + ">"
    msg["X-Wajo-Reply-Key"] = reply["message_key"]
    msg["Date"] = format_datetime(datetime.now(timezone.utc))
    parent = headers.get("message-id", "")
    if re.fullmatch(r"<[^<>\s]+@[^<>\s]+>", parent):
        msg["In-Reply-To"] = parent
        msg["References"] = parent
    msg.set_content(reply["text"].replace("\r\n", "\n").replace("\r", "\n"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    # Gmail requires a matching subject for explicit thread placement.
    original_subject = headers.get("subject", "")
    canonical = lambda s: re.sub(r"^(re:\s*)+", "", s, flags=re.I).strip()
    thread_id = original.get("threadId", "") if parent and canonical(original_subject) == canonical(reply["subject"]) else ""
    return raw, thread_id


def parsed(raw):
    return BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


def same_message(raw, expected, ignore_message_id=False):
    """Compare decoded recipient/content, not MIME transfer encoding or Gmail headers."""
    actual, wanted = parsed(raw), parsed(expected)
    if actual.defects or actual.is_multipart() or actual.get_content_type() != "text/plain":
        return False
    for header in ("from", "to", "subject", "message-id", "in-reply-to", "references", "x-wajo-reply-key"):
        if header == "message-id" and ignore_message_id:
            continue
        if len(actual.get_all(header, [])) != len(wanted.get_all(header, [])):
            return False
        if str(actual.get(header, "")) != str(wanted.get(header, "")):
            return False
    if any(actual.get_all(h) for h in ("cc", "bcc", "resent-to", "resent-cc", "resent-bcc")):
        return False
    norm = lambda s: s.replace("\r\n", "\n").replace("\r", "\n")
    return norm(actual.get_content()) == norm(wanted.get_content())


def find_sent(api, reply):
    if reply.get("sent_id"):
        message = api.users().messages().get(userId="me", id=reply["sent_id"], format="raw").execute()
        if "SENT" in message.get("labelIds", []) and same_message(message["raw"], reply["raw"], ignore_message_id=bool(parsed(reply["raw"]).get("X-Wajo-Reply-Key"))):
            return message["id"]
        return ""
    result = api.users().messages().list(userId="me", q="in:sent rfc822msgid:" + reply["message_key"],
                                         maxResults=10).execute()
    matches = []
    for item in result.get("messages", []):
        message = api.users().messages().get(userId="me", id=item["id"], format="raw").execute()
        if "SENT" in message.get("labelIds", []) and same_message(message["raw"], reply["raw"], ignore_message_id=bool(parsed(reply["raw"]).get("X-Wajo-Reply-Key"))):
            matches.append(message)
    if not matches and parsed(reply["raw"]).get("X-Wajo-Reply-Key"):
        # Gmail may replace Message-ID. A bounded Sent scan checks our marker AND
        # every recipient/content field; an absent match remains unknown.
        recent = api.users().messages().list(userId="me", labelIds=["SENT"], maxResults=50).execute()
        for item in recent.get("messages", []):
            message = api.users().messages().get(userId="me", id=item["id"], format="raw").execute()
            if "SENT" in message.get("labelIds", []) and same_message(message["raw"], reply["raw"], ignore_message_id=True):
                matches.append(message)
    if len(matches) == 1 and not result.get("nextPageToken"):
        return matches[0]["id"]
    if matches:
        from .gmail_executor import ScopeError
        raise ScopeError("Multiple matching sent messages require manual review")
    return ""


def find_draft(api, reply):
    if reply["draft_id"]:
        candidates = [{"id": reply["draft_id"]}]
    else:
        result = api.users().drafts().list(userId="me", maxResults=50).execute()
        if result.get("nextPageToken"):
            return ""
        candidates = result.get("drafts", [])
    matches = []
    for item in candidates:
        draft = api.users().drafts().get(userId="me", id=item["id"], format="raw").execute()
        if same_message(draft["message"]["raw"], reply["raw"], ignore_message_id=True):
            matches.append(draft["id"])
    return matches[0] if len(matches) == 1 else ""


def run_operation(agent, executor, operation, check_only):
    """Own durable claim and all reply-specific validation. Never replay an ambiguous write."""
    from .gmail_executor import ScopeError
    row = dict(operation)
    allowed = {"unknown", "error"} if check_only else {"queued"}
    claimed = False
    try:
        with agent.db:
            agent.db.execute("BEGIN IMMEDIATE")
            current = agent.db.execute("SELECT * FROM gmail_operations WHERE id=?", (row["id"],)).fetchone()
            if current["status"] not in allowed:
                raise ValueError("Reply operation is no longer available")
            action = agent.get(row["action_id"])
            if action["revision"] != row["revision"] or action["transport"] != "gmail":
                raise ValueError("Reply revision is stale")
            claimed = True
            kind = row["operation"].split(":")[0]
            reply = dict(agent.db.execute("SELECT * FROM gmail_replies WHERE action_id=? AND revision=?",
                                           (action["id"], row["revision"])).fetchone())
            binding = dict(agent.db.execute("SELECT * FROM gmail_bindings WHERE email_id=?", (action["email_id"],)).fetchone())
            p = Proposal(**action["proposal"])
            validate(p)
            if p.action not in {"send", "draft"} or decide(p).status not in {"ready", "pending"}:
                raise ScopeError("Policy blocks reply execution")
            validate_reply(reply["recipient"], reply["subject"], reply["text"])
            if kind == "send" and row.get("automatic"):
                from .superpowers import validate_automatic_send
                try:
                    validate_automatic_send(agent, action, reply, binding)
                except ValueError as exc:
                    raise ScopeError(str(exc)) from exc
            if kind == "send" and (not row["approved"] or not reply["approved_hash"] or
                    hashlib.sha256(reply["raw"].encode()).hexdigest() != reply["approved_hash"]):
                raise ScopeError("Exact reply approval is missing or invalid")
            agent.db.execute("UPDATE gmail_operations SET status='processing',error='' WHERE id=?", (row["id"],))
            agent.log(action["id"], "gmail_started", {"operation": row["operation"], "check_only": check_only})
        # Account read is also required for reconciliation; old source label removal
        # must not prevent finding a message that may already have been sent.
        api = executor.api
        if api.users().getProfile(userId="me").execute()["emailAddress"].casefold() != binding["account"]:
            raise ScopeError("Connected Gmail account differs from the imported account")
        if check_only:
            found = find_sent(api, reply) if kind == "send" else find_draft(api, reply)
            if not found:
                raise RuntimeError("Not yet reconciled")
        else:
            executor.inspect(binding)
            if kind == "draft":
                if not reply["raw"]:
                    original = api.users().messages().get(userId="me", id=binding["message_id"], format="full").execute()
                    reply["raw"], reply["thread_id"] = encode_message(reply, binding, original)
                    # Commit bytes and Message-ID BEFORE the first Gmail mutation.
                    with agent.db:
                        agent.db.execute("UPDATE gmail_replies SET raw=?,thread_id=? WHERE action_id=? AND revision=?",
                            (reply["raw"], reply["thread_id"], action["id"], row["revision"]))
                message = {"raw": reply["raw"]}
                if reply["thread_id"]:
                    message["threadId"] = reply["thread_id"]
                if reply["draft_id"]:
                    # Do not overwrite a draft the user independently changed.
                    previous = agent.db.execute("SELECT raw FROM gmail_replies WHERE action_id=? AND revision<? ORDER BY revision DESC LIMIT 1",
                                                 (action["id"], row["revision"])).fetchone()
                    current_draft = api.users().drafts().get(userId="me", id=reply["draft_id"], format="raw").execute()
                    if previous is None or not same_message(current_draft["message"]["raw"], previous["raw"], ignore_message_id=True):
                        raise ScopeError("Gmail draft changed outside Wajo. Review it in Gmail; nothing was overwritten.")
                    draft = api.users().drafts().update(userId="me", id=reply["draft_id"], body={"message": message}).execute()
                else:
                    draft = api.users().drafts().create(userId="me", body={"message": message}).execute()
                reply["draft_id"] = draft["id"]
                with agent.db:
                    agent.db.execute("UPDATE gmail_replies SET draft_id=? WHERE action_id=? AND revision=?",
                                     (reply["draft_id"], action["id"], row["revision"]))
                found = find_draft(api, reply)
                if not found:
                    raise RuntimeError("Draft readback differs")
            else:
                # Detect manual send before issuing our one send attempt.
                found = find_sent(api, reply)
                if not found:
                    if not find_draft(api, reply):
                        raise ScopeError("Gmail draft changed or disappeared. No send was attempted.")
                    message = {"raw": reply["raw"]}
                    if reply["thread_id"]:
                        message["threadId"] = reply["thread_id"]
                    sent = api.users().drafts().send(userId="me", body={"id": reply["draft_id"], "message": message}).execute()
                    # Persist returned ID before readback. An HTTP failure never leads to retry.
                    with agent.db:
                        agent.db.execute("UPDATE gmail_replies SET sent_id=? WHERE action_id=? AND revision=?",
                                         (sent["id"], action["id"], row["revision"]))
                    actual = api.users().messages().get(userId="me", id=sent["id"], format="raw").execute()
                    if "SENT" not in actual.get("labelIds", []) or not same_message(actual["raw"], reply["raw"], ignore_message_id=bool(parsed(reply["raw"]).get("X-Wajo-Reply-Key"))):
                        raise RuntimeError("Sent readback differs")
                    found = sent["id"]
        with agent.db:
            agent.db.execute("UPDATE gmail_operations SET status='done',error='' WHERE id=?", (row["id"],))
            status = "pending" if kind == "draft" else "executed"
            reason = "Gmail draft saved. Review recipient, subject and body before approving send." if kind == "draft" else "Gmail confirmed the sent message"
            agent.db.execute("UPDATE actions SET status=?,reason=?,autonomy='ask',safety='confirmation_required' WHERE id=?",
                             (status, reason, action["id"]))
            field = "draft_id" if kind == "draft" else "sent_id"
            agent.db.execute(f"UPDATE gmail_replies SET {field}=? WHERE action_id=? AND revision=?",
                             (found, action["id"], row["revision"]))
            if kind == "send":
                agent.db.execute("INSERT INTO sent VALUES(?,?,?,?,?)",
                    (action["id"], action["email_id"], reply["recipient"], reply["subject"], reply["text"]))
                from .superpowers import record_successful_send
                if row.get("automatic"):
                    record_successful_send(agent, action, reply, found, True)
                elif agent.db.execute("SELECT 1 FROM superpower_applications WHERE action_id=? AND revoked_at=''",
                                      (action["id"],)).fetchone():
                    record_successful_send(agent, action, reply, found, False)
                agent.log(action["id"], "notification", {"action": "send", "transport": "gmail",
                    "recipient": reply["recipient"], "subject": reply["subject"], "text": reply["text"], "sent_id": found})
            agent.log(action["id"], "gmail_draft_saved" if kind == "draft" else "executed",
                      {"action": kind, "revision": row["revision"], "transport": "gmail", "check_only": check_only})
            if kind == "draft" and not check_only:
                from .superpowers import maybe_queue_auto_send
                maybe_queue_auto_send(agent, action["id"])
        return True
    except Exception as exc:
        agent.db.rollback()
        if not claimed:
            raise
        status = "error" if isinstance(exc, ScopeError) else "unknown"
        reason = str(exc) if isinstance(exc, ScopeError) else "Gmail reply result is unknown. Use Check Gmail status; do not resend manually while unresolved."
        with agent.db:
            agent.db.execute("UPDATE gmail_operations SET status=?,error=? WHERE id=?", (status, reason, row["id"]))
            agent.db.execute("UPDATE actions SET status=?,reason=? WHERE id=?", (status, reason, row["action_id"]))
            agent.log(row["action_id"], "gmail_" + status, {"operation": row["operation"], "reason": reason})
            if row.get("automatic"):
                from .superpowers import record_failed_auto
                record_failed_auto(agent, row["action_id"], status)
        return True
