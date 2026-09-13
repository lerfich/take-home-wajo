"""Resumable Gmail history import and cursor-based synchronization of new mail."""
import json
from datetime import datetime, timedelta, timezone

from .gmail import message_fields

HISTORY_MODES = {"last30": 30, "last100": 100, "all": None, "new": 0}
PAGE_SIZE = 25
INCOMPLETE_RETRY = timedelta(hours=1)
POLL_INTERVAL = timedelta(seconds=10)
MAX_HISTORY_LABELS = 10


def now_iso(now=None):
    return (now or datetime.now(timezone.utc)).isoformat()


def initialize(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS gmail_sync_settings (
        account TEXT PRIMARY KEY, history_mode TEXT NOT NULL, history_labels TEXT NOT NULL DEFAULT '[]',
        history_status TEXT NOT NULL, page_token TEXT NOT NULL DEFAULT '', scanned INTEGER NOT NULL DEFAULT 0,
        imported INTEGER NOT NULL DEFAULT 0, incomplete INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0, total_hint INTEGER NOT NULL DEFAULT 0,
        sync_enabled INTEGER NOT NULL DEFAULT 1, history_id TEXT NOT NULL DEFAULT '',
        allow_groq INTEGER NOT NULL DEFAULT 0, last_poll_at TEXT NOT NULL DEFAULT '',
        next_poll_at TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS gmail_message_cache (
        account TEXT NOT NULL, message_id TEXT NOT NULL, thread_id TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT 'incoming', state TEXT NOT NULL, unread INTEGER NOT NULL DEFAULT 0,
        labels TEXT NOT NULL DEFAULT '[]', internal_date TEXT NOT NULL DEFAULT '',
        sender TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
        retry_at TEXT NOT NULL DEFAULT '', fetched_at TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(account,message_id));
      CREATE TABLE IF NOT EXISTS gmail_account_state (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), active_account TEXT NOT NULL,
        updated_at TEXT NOT NULL);
    """)
    columns = {r["name"] for r in db.execute("PRAGMA table_info(gmail_message_cache)")}
    if "wajo_key" not in columns:
        db.execute("ALTER TABLE gmail_message_cache ADD COLUMN wajo_key TEXT NOT NULL DEFAULT ''")
    if "history_labels" not in columns:
        db.execute("ALTER TABLE gmail_message_cache ADD COLUMN history_labels TEXT NOT NULL DEFAULT '[]'")


def public_settings(db, account=None):
    row = (db.execute("SELECT * FROM gmail_sync_settings WHERE account=?", (account,)).fetchone()
           if account else db.execute("SELECT * FROM gmail_sync_settings ORDER BY updated_at DESC LIMIT 1").fetchone())
    if not row:
        return None
    result = dict(row)
    result["history_labels"] = json.loads(result["history_labels"])
    # New mail is always synchronized for a configured connection. Historical
    # pause settings only control the optional initial-history import.
    result["sync_enabled"] = True
    result["allow_groq"] = bool(result["allow_groq"])
    result["progress"] = progress(result)
    result.pop("history_id", None)
    result.pop("page_token", None)
    return result


def active_account(db):
    row = db.execute("SELECT active_account FROM gmail_account_state WHERE singleton=1").fetchone()
    if row:
        return row["active_account"]
    legacy = db.execute("SELECT account FROM gmail_sync_settings ORDER BY updated_at DESC LIMIT 1").fetchone()
    if legacy:
        return legacy["account"]
    # Databases created before the resumable sync settings still have durable
    # Gmail bindings.  They are enough to require an explicit account choice;
    # silently adopting a newly connected account could mix two mailboxes.
    binding = db.execute("SELECT account FROM gmail_bindings ORDER BY rowid DESC LIMIT 1").fetchone()
    return binding["account"] if binding else None


def set_active_account(db, account):
    db.execute("""INSERT INTO gmail_account_state(singleton,active_account,updated_at) VALUES(1,?,?)
                  ON CONFLICT(singleton) DO UPDATE SET active_account=excluded.active_account,
                  updated_at=excluded.updated_at""", (account, now_iso()))


def local_data_counts(db):
    groups = {
        "emails": ("emails",),
        "actions": ("actions",),
        "feedback": ("preference_feedback", "label_feedback", "attention_feedback",
                     "organization_feedback", "draft_style_feedback"),
        "skills": ("skills",),
        "drafts_and_sent": ("drafts", "sent", "gmail_replies"),
        "labels_and_rules": ("labels", "archive_rules", "label_rules", "attention_rules",
                             "organization_rules", "draft_style_rules"),
        "jobs_and_operations": ("incoming_jobs", "gmail_operations", "gmail_message_cache"),
        "gmail_state": ("gmail_bindings", "gmail_sync_settings"),
        "superpowers": ("autosent_journal", "superpower_confirmations", "superpower_revocations",
                        "superpower_applications", "superpower_settings"),
        "events": ("event_skill_applications", "event_skill_feedback", "event_skill_revocations",
                   "event_skills", "calendar_events", "event_proposals", "notification_jobs"),
        "supporting_state": ("label_conflicts", "label_targets", "skill_examples",
                             "skill_legacy_links", "skill_feedback_seen", "skill_draft_seen",
                             "draft_style_applications", "draft_edit_versions", "email_organization",
                             "attention_items", "label_reviews"),
        "audit": ("audit",),
    }
    result = {name: sum(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                        for table in tables) for name, tables in groups.items()}
    result["total_records"] = sum(result.values())
    return result


def clear_local_data(db):
    """Clear Wajo's local mail-derived state. This never calls or mutates Gmail."""
    tables = (
        "notification_jobs", "event_skill_applications", "event_skill_feedback",
        "event_skill_revocations", "event_skills", "calendar_events", "event_proposals",
        "autosent_journal", "superpower_confirmations", "superpower_revocations",
        "superpower_applications", "superpower_settings",
        "gmail_operations", "gmail_replies", "label_conflicts", "label_targets",
        "skill_examples", "skill_legacy_links", "skill_feedback_seen", "skill_draft_seen",
        "skills", "draft_style_applications", "draft_edit_versions", "draft_style_rules",
        "draft_style_feedback", "email_organization", "organization_rules", "organization_feedback",
        "attention_items", "attention_rules", "attention_feedback", "label_rules", "label_feedback",
        "label_reviews", "preference_feedback", "archive_rules", "sent", "drafts", "labels",
        "audit", "gmail_bindings", "actions", "emails", "incoming_jobs",
        "gmail_message_cache", "gmail_sync_settings")
    for table in tables:
        db.execute(f"DELETE FROM {table}")


def progress(row):
    target = HISTORY_MODES[row["history_mode"]]
    if row["history_status"] == "done":
        return 100
    denominator = target or row["total_hint"]
    return min(99, round(100 * row["scanned"] / denominator)) if denominator else 0


def configure(api, app, data):
    if set(data) != {"allow_groq", "account", "live", "history_mode", "label_ids"}:
        raise ValueError("Review the Gmail account, history scope and data-sharing consent.")
    if data["allow_groq"] is not True:
        raise ValueError("Allow selected email text to be sent to the active model provider before syncing.")
    if data["history_mode"] not in HISTORY_MODES:
        raise ValueError("Choose how much existing mail to import.")
    if type(data["label_ids"]) is not list or any(type(x) is not str for x in data["label_ids"]):
        raise ValueError("Choose Gmail labels from the displayed list.")
    if len(data["label_ids"]) > MAX_HISTORY_LABELS:
        raise ValueError(f"Choose no more than {MAX_HISTORY_LABELS} Gmail labels.")
    profile = api.users().getProfile(userId="me").execute()
    account = profile["emailAddress"].casefold()
    if account != data["account"]:
        raise ValueError("The Gmail account changed. Check the connection before syncing.")
    available = {x["id"] for x in api.users().labels().list(userId="me").execute().get("labels", [])}
    selected = sorted(set(data["label_ids"]))
    if not set(selected) <= available:
        raise ValueError("A selected Gmail label changed. Refresh the connection.")
    timestamp = now_iso()
    signature = json.dumps(selected)
    with app.connect() as db:
        if active_account(db) not in {None, account}:
            raise ValueError("Choose how to handle the previous account before syncing.")
        set_active_account(db, account)
        old = db.execute("SELECT * FROM gmail_sync_settings WHERE account=?", (account,)).fetchone()
        same = old and old["history_mode"] == data["history_mode"] and old["history_labels"] == signature
        if same:
            status = "done" if old["history_status"] == "done" else "running"
            db.execute("""UPDATE gmail_sync_settings SET history_status=?,sync_enabled=1,allow_groq=1,
                          updated_at=? WHERE account=?""", (status, timestamp, account))
        else:
            status = "done" if data["history_mode"] == "new" else "running"
            db.execute("""INSERT INTO gmail_sync_settings(account,history_mode,history_labels,history_status,
                          total_hint,sync_enabled,history_id,allow_groq,created_at,updated_at)
                          VALUES(?,?,?,?,?,1,?,1,?,?) ON CONFLICT(account) DO UPDATE SET
                          history_mode=excluded.history_mode,history_labels=excluded.history_labels,
                          history_status=excluded.history_status,page_token='',scanned=0,imported=0,
                          incomplete=0,skipped=0,total_hint=excluded.total_hint,sync_enabled=1,
                          history_id=excluded.history_id,allow_groq=1,last_poll_at='',next_poll_at='',updated_at=excluded.updated_at""",
                       (account, data["history_mode"], signature, status,
                        HISTORY_MODES[data["history_mode"]] or 0, str(profile.get("historyId", "")), timestamp, timestamp))
    return public(app, account)


def public(app, account=None):
    with app.connect() as db:
        result = public_settings(db, account)
        query = """SELECT account,message_id,thread_id,state,error,attempts,retry_at
                   FROM gmail_message_cache WHERE state='incomplete'"""
        args = ()
        if account:
            query += " AND account=?"
            args = (account,)
        incomplete = [dict(r) for r in db.execute(query + " ORDER BY retry_at", args)]
        if result:
            result["current_incomplete"] = len(incomplete)
    return {"settings": result, "incomplete": incomplete}


def refresh_before_analysis(token_path, app, email_id):
    """Refresh the read signal immediately before analysis without changing Gmail."""
    with app.connect() as db:
        binding = db.execute("SELECT * FROM gmail_bindings WHERE email_id=?", (email_id,)).fetchone()
    if not binding:
        return True
    from .gmail import service
    api = service(token_path)
    account = api.users().getProfile(userId="me").execute()["emailAddress"].casefold()
    if account != binding["account"]:
        raise ValueError("Connected Gmail account changed before analysis")
    message = api.users().messages().get(userId="me", id=binding["message_id"], format="minimal").execute()
    ids = set(message.get("labelIds", []))
    if ids.intersection({"SPAM", "TRASH", "DRAFT", "SENT"}):
        with app.connect() as db:
            db.execute("UPDATE gmail_message_cache SET role='excluded' WHERE account=? AND message_id=?",
                       (binding["account"], binding["message_id"]))
        return False
    with app.connect() as db:
        # Once observed as read before Wajo's decision, never promote it back to
        # the proactive flow because a client later toggled it unread.
        if "UNREAD" not in ids:
            db.execute("UPDATE gmail_bindings SET initial_unread=0 WHERE email_id=?", (email_id,))
            db.execute("UPDATE gmail_message_cache SET unread=0 WHERE account=? AND message_id=?",
                       (binding["account"], binding["message_id"]))
    return True


def set_history_status(app, account, status):
    if status not in {"paused", "running"}:
        raise ValueError("Invalid import state")
    with app.connect() as db:
        row = db.execute("SELECT history_status FROM gmail_sync_settings WHERE account=?", (account,)).fetchone()
        if not row:
            raise ValueError("Configure Gmail sync first")
        if row["history_status"] == "done":
            return public_settings(db, account)
        db.execute("UPDATE gmail_sync_settings SET history_status=?,updated_at=? WHERE account=?",
                   (status, now_iso(), account))
    return public(app, account)["settings"]


def set_enabled(app, account, enabled):
    if type(enabled) is not bool:
        raise ValueError("Sync setting must be on or off")
    if not enabled:
        raise ValueError("New email sync is always on while Gmail is connected.")
    with app.connect() as db:
        if not db.execute("SELECT 1 FROM gmail_sync_settings WHERE account=?", (account,)).fetchone():
            raise ValueError("Configure Gmail sync first")
        db.execute("UPDATE gmail_sync_settings SET sync_enabled=?,next_poll_at='',updated_at=? WHERE account=?",
                   (int(enabled), now_iso(), account))
    return public(app, account)["settings"]


def defer_poll(app, account, now=None):
    """Back off after a read failure without advancing the Gmail history cursor."""
    timestamp = now or datetime.now(timezone.utc)
    with app.connect() as db:
        db.execute("UPDATE gmail_sync_settings SET next_poll_at=?,updated_at=? WHERE account=?",
                   ((timestamp + POLL_INTERVAL).isoformat(), timestamp.isoformat(), account))


def _cache_incomplete(app, account, item, now=None, history_labels=()):
    timestamp = now or datetime.now(timezone.utc)
    retry = (timestamp + INCOMPLETE_RETRY).isoformat()
    with app.connect() as db:
        db.execute("""INSERT INTO gmail_message_cache(account,message_id,thread_id,state,error,attempts,retry_at,history_labels)
                      VALUES(?,?,?,'incomplete','Message could not be fully loaded.',1,?,?)
                      ON CONFLICT(account,message_id) DO UPDATE SET state='incomplete',
                      error='Message could not be fully loaded.',attempts=attempts+1,retry_at=excluded.retry_at,
                      history_labels=excluded.history_labels""",
                   (account, item["id"], item.get("threadId", ""), retry, json.dumps(list(history_labels))))


def _cache_context(app, account, message, fields, role, unread):
    headers = {x.get("name", "").casefold(): x.get("value", "")
               for x in message.get("payload", {}).get("headers", [])}
    wajo_key = headers.get("x-wajo-reply-key", "")
    with app.connect() as db:
        db.execute("""INSERT INTO gmail_message_cache(account,message_id,thread_id,role,state,unread,labels,
                      internal_date,sender,subject,body,error,retry_at,fetched_at,wajo_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,'','',?,?)
                      ON CONFLICT(account,message_id) DO UPDATE SET thread_id=excluded.thread_id,role=excluded.role,
                      state=excluded.state,unread=excluded.unread,labels=excluded.labels,internal_date=excluded.internal_date,
                      sender=excluded.sender,subject=excluded.subject,body=excluded.body,error='',retry_at='',
                      fetched_at=excluded.fetched_at,wajo_key=excluded.wajo_key""",
                   (account, message["id"], message.get("threadId", ""), role, "complete", int(unread),
                    json.dumps(message.get("labelIds", [])), str(message.get("internalDate", "")),
                    fields["sender"], fields["subject"], fields["body"], now_iso(), wajo_key))


def fetch_one(api, app, account, item, selected_history_labels=(), now=None):
    with app.connect() as db:
        cached = db.execute("SELECT state FROM gmail_message_cache WHERE account=? AND message_id=?",
                            (account, item["id"])).fetchone()
        if cached and cached["state"] == "complete":
            return "existing"
    try:
        message = api.users().messages().get(userId="me", id=item["id"], format="full").execute()
        message.setdefault("id", item["id"])
        ids = set(message.get("labelIds", []))
        if selected_history_labels and not ids.intersection(selected_history_labels):
            with app.connect() as db:
                db.execute("DELETE FROM gmail_message_cache WHERE account=? AND message_id=? AND state='incomplete'",
                           (account, item["id"]))
            return "skipped"
        if ids.intersection({"SPAM", "TRASH"}):
            role = "excluded"
        elif "DRAFT" in ids:
            role = "draft"
        elif "SENT" in ids:
            role = "sent"
        else:
            role = "incoming"
        if role == "excluded":
            headers = {x.get("name", "").casefold(): x.get("value", "")
                       for x in message.get("payload", {}).get("headers", [])}
            fields = {"sender": headers.get("from", ""), "subject": headers.get("subject", ""), "body": ""}
        else:
            fields = message_fields(message)
        unread = "UNREAD" in ids
        # Persist the trusted Gmail timestamp before the analysis job becomes
        # visible to a worker. Relative calendar dates must be anchored to the
        # source message time, not to whichever thread claims the job first.
        _cache_context(app, account, message, fields, role, unread)
        if role == "incoming":
            event_id = f"gmail:{account}:{item['id']}"
            from .gmail import has_attachments
            binding = {"account": account, "message_id": item["id"], "label_id": "", "label_name": "",
                       "initial_inbox": int("INBOX" in ids), "thread_id": message.get("threadId", ""),
                       "initial_unread": int(unread), "source_role": role,
                       "has_attachments": int(has_attachments(message.get("payload", {})))}
            app.enqueue(fields, event_id=event_id, gmail_binding=binding)
        return "imported" if role == "incoming" else "context"
    except Exception as exc:
        # History may reference a draft version removed by a later update.
        # A missing message is not a transient MIME/network failure.
        if getattr(getattr(exc, 'resp', None), 'status', None) == 404:
            with app.connect() as db:
                db.execute("DELETE FROM gmail_message_cache WHERE account=? AND message_id=? AND state='incomplete'",
                           (account, item['id']))
            return 'skipped'
        _cache_incomplete(app, account, item, now, selected_history_labels)
        return "incomplete"


def history_step(api, app, account, now=None):
    with app.connect() as db:
        row = db.execute("SELECT * FROM gmail_sync_settings WHERE account=?", (account,)).fetchone()
    if not row or row["history_status"] != "running":
        return False
    target = HISTORY_MODES[row["history_mode"]]
    remaining = None if target is None else target - row["scanned"]
    if remaining is not None and remaining <= 0:
        with app.connect() as db:
            db.execute("UPDATE gmail_sync_settings SET history_status='done',page_token='',updated_at=? WHERE account=?",
                       (now_iso(now), account))
        return False
    args = {"userId": "me", "maxResults": min(PAGE_SIZE, remaining) if remaining is not None else PAGE_SIZE}
    if row["page_token"]:
        args["pageToken"] = row["page_token"]
    response = api.users().messages().list(**args).execute()
    messages = response.get("messages", [])
    counts = {"imported": 0, "incomplete": 0, "skipped": 0}
    selected = json.loads(row["history_labels"])
    scanned = 0
    for item in messages:
        outcome = fetch_one(api, app, account, item, selected, now)
        scanned += 1
        if outcome in counts:
            counts[outcome] += 1
        elif outcome == "context":
            counts["imported"] += 1
    next_token = response.get("nextPageToken", "")
    done = not next_token or (target is not None and row["scanned"] + scanned >= target)
    with app.connect() as db:
        db.execute("""UPDATE gmail_sync_settings SET history_status=?,page_token=?,scanned=scanned+?,
                      imported=imported+?,incomplete=incomplete+?,skipped=skipped+?,
                      total_hint=CASE WHEN total_hint=0 THEN ? ELSE total_hint END,updated_at=? WHERE account=?""",
                   ("done" if done else db.execute("SELECT history_status FROM gmail_sync_settings WHERE account=?", (account,)).fetchone()[0],
                    "" if done else next_token, scanned, counts["imported"], counts["incomplete"], counts["skipped"],
                    int(response.get("resultSizeEstimate", 0)), now_iso(now), account))
    return not done


def run_history(api, app, account, now=None):
    while history_step(api, app, account, now):
        pass
    return public(app, account)["settings"]


def retry_incomplete(api, app, account, now=None):
    timestamp = now or datetime.now(timezone.utc)
    with app.connect() as db:
        rows = list(db.execute("""SELECT message_id,thread_id,history_labels FROM gmail_message_cache
                    WHERE account=? AND state='incomplete' AND retry_at<=? ORDER BY retry_at""",
                               (account, timestamp.isoformat())))
    retried = 0
    for row in rows:
        labels = json.loads(row["history_labels"])
        if fetch_one(api, app, account, {"id": row["message_id"], "threadId": row["thread_id"]}, labels, timestamp) != "incomplete":
            retried += 1
    return retried


def poll_new(api, app, account, now=None):
    timestamp = now or datetime.now(timezone.utc)
    with app.connect() as db:
        row = db.execute("SELECT * FROM gmail_sync_settings WHERE account=?", (account,)).fetchone()
    if not row:
        return {"added": 0, "incomplete_recovered": 0}
    if row["next_poll_at"] and row["next_poll_at"] > timestamp.isoformat():
        return {"added": 0, "incomplete_recovered": 0}
    recovered = retry_incomplete(api, app, account, timestamp)
    start = row["history_id"]
    if not start:
        latest = api.users().getProfile(userId="me").execute().get("historyId", "")
        with app.connect() as db:
            db.execute("UPDATE gmail_sync_settings SET history_id=?,last_poll_at=?,next_poll_at=? WHERE account=?",
                       (str(latest), timestamp.isoformat(), (timestamp + POLL_INTERVAL).isoformat(), account))
        return {"added": 0, "incomplete_recovered": recovered}
    page = ""
    seen = set()
    latest = start
    while True:
        args = {"userId": "me", "startHistoryId": start, "historyTypes": ["messageAdded"]}
        if page:
            args["pageToken"] = page
        response = api.users().history().list(**args).execute()
        latest = str(response.get("historyId", latest))
        for history in response.get("history", []):
            for added in history.get("messagesAdded", []):
                message = added.get("message", {})
                if message.get("id"):
                    seen.add((message["id"], message.get("threadId", "")))
        page = response.get("nextPageToken", "")
        if not page:
            break
    added = 0
    for message_id, thread_id in seen:
        if fetch_one(api, app, account, {"id": message_id, "threadId": thread_id}, now=timestamp) in {"imported", "context"}:
            added += 1
    with app.connect() as db:
        db.execute("""UPDATE gmail_sync_settings SET history_id=?,last_poll_at=?,next_poll_at=?,updated_at=?
                      WHERE account=?""", (latest, timestamp.isoformat(), (timestamp + POLL_INTERVAL).isoformat(),
                      timestamp.isoformat(), account))
    return {"added": added, "incomplete_recovered": recovered}
