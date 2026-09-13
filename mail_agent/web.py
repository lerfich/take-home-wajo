"""Loopback-only local UI. Same policy core; background local inbox processing."""
import argparse
import errno
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import signal
import sqlite3
import threading
import time
import uuid
from zoneinfo import ZoneInfo

from .core import Agent, Email, PATTERNS, Proposal, learnable
from .demo import CASES, ScriptedProposer
from .groq_provider import GroqProposer
from .model_settings import (BUNDLED_GROQ, USER_GROQ, USER_OPENAI,
                             CredentialStore, create_provider,
                             initialize_model_settings, load_model_settings,
                             save_model_settings, validate_model_settings,
                             validate_user_key)

STATIC = Path(__file__).parent / "static"


class Application:
    def __init__(self, db_path, demo=False, recover_jobs=True, gmail_token=None,
                 connection_token=None, gmail_credentials=None):
        self.db_path = str(db_path)
        self.demo = demo
        self.gmail_token = gmail_token
        if demo and gmail_token:
            raise ValueError("Sample mode cannot enable Gmail writes")
        self.csrf = secrets.token_urlsafe(32)
        self.stop = threading.Event()
        self.wakeup = threading.Event()
        self.lock = threading.Lock()
        self.analysis_condition = threading.Condition()
        self.analysis_inflight = 0
        self.account_switching = False
        # Keep the UI responsive without reproducing the seven-request burst
        # that exhausted the selected free Groq model's shared token budget.
        # Provider Retry-After remains authoritative for the shared minute limit.
        self.analysis_limit = 3
        self.analysis_threads = []
        self.model_validation = {}
        self.model_validation_lock = threading.Lock()
        self.credentials = CredentialStore(Path(self.db_path).parent / "model-credentials.json")
        self.label_review_lock = threading.Lock()
        from .gmail_connection import GmailConnection
        self.gmail_connection = GmailConnection(self, gmail_token or connection_token or Path("data/gmail-token.json"),
                                                 gmail_credentials or Path("data/gmail-credentials.json"))
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS incoming_jobs (
                id TEXT PRIMARY KEY, email TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, diagnostics TEXT NOT NULL DEFAULT '[]')""")
            if "processing_mode" not in {r["name"] for r in db.execute("PRAGMA table_info(incoming_jobs)")}:
                db.execute("ALTER TABLE incoming_jobs ADD COLUMN processing_mode TEXT NOT NULL DEFAULT 'triage'")
            job_columns = {r["name"] for r in db.execute("PRAGMA table_info(incoming_jobs)")}
            if "attempts" not in job_columns:
                db.execute("ALTER TABLE incoming_jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
            if "next_retry_at" not in job_columns:
                db.execute("ALTER TABLE incoming_jobs ADD COLUMN next_retry_at TEXT NOT NULL DEFAULT ''")
            if "completed_at" not in job_columns:
                db.execute("ALTER TABLE incoming_jobs ADD COLUMN completed_at TEXT NOT NULL DEFAULT ''")
            # Only local operations: a completed core event is idempotently returned.
            if recover_jobs:
                db.execute("UPDATE incoming_jobs SET status='queued' WHERE status='processing'")
            initialize_model_settings(db)
            self.analysis_limit = load_model_settings(db).concurrency
        agent = self.agent()
        agent.close()
        from .notifications import NotificationScheduler
        self.notifications = NotificationScheduler(
            self.db_path, source_verifier=self._notification_source_current,
            policy_check=self._notification_policy_allows)
        self._backfill_event_reminders()
        if recover_jobs:
            from .gmail_executor import recover
            recover(self.db_path)
        self.worker = threading.Thread(target=self.work, daemon=True)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def agent(self, proposer=None):
        return Agent(self.db_path, proposer or ScriptedProposer())

    def model_settings(self):
        with self.connect() as db:
            return load_model_settings(db)

    def model_provider(self):
        return create_provider(
            self.model_settings(), self.credentials,
            Path(__file__).resolve().parents[1] / ".env")

    @staticmethod
    def _model_label(mode):
        return {BUNDLED_GROQ: "Bundled free Groq", USER_GROQ: "Your Groq",
                USER_OPENAI: "Your OpenAI · gpt-5.6-luna"}[mode]

    def public_models(self):
        settings = self.model_settings()
        return {**settings.public_dict(), "label": self._model_label(settings.mode),
                "has_user_groq_key": self.credentials.has_key(USER_GROQ),
                "has_user_openai_key": self.credentials.has_key(USER_OPENAI)}

    def _notification_source_current(self, job):
        if job.kind == "startup_mode":
            return True
        with self.connect() as db:
            if job.source_ref.startswith("event:"):
                event_id = job.source_ref.split(":", 1)[1]
                return db.execute(
                    "SELECT 1 FROM calendar_events WHERE id=? AND status='current'", (event_id,)
                ).fetchone() is not None
            if job.source_ref.startswith("proposal:"):
                proposal_id = job.source_ref.split(":", 1)[1]
                return db.execute(
                    """SELECT 1 FROM event_proposals p JOIN emails e ON e.id=p.source_email_id
                       WHERE p.id=? AND p.kind='response_deadline'
                         AND p.confidence='clear' AND p.status IN ('awaiting_confirmation','approved')""",
                    (proposal_id,),
                ).fetchone() is not None
        return False

    @staticmethod
    def _notification_policy_allows(job):
        """Fail closed for notification kinds not explicitly supported by D."""
        return job.kind in {"startup_mode", "event_reminder", "urgent_deadline"}

    def _backfill_event_reminders(self):
        """Repair the durable event/reminder gap after an interrupted process."""
        with self.connect() as db:
            rows = db.execute("""SELECT * FROM calendar_events
                                 WHERE status='current' AND all_day=0 AND start_utc!=''""").fetchall()
        for row in rows:
            event = dict(row)
            self.notifications.schedule_event_reminder(
                str(event["id"]), title=event["title"],
                starts_at=datetime.fromisoformat(event["start_utc"]),
                source_ref=f"event:{event['id']}", source_verified=True)

    def _schedule_event_result(self, result):
        event = result.get("event") if isinstance(result, dict) else None
        if not event:
            proposal = result.get("proposal", {}) if isinstance(result, dict) else {}
            prior_id = proposal.get("supersedes_event_id") if isinstance(proposal, dict) else None
            if prior_id:
                self.notifications.cancel_event(str(prior_id))
            return
        prior_id = event.get("supersedes_event_id")
        if prior_id:
            self.notifications.cancel_event(str(prior_id))
        if event["all_day"] or not event["start_utc"]:
            return
        self.notifications.schedule_event_reminder(
            str(event["id"]), title=event["title"],
            starts_at=datetime.fromisoformat(event["start_utc"]),
            source_ref=f"event:{event['id']}", source_verified=True)

    def _schedule_analysis_notifications(self, email_id):
        from .events import proposals_for_source
        with self.connect() as db:
            proposals = proposals_for_source(db, email_id)
            event_rows = {row["proposal_id"]: dict(row) for row in db.execute(
                "SELECT * FROM calendar_events WHERE source_email_id=?", (email_id,))}
        for proposal in proposals:
            event = event_rows.get(proposal["id"])
            if event and proposal["automatic"]:
                event["all_day"] = bool(event["all_day"])
                self._schedule_event_result({"event": event, "proposal": proposal})
            if (proposal["kind"] == "response_deadline" and proposal["confidence"] == "clear"
                    and proposal["status"] in {"awaiting_confirmation", "approved"}):
                self.notifications.schedule_urgent_deadline(
                    str(proposal["id"]), title="Urgent response deadline",
                    body=proposal["title"], source_ref=f"proposal:{proposal['id']}",
                    source_verified=True)

    def enqueue(self, payload, event_id=None, gmail_binding=None, processing_mode="triage"):
        if processing_mode not in {"triage", "label_review"}:
            raise ValueError("Invalid processing mode")
        if self.demo:
            raise ValueError("Custom emails require Groq mode; sample mode uses predefined cases")
        if type(payload) is not dict or set(payload) != {"sender", "subject", "body"}:
            raise ValueError("Sender, subject and body are required")
        if not all(type(v) is str and v.strip() for v in payload.values()):
            raise ValueError("Complete all email fields")
        if "@" not in payload["sender"] or any(c in payload["sender"] for c in "\r\n,;"):
            raise ValueError("Enter one sender email address")
        if len(json.dumps(payload)) > 24000:
            raise ValueError("Email exceeds the current size limit")
        email = Email(event_id or uuid.uuid4().hex, **payload)
        with self.connect() as db:
            cursor = db.execute("INSERT OR IGNORE INTO incoming_jobs(id,email,status,created_at) VALUES(?,?,'queued',?)",
                       (email.id, json.dumps(asdict(email)), datetime.now(timezone.utc).isoformat()))
            if cursor.rowcount and gmail_binding is not None:
                db.execute("""INSERT INTO gmail_bindings(email_id,account,message_id,label_id,label_name,initial_inbox,
                           thread_id,initial_unread,source_role,has_attachments) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                           (email.id, gmail_binding["account"], gmail_binding["message_id"],
                            gmail_binding.get("label_id", ""), gmail_binding.get("label_name", ""),
                            gmail_binding["initial_inbox"], gmail_binding.get("thread_id", ""),
                            gmail_binding.get("initial_unread", 1), gmail_binding.get("source_role", "incoming"),
                            gmail_binding.get("has_attachments", 0)))
            if cursor.rowcount:
                db.execute("UPDATE incoming_jobs SET processing_mode=? WHERE id=?", (processing_mode,email.id))
        self.wakeup.set()
        return {"id": email.id, "status": "queued"}

    def work(self):
        if not self.demo and self.gmail_connection.token_path.is_file():
            self.gmail_connection.start("check", {})
        self.analysis_threads = [threading.Thread(target=self.analysis_work, args=(i,), daemon=True,
                                                  name=f"mailward-analysis-{i + 1}")
                                 for i in range(12)]
        for thread in self.analysis_threads:
            thread.start()
        while not self.stop.is_set():
            if self.gmail_token and self.gmail_connection.snapshot()["status"] != "account_choice" and not self.account_switching:
                with self.lock:
                    if self.run_gmail():
                        continue
            if not self.demo:
                self.gmail_connection.poll_if_due()
            self.wakeup.wait(1)
            self.wakeup.clear()
        for thread in self.analysis_threads:
            thread.join(3)

    def analysis_work(self, worker_index=0):
        while not self.stop.is_set():
            with self.analysis_condition:
                if (worker_index >= self.analysis_limit or self.account_switching
                        or self.gmail_connection.snapshot()["status"] == "account_choice"):
                    self.analysis_condition.wait(1)
                    continue
                self.analysis_inflight += 1
            now = datetime.now(timezone.utc)
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""UPDATE incoming_jobs SET status='queued' WHERE status='error'
                              AND next_retry_at!='' AND next_retry_at<=?""", (now.isoformat(),))
                row = db.execute("SELECT * FROM incoming_jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
                if row:
                    db.execute("UPDATE incoming_jobs SET status='processing',attempts=attempts+1 WHERE id=?", (row["id"],))
            if row is None:
                with self.analysis_condition:
                    self.analysis_inflight -= 1
                    self.analysis_condition.notify_all()
                self.wakeup.wait(1)
                self.wakeup.clear()
                continue
            provider = None
            try:
                in_scope = True
                if self.gmail_connection.token_path.is_file():
                    from .gmail_sync import refresh_before_analysis
                    in_scope = refresh_before_analysis(self.gmail_connection.token_path, self, row["id"])
                if not in_scope:
                    status = "done"
                    diagnostics = [{"status": "skipped", "reason": "Message left the incoming-mail scope."}]
                else:
                    provider = self.model_provider()
                    if row["processing_mode"] == "label_review":
                        from .label_provider import LabelReviewProposer
                        provider = LabelReviewProposer(provider)
                    guard = self.label_review_lock if row["processing_mode"] == "label_review" else nullcontext()
                    with guard:
                        agent = self.agent(provider)
                        try:
                            result = agent.ingest(Email(**json.loads(row["email"])), retry_error=True)
                        finally:
                            agent.close()
                    status = "error" if result["status"] == "error" else "done"
                    diagnostics = provider.calls
                    if status == "done":
                        self._schedule_analysis_notifications(row["id"])
            except Exception:
                status = "error"
                diagnostics = [{"status": "error", "error": "Processing unavailable. Check the active model configuration."}]
            with self.connect() as db:
                attempts = db.execute("SELECT attempts FROM incoming_jobs WHERE id=?", (row["id"],)).fetchone()[0]
                delay = min(3600, 60 * (2 ** min(attempts - 1, 6)))
                permanent_403 = any(
                    attempt.get("http_status") == 403
                    for call in diagnostics for attempt in call.get("http_attempts", []))
                retry_at = (datetime.fromtimestamp(time.time() + delay, timezone.utc).isoformat()
                            if status == "error" and not permanent_403 else "")
                completed = datetime.now(timezone.utc).isoformat() if status == "done" else ""
                db.execute("UPDATE incoming_jobs SET status=?,diagnostics=?,next_retry_at=?,completed_at=? WHERE id=?",
                           (status, json.dumps(diagnostics, ensure_ascii=False), retry_at, completed, row["id"]))
            with self.analysis_condition:
                self.analysis_inflight -= 1
                self.analysis_condition.notify_all()
            if row["processing_mode"] == "label_review":
                # Pace the explicit review batch on the free provider tier.
                self.stop.wait(20)

    def run_gmail(self, operation_id=None, check_only=False):
        from .gmail_executor import GmailExecutor, run_one
        from .gmail import service
        # Build a fresh client inside this thread; Google HTTP clients are not thread safe.
        class LazyExecutor:
            def __init__(inner):
                inner.client = None
            @property
            def api(inner):
                if inner.client is None:
                    inner.client = service(self.gmail_token)
                return inner.client
            def inspect(inner, *args, **kwargs):
                return GmailExecutor(inner.api).inspect(*args, **kwargs)
            def apply(inner, *args, **kwargs):
                return GmailExecutor(inner.api).apply(*args, **kwargs)
        return run_one(self.db_path, LazyExecutor(), operation_id, check_only)

    def state(self):
        agent = self.agent()
        try:
            from .skills import collect, listing
            collect(agent)
            # Keep all tables in this response on one SQLite snapshot while the
            # background worker may commit a newly processed email.
            agent.db.execute("BEGIN")
            state = agent.snapshot()
            state["jobs"] = [dict(row) for row in agent.db.execute(
                "SELECT * FROM incoming_jobs ORDER BY created_at")]
            # Archive Skills have their own automatic three-confirmation lifecycle.
            # Do not expose the older suggested/archive family beside them.
            state['skills'] = [skill for skill in listing(agent) if skill['family'] != 'archive']
            state['label_conflicts'] = [dict(r) for r in agent.db.execute("SELECT * FROM label_conflicts WHERE status='open'")]
            bindings = {r["email_id"]: dict(r) for r in agent.db.execute("SELECT * FROM gmail_bindings")}
            state["event_proposals"] = [dict(r) for r in agent.db.execute(
                "SELECT * FROM event_proposals ORDER BY id")]
            from .events import system_timezone
            event_zone = ZoneInfo(system_timezone())
            for proposal in state["event_proposals"]:
                proposal["all_day"] = bool(proposal["all_day"])
                proposal["automatic"] = bool(proposal["automatic"])
                proposal["local_start"] = (datetime.fromisoformat(proposal["start_utc"])
                                                   .astimezone(event_zone).isoformat()
                                                   if proposal["start_utc"] else "")
                proposal["local_end"] = (datetime.fromisoformat(proposal["end_utc"])
                                                 .astimezone(event_zone).isoformat()
                                                 if proposal["end_utc"] else "")
            pending_event_sources = {p["source_email_id"] for p in state["event_proposals"]
                                     if p["status"] in {"awaiting_confirmation", "needs_clarification"}}
            email_map = {e["id"]: e for e in state["emails"]}
            for action in state["actions"]:
                binding = bindings.get(action["email_id"])
                action["thread_id"] = binding["thread_id"] if binding and binding["thread_id"] else action["email_id"]
                action["account"] = binding["account"] if binding else "local"
                action["awaiting_event_decision"] = action["email_id"] in pending_event_sources
                action["initial_unread"] = bool(binding["initial_unread"]) if binding else True
                action["reply"] = agent.get(action["id"])["reply"]
                p = Proposal(**json.loads(action["proposal"]))
                action["proposal"] = asdict(p)
                from .archive_skills import public as public_archive_decision
                action["archive_decision"] = public_archive_decision(agent.db, action["id"])
                from .label_preferences import public_independent
                action["independent_label"] = public_independent(agent.db, action["id"])
                from .label_preferences import account_for
                from .attention import cue_for, effective_rule
                email = email_map[action["email_id"]]
                keys = {"email": "email:" + action["email_id"], "similar": "*", "sender": email["sender"].casefold()}
                attention_cue = cue_for(Email(**{k: email[k] for k in ("id", "sender", "subject", "body")}), p)
                action["attention_cue"] = attention_cue
                action["attention_effective_rule"] = effective_rule(
                    agent, Email(**{k: email[k] for k in ("id", "sender", "subject", "body")}), p)
                action["attention_scopes"] = [scope for scope, key in keys.items() if any(
                    r["enabled"] and r["account"] == account_for(agent, action["email_id"])
                    and r["scope"] == key and (scope == "email" or r["cue"] == attention_cue)
                    for r in state["attention_rules"])]
                action["preference"] = agent.preference(p, Email(**{
                    k: email_map[action["email_id"]][k] for k in ("id", "sender", "subject", "body")}))
                action["learning_eligible"] = learnable(p, Email(**{
                    k: email_map[action["email_id"]][k] for k in ("id", "sender", "subject", "body")}))
                from .organization import current as current_organization
                action["organization"] = current_organization(agent, action["id"])
                action["draft_style_preview"] = None
                action["draft_style_note"] = ""
                action["draft_style_saved"] = False
                if ((action["reply"] or action["proposal"]["action"] == "send")
                        and action["revision"] > 1 and action["status"] == "pending"):
                    saved_style = agent.db.execute(
                        "SELECT id FROM draft_style_feedback WHERE action_id=? AND revision=? LIMIT 1",
                        (action["id"], action["revision"])).fetchone()
                    saved_skill = agent.db.execute("SELECT id FROM skills WHERE family='draft' AND origin=? AND status='active'",
                        ('draft:' + str(action['id']) + ':' + str(action['revision']),)).fetchone()
                    saved_style = saved_style or saved_skill
                    if saved_style:
                        action["draft_style_saved"] = True
                        action["draft_style_note"] = "Draft style saved for future matching drafts."
                    else:
                        from .draft_preferences import preview as draft_style_preview
                        try:
                            action["draft_style_preview"] = draft_style_preview(agent, action["id"], action["revision"])
                        except ValueError as exc:
                            action["draft_style_note"] = str(exc)
            from .skills import legacy_managed
            for family, table in [('labels', 'label_rules'), ('organization', 'organization_rules'), ('draft', 'draft_style_rules')]:
                state[table] = [r for r in state[table] if not legacy_managed(agent, family, r['id'])]
            state['attention_rules'] = [r for r in state['attention_rules'] if not legacy_managed(agent, 'attention', json.dumps([r['account'], r['kind'], r['scope']]))]
            connection = self.gmail_connection.snapshot()
            from .superpowers import state as superpower_state
            verified_account = connection.get("account") if connection.get("status") == "connected" else None
            from .archive_skills import list_skills as list_archive_skills
            state["archive_skills"] = list_archive_skills(agent.db, verified_account) if verified_account else []
            state["superpowers"] = superpower_state(agent, verified_account)
            from .events import list_events
            state["events"] = list_events(agent.db, local_timezone=system_timezone())
            automatic_proposals = {p["id"] for p in state["event_proposals"] if p["automatic"]}
            for event in state["events"]:
                event["automatic"] = event["proposal_id"] in automatic_proposals
            from .event_skills import list_skills as list_event_skills
            state["event_skills"] = list_event_skills(agent.db)
        finally:
            agent.close()
        with self.connect() as db:
            state["gmail_messages"] = [dict(row) for row in db.execute("""SELECT account,message_id,thread_id,role,state,
                unread,labels,internal_date,sender,subject,body,error,attempts,retry_at,fetched_at,wajo_key
                FROM gmail_message_cache WHERE role!='excluded' ORDER BY internal_date DESC,message_id""")]
            managed_drafts = {r[0] for r in db.execute("SELECT message_key FROM gmail_replies")}
            for message in state["gmail_messages"]:
                message["managed"] = message["role"] == "draft" and message["wajo_key"] in managed_drafts
        from .gmail_sync import public as sync_public
        state["gmail_sync"] = sync_public(self, connection.get("account"))
        state["models"] = self.public_models()
        state.update(mode="scripted" if self.demo else "groq", csrf=self.csrf,
                     patterns=sorted(PATTERNS), gmail_enabled=bool(self.gmail_token),
                     gmail_connection=connection)
        from .label_preferences import LABEL_KINDS
        state["label_kinds"] = LABEL_KINDS
        from .attention import ATTENTION_CUES
        state["attention_cues"] = ATTENTION_CUES
        return state

    def mutate(self, route, data):
        # Feedback changes this email. Future behavior requires Skills review;
        # a caller cannot opt out of that boundary with a JSON flag.
        if route in {'/api/attention', '/api/organization', '/api/label-review', '/api/draft-style',
                     '/api/approve', '/api/reject', '/api/correct'}:
            data = dict(data, propose_skill=True)
        connection_routes = {"/api/gmail/connect": "connect", "/api/gmail/status": "check",
                             "/api/gmail/sync": "sync"}
        if route in connection_routes:
            return self.gmail_connection.start(connection_routes[route], data)
        if route == "/api/models/validate":
            if set(data) != {"mode", "key"} or type(data["mode"]) is not str or type(data["key"]) is not str:
                raise ValueError("Choose a user model mode and enter its API key")
            validation = validate_user_key(data["mode"], data["key"])
            if not validation.ok:
                return {"valid": False, "error": validation.error}
            token = secrets.token_urlsafe(32)
            digest = hashlib.sha256((data["mode"] + "\0" + data["key"]).encode()).hexdigest()
            with self.model_validation_lock:
                now = time.monotonic()
                self.model_validation = {
                    saved_token: proof for saved_token, proof in self.model_validation.items()
                    if proof.get("expires", 0) >= now
                }
                self.model_validation[token] = {"digest": digest, "expires": now + 300}
            return {"valid": True, "error": "", "token": token}
        if route == "/api/models/apply":
            mode, concurrency = data.get("mode"), data.get("concurrency")
            validate_model_settings(mode, concurrency)
            if mode == BUNDLED_GROQ:
                if set(data) != {"mode", "concurrency"}:
                    raise ValueError("Bundled Groq does not accept a user key")
            else:
                if set(data) != {"mode", "concurrency", "key", "validation_token"}:
                    raise ValueError("Validate the current API key before applying it")
                key, token = data.get("key"), data.get("validation_token")
                if type(key) is not str or type(token) is not str:
                    raise ValueError("Validate the current API key before applying it")
                digest = hashlib.sha256((str(mode) + "\0" + key).encode()).hexdigest()
                with self.model_validation_lock:
                    proof = self.model_validation.get(token, {})
                    valid = (proof.get("expires", 0) >= time.monotonic()
                             and secrets.compare_digest(proof.get("digest", ""), digest))
                    if valid:
                        self.model_validation.pop(token, None)
                if not valid:
                    raise ValueError("This key validation is missing or stale. Validate the current key again")
                self.credentials.set_key(mode, key)
            with self.connect() as db:
                settings = save_model_settings(db, mode, concurrency)
            with self.analysis_condition:
                self.analysis_limit = settings.concurrency
                self.analysis_condition.notify_all()
            return {"applied": True, **settings.public_dict(), "label": self._model_label(settings.mode)}
        if route == "/api/gmail/account-choice":
            return self.gmail_connection.resolve_account_switch(data)
        if route == "/api/ingest":
            return self.enqueue(data)
        if route in {"/api/gmail/import-pause", "/api/gmail/import-resume", "/api/gmail/sync-toggle"}:
            account = data.get("account")
            if type(account) is not str or account != self.gmail_connection.snapshot().get("account"):
                raise ValueError("The displayed Gmail account changed. Refresh and try again.")
            from .gmail_sync import set_enabled, set_history_status
            if route == "/api/gmail/sync-toggle":
                return set_enabled(self, account, data.get("enabled"))
            result = set_history_status(self, account, "paused" if route.endswith("pause") else "running")
            if route.endswith("resume"):
                self.gmail_connection.resume_history(account)
            return result
        with self.lock:
            if route == "/api/retry":
                # A model failure can be repeated; an external write with an
                # uncertain result can only be reconciled, never replayed here.
                with self.connect() as db:
                    action = None
                    if "action_id" in data:
                        if type(data.get("action_id")) is not int or type(data.get("revision")) is not int:
                            raise ValueError("The displayed action and version are required")
                        action = db.execute("SELECT * FROM actions WHERE id=?", (data["action_id"],)).fetchone()
                        if not action or action["revision"] != data["revision"]:
                            raise ValueError("This email changed. Refresh before retrying.")
                        email_id = action["email_id"]
                    else:
                        email_id = data.get("email_id")
                        if type(email_id) is not str:
                            raise ValueError("Choose the email to retry")
                        action = db.execute("SELECT * FROM actions WHERE email_id=?", (email_id,)).fetchone()
                    operations = db.execute("SELECT * FROM gmail_operations WHERE action_id=? ORDER BY id DESC",
                                            (action["id"],)).fetchall() if action else []
                    operation = next((row for row in operations if row["status"] in {"error", "unknown"}), None)
                    if operations and not operation:
                        raise ValueError("This email has a Gmail operation. Refresh its current status.")
                    if not operation:
                        job = db.execute("SELECT status FROM incoming_jobs WHERE id=?", (email_id,)).fetchone()
                        if not job or job["status"] != "error":
                            raise ValueError("Only failed processing can be retried")
                        db.execute("UPDATE incoming_jobs SET status='queued',next_retry_at='' WHERE id=?", (email_id,))
                if operation:
                    if not self.gmail_token:
                        raise ValueError("Connect Gmail to verify this operation.")
                    self.run_gmail(operation["id"], check_only=True)
                    return {"checked": True, "retried": False,
                            "message": "Gmail status checked. An uncertain write is not repeated."}
                self.wakeup.set()
                return {"retried": True, "message": "Email processing queued again."}
            if route == "/api/gmail-check":
                if not self.gmail_token:
                    raise ValueError("Connect Gmail to verify this operation.")
                if type(data.get("operation_id")) is not int:
                    raise ValueError("Invalid Gmail operation ID")
                self.run_gmail(data["operation_id"], check_only=True)
                return {"checked": True}
            if route == "/api/label-status":
                if type(data.get("action_id")) is not int or type(data.get("revision")) is not int:
                    raise ValueError("The displayed label decision and version are required")
                if not self.gmail_token:
                    raise ValueError("Connect Gmail before checking label status")
                with self.connect() as db:
                    from .label_preferences import public_independent
                    decision = public_independent(db, data["action_id"])
                    operation = db.execute("""SELECT id,status FROM gmail_operations
                        WHERE action_id=? AND operation='label-independent'""", (data["action_id"],)).fetchone()
                    if (not decision or decision["revision"] != data["revision"] or not operation
                            or operation["status"] not in {"unknown", "error"}):
                        raise ValueError("No uncertain label operation is waiting for a status check")
                    operation_id = operation["id"]
                self.run_gmail(operation_id, check_only=True)
                return {"checked": True}
            agent = self.agent()
            try:
                if data.get('propose_skill') and route in {'/api/approve', '/api/reject', '/api/correct'}:
                    from .label_preferences import account_for
                    email = agent.email_for(data.get('action_id'))
                    with agent.db:
                        agent.log(None, 'skills_archive_managed', {'account': account_for(agent, email.id)})
                if route == "/api/demo" and self.demo:
                    return [agent.ingest(email) for email, _ in CASES]
                if route == "/api/label-review":
                    if type(data.get("action_id")) is not int or type(data.get("revision")) is not int:
                        raise ValueError("The displayed action and version are required")
                    row = agent.get(data["action_id"])
                    if row["transport"] == "gmail" and not self.gmail_token:
                        raise ValueError("Connect Gmail to update its labels.")
                    from .label_preferences import submit
                    result = submit(agent, data["action_id"], data["revision"], data.get("label"),
                                    'email' if data.get('propose_skill') else data.get("scope", "email"), data.get("mode", "replace"))
                    self.wakeup.set()
                    return result
                if route == "/api/label-decision":
                    if type(data.get("action_id")) is not int or type(data.get("revision")) is not int:
                        raise ValueError("The displayed label decision and version are required")
                    if not self.gmail_token:
                        raise ValueError("Connect Gmail before updating its labels")
                    from .label_preferences import decide_independent
                    with agent.db:
                        result = decide_independent(agent, data["action_id"], data["revision"],
                                                    data.get("choice"), data.get("label", ""),
                                                    data.get("second_label", ""))
                    self.wakeup.set()
                    return result
                if route == "/api/attention":
                    if type(data.get('action_id')) is not int:
                        raise ValueError('Invalid email action')
                    from .attention import set_rule
                    return set_rule(agent,data['action_id'],data.get('enabled'),
                                    'email' if data.get('propose_skill') else data.get('scope','email'))
                if route in {'/api/skills/preview', '/api/skills/save'}:
                    if 'skill_id' in data:
                        from .skills import preview, save
                        return (preview if route.endswith('/preview') else save)(agent, data)
                    raise ValueError('Review a suggested skill from saved feedback first')
                if route == '/api/skills/manage':
                    from .skills import manage
                    return manage(agent, data)
                if route == "/api/archive-decision":
                    if type(data.get("action_id")) is not int or type(data.get("revision")) is not int:
                        raise ValueError("The displayed archive decision and version are required")
                    from .archive_skills import decide as decide_archive
                    with agent.db:
                        result = decide_archive(
                            agent, data["action_id"], data["revision"], data.get("choice")
                        )
                    self.wakeup.set()
                    return result
                if route == "/api/archive-mistake":
                    if type(data.get("action_id")) is not int:
                        raise ValueError("Choose an automatically archived email")
                    from .archive_skills import correct_automatic_archive
                    with agent.db:
                        result = correct_automatic_archive(agent, data["action_id"])
                    self.wakeup.set()
                    return result
                if route == "/api/archive-skills/manage":
                    if type(data.get("skill_id")) is not int or type(data.get("revision")) is not int:
                        raise ValueError("Choose a current Archive Skill")
                    from .archive_skills import manage as manage_archive_skill
                    connection = self.gmail_connection.snapshot()
                    account = connection.get("account") if connection.get("status") == "connected" else None
                    if not account:
                        raise ValueError("Connect and verify Gmail before changing Archive Skills")
                    with agent.db:
                        return manage_archive_skill(
                            agent.db, data["skill_id"], data["revision"],
                            data.get("operation"), account,
                        )
                if route == "/api/escalation-review":
                    if (type(data.get("action_id")) is not int
                            or type(data.get("revision")) is not int
                            or data.get("choice") not in {"handled", "attention"}):
                        raise ValueError("Choose how to handle the current escalation")
                    row = agent.get(data["action_id"])
                    if row["revision"] != data["revision"] or row["status"] not in {"escalated", "blocked"}:
                        raise ValueError("This escalation changed. Refresh and review it again.")
                    with agent.db:
                        if data["choice"] == "attention":
                            from .attention import set_rule
                            set_rule(agent, row["id"], True, "email")
                        agent.db.execute(
                            "UPDATE actions SET status='reviewed',revision=revision+1 WHERE id=?",
                            (row["id"],),
                        )
                        agent.log(row["id"], "escalation_reviewed", {
                            "choice": data["choice"], "email_action_executed": False,
                        })
                    return {"reviewed": True, "choice": data["choice"], "email_action_executed": False}
                if route in {"/api/events/approve", "/api/events/reject", "/api/events/clarify",
                             "/api/events/mistake", "/api/event-skills/manage"}:
                    from . import event_skills, events
                    if route == "/api/events/mistake":
                        if type(data.get("event_id")) is not int:
                            raise ValueError("Choose an automatically added event")
                        result = event_skills.remove_mistaken_event(agent.db, data["event_id"])
                        self.notifications.cancel_event(str(data["event_id"]))
                        return result
                    if route == "/api/event-skills/manage":
                        if type(data.get("skill_id")) is not int or type(data.get("revision")) is not int:
                            raise ValueError("Choose a current Event Skill")
                        skill = event_skills.get(agent.db, data["skill_id"])
                        connection = self.gmail_connection.snapshot()
                        account = (connection.get("account") if connection.get("status") == "connected"
                                   else skill["origin_account"])
                        return event_skills.manage(
                            agent.db, data["skill_id"], data["revision"], data.get("operation"),
                            account=account, context=data.get("context"))
                    if type(data.get("proposal_id")) is not int or type(data.get("revision")) is not int:
                        raise ValueError("The displayed event proposal and revision are required")
                    if route == "/api/events/approve":
                        result = event_skills.approve_proposal(
                            agent.db, data["proposal_id"], data["revision"])
                        self._schedule_event_result(result)
                        return result
                    if route == "/api/events/reject":
                        self.notifications.cancel_source(
                            f"proposal:{data['proposal_id']}", kind="urgent_deadline")
                        return event_skills.reject_proposal(
                            agent.db, data["proposal_id"], data["revision"])
                    if type(data.get("all_day")) is not bool:
                        raise ValueError("Choose whether this is an all-day event")
                    result = events.clarify_event(
                        agent.db, data["proposal_id"], data["revision"],
                        start_at=data.get("start_at", ""), end_at=data.get("end_at", ""),
                        all_day=data["all_day"], local_date=data.get("local_date", ""),
                        local_end_date=data.get("local_end_date", ""),
                        source_timezone=data.get("timezone", ""),
                        local_timezone=events.system_timezone())
                    if (result["kind"] == "response_deadline"
                            and result["confidence"] == "clear"):
                        self.notifications.schedule_urgent_deadline(
                            str(result["id"]), title="Urgent response deadline",
                            body=result["title"], source_ref=f"proposal:{result['id']}",
                            source_verified=True)
                    return result
                if route.startswith('/api/superpowers/'):
                    connection = self.gmail_connection.snapshot()
                    account = connection.get('account') if connection.get('status') == 'connected' else None
                    if not account:
                        raise ValueError('Connect and verify Gmail before changing Superpowers')
                    from . import superpowers
                    if route == '/api/superpowers/global':
                        return superpowers.set_global(agent, data, account)
                    if route == '/api/superpowers/seen':
                        return superpowers.mark_seen(agent, data.get('id'), account)
                    if route in {'/api/superpowers/disable', '/api/superpowers/improve'}:
                        skill = superpowers.validate_skill_request(agent, data, account)
                        with agent.db:
                            superpowers.revoke_for_skill(agent, skill['id'], skill['revision'],
                                'Auto-send disabled' if route.endswith('disable') else 'Draft Skill improvement requested',
                                current_account=account)
                            if route.endswith('improve'):
                                agent.db.execute("UPDATE skills SET status='suggested',revision=revision+1 WHERE id=?",
                                                 (skill['id'],))
                                agent.log(skill['source_id'], 'skill_improvement_requested', {'skill_id': skill['id']})
                        return {'saved': True, 'skill_id': skill['id']}
                    raise ValueError('Unknown Superpowers operation')
                if route == '/api/labels/resolve':
                    row = agent.get(data.get('action_id'))
                    if row['transport'] == 'gmail' and not self.gmail_token:
                        raise ValueError('Gmail live mode is required to resolve Gmail labels')
                    from .multi_labels import resolve
                    result = resolve(agent, data)
                    self.wakeup.set()
                    return result
                if route == "/api/attention-seen":
                    if type(data.get('action_id')) is not int:
                        raise ValueError('Invalid email action')
                    with agent.db:
                        agent.db.execute('UPDATE attention_items SET seen=1 WHERE action_id=?',(data['action_id'],))
                    return {'seen':True}
                if route == "/api/organization":
                    if type(data.get("action_id")) is not int:
                        raise ValueError("Invalid email action")
                    from .organization import submit
                    return submit(agent, data["action_id"], data.get("topic"), data.get("subtype"),
                                  data.get("important"), 'email' if data.get('propose_skill') else data.get("scope", "email"))
                if route == "/api/organization-rule-pause":
                    if type(data.get("rule_id")) is not int:
                        raise ValueError("Invalid preference ID")
                    from .organization import pause
                    return pause(agent, data["rule_id"])
                if route == "/api/draft-style":
                    if type(data.get("action_id")) is not int or type(data.get("revision")) is not int:
                        raise ValueError("The displayed draft and version are required")
                    if data.get('propose_skill'):
                        from .skills import collect
                        from .draft_preferences import preview as preview_style
                        preview_style(agent, data['action_id'], data['revision'])
                        collect(agent)
                        return {'suggested': True}
                    from .draft_preferences import save
                    return save(agent, data["action_id"], data["revision"], data.get("scope"))
                if route == "/api/draft-style-rule-pause":
                    if type(data.get("rule_id")) is not int:
                        raise ValueError("Invalid preference ID")
                    from .draft_preferences import pause
                    return pause(agent, data["rule_id"])
                if route == "/api/label-rule-pause":
                    if type(data.get("rule_id")) is not int:
                        raise ValueError("Invalid preference ID")
                    with agent.db:
                        agent.db.execute("UPDATE label_rules SET active=0 WHERE id=?",(data["rule_id"],))
                        agent.log(None,"label_rule_paused",{"rule_id":data["rule_id"]})
                    return {"paused":True}
                if route == "/api/rule":
                    if type(data.get("keep", True)) is not bool:
                        raise ValueError("Invalid rule")
                    return agent.set_archive_rule(data.get("sender", "*"), data.get("pattern", "*"), data.get("keep", True))
                if route in {"/api/approve", "/api/reject", "/api/correct", "/api/edit"}:
                    if type(data.get("action_id")) is not int:
                        raise ValueError("Invalid action ID")
                    action_id = data["action_id"]
                    if route == "/api/correct":
                        return agent.correct_archive(action_id, data.get("scope", "general"))
                    if type(data.get("revision")) is not int:
                        raise ValueError("The displayed action revision is required")
                    if route == "/api/edit":
                        if agent.get(action_id)["revision"] != data["revision"]:
                            raise ValueError("The action changed. Refresh the email")
                        if not all(type(data.get(k)) is str for k in ("text", "recipient")):
                            raise ValueError("Reply text and recipient are required")
                        return agent.revise_send(action_id, data["text"], data["recipient"], data.get("subject"))
                    method = agent.approve if route == "/api/approve" else agent.reject
                    return method(action_id, data["revision"], data.get("scope", "general"))
                raise ValueError("Unknown operation")
            finally:
                agent.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Request bodies, email contents and credentials never enter access logs.

    def send(self, code, content, content_type="application/json; charset=utf-8"):
        raw = content if isinstance(content, bytes) else json.dumps(content, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(raw)

    def valid_host(self):
        port = self.server.server_address[1]
        return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def do_GET(self):
        if not self.valid_host():
            return self.send(403, {"error": "Local access only"})
        if self.path == "/api/state":
            return self.send(200, self.server.app.state())
        files = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                 "/skills.js": ("skills.js", "text/javascript; charset=utf-8"),
                 "/skills.css": ("skills.css", "text/css; charset=utf-8"),
                 "/shell-ux.js": ("shell-ux.js", "text/javascript; charset=utf-8"),
                 "/shell-ux.css": ("shell-ux.css", "text/css; charset=utf-8"),
                 "/review-ux.css": ("review-ux.css", "text/css; charset=utf-8"),
                 "/style.css": ("style.css", "text/css; charset=utf-8")}
        if self.path not in files:
            return self.send(404, {"error": "Not found"})
        name, mime = files[self.path]
        self.send(200, (STATIC / name).read_bytes(), mime)

    def do_POST(self):
        if (not self.valid_host() or self.headers.get("Origin") != "http://" + self.headers.get("Host", "")
                or not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), self.server.app.csrf)):
            return self.send(403, {"error": "Refresh the app and try again"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 65536 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(400, {"error": "Expected JSON up to 64 KB"})
            data = json.loads(self.rfile.read(size))
            if type(data) is not dict:
                raise ValueError("Expected a JSON object")
            result = self.server.app.mutate(self.path, data)
            self.send(200, result)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self.send(400, {"error": str(exc)})
        except Exception:
            self.send(500, {"error": "Operation failed. Refresh the page and check the email status."})


def create_server(db_path, port=8765, demo=False, gmail_token=None, connection_token=None,
                  gmail_credentials=None, bind="127.0.0.1"):
    # Bind before touching persistent queue state: a duplicate launch must not
    # requeue a job currently being processed by the existing server.
    server = ThreadingHTTPServer((bind, port), Handler)
    try:
        server.app = Application(db_path, demo, gmail_token=gmail_token,
                                 connection_token=connection_token, gmail_credentials=gmail_credentials)
    except Exception:
        server.server_close()
        raise
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/web.sqlite3"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bind", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1",
                        help="Container use only: bind all container interfaces; publish the port on host loopback")
    parser.add_argument("--demo", action="store_true", help="Scripted fixtures, no model calls; use a separate database")
    parser.add_argument("--gmail-live", action="store_true", help="Execute queued Gmail operations for explicitly live imports; sends require approval")
    parser.add_argument("--gmail-token", type=Path, default=Path("data/gmail-token.json"))
    parser.add_argument("--gmail-credentials", type=Path, default=Path("data/gmail-credentials.json"),
                        help="Local Google Desktop client JSON for Connect Gmail")
    args = parser.parse_args()
    if args.demo and args.gmail_live:
        parser.error("--demo cannot be combined with --gmail-live")
    args.db.parent.mkdir(parents=True, exist_ok=True)
    try:
        server = create_server(args.db, args.port, args.demo, args.gmail_token if not args.demo else None,
                               args.gmail_token, args.gmail_credentials, bind=args.bind)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            parser.exit(2, f"Port {args.port} is already in use. If Mailward is running, open "
                        f"http://127.0.0.1:{args.port}/; a second launch is unnecessary. "
                        "To change the database or mode, stop the existing server with Ctrl+C first. Do not run two servers against the same database.\n")
        raise
    server.app.worker.start()
    if not args.demo:
        server.app.notifications.schedule_startup_mode(
            server.app._model_label(server.app.model_settings().mode))
        server.app.notifications.start()
    print(f"Mailward local mailbox: http://127.0.0.1:{server.server_address[1]} "
          f"({'scripted demo' if args.demo else server.app._model_label(server.app.model_settings().mode)})", flush=True)
    def stop_on_sigterm(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_on_sigterm)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.app.stop.set()
        server.app.wakeup.set()
        server.app.notifications.close()
        server.server_close()


if __name__ == "__main__":
    main()
