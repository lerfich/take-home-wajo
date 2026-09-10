"""Loopback-only local UI. Same policy core; background local inbox processing."""
import argparse
import errno
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import uuid

from .core import Agent, Email, PATTERNS, Proposal, learnable
from .demo import CASES, ScriptedProposer
from .groq_provider import GroqProposer

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
        from .gmail_connection import GmailConnection
        self.gmail_connection = GmailConnection(self, gmail_token or connection_token or Path("data/gmail-token.json"),
                                                 gmail_credentials or Path("data/gmail-credentials.json"))
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS incoming_jobs (
                id TEXT PRIMARY KEY, email TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, diagnostics TEXT NOT NULL DEFAULT '[]')""")
            if "processing_mode" not in {r["name"] for r in db.execute("PRAGMA table_info(incoming_jobs)")}:
                db.execute("ALTER TABLE incoming_jobs ADD COLUMN processing_mode TEXT NOT NULL DEFAULT 'triage'")
            # Only local operations: a completed core event is idempotently returned.
            if recover_jobs:
                db.execute("UPDATE incoming_jobs SET status='queued' WHERE status='processing'")
        agent = self.agent()
        agent.close()
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
                db.execute("INSERT INTO gmail_bindings VALUES(?,?,?,?,?,?)",
                           (email.id, gmail_binding["account"], gmail_binding["message_id"],
                            gmail_binding["label_id"], gmail_binding["label_name"], gmail_binding["initial_inbox"]))
            if cursor.rowcount:
                db.execute("UPDATE incoming_jobs SET processing_mode=? WHERE id=?", (processing_mode,email.id))
        self.wakeup.set()
        return {"id": email.id, "status": "queued"}

    def work(self):
        if not self.demo and self.gmail_connection.token_path.is_file():
            self.gmail_connection.start("check", {})
        while not self.stop.is_set():
            if self.gmail_token:
                with self.lock:
                    if self.run_gmail():
                        continue
            with self.connect() as db:
                row = db.execute("SELECT * FROM incoming_jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
                if row:
                    db.execute("UPDATE incoming_jobs SET status='processing' WHERE id=?", (row["id"],))
            if row is None:
                self.wakeup.wait(1)
                self.wakeup.clear()
                continue
            provider = None
            try:
                from .label_provider import LabelProposer
                provider_class = LabelProposer if row["processing_mode"] == "label_review" else GroqProposer
                provider = provider_class.from_env(Path(__file__).resolve().parents[1] / ".env")
                agent = self.agent(provider)
                try:
                    result = agent.ingest(Email(**json.loads(row["email"])))
                finally:
                    agent.close()
                status = "error" if result["status"] == "error" else "done"
                diagnostics = provider.calls
            except Exception:
                status = "error"
                diagnostics = [{"status": "error", "error": "Processing unavailable. Check the local Groq configuration."}]
            with self.connect() as db:
                db.execute("UPDATE incoming_jobs SET status=?,diagnostics=? WHERE id=?",
                           (status, json.dumps(diagnostics, ensure_ascii=False), row["id"]))
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
            # Keep all tables in this response on one SQLite snapshot while the
            # background worker may commit a newly processed email.
            agent.db.execute("BEGIN")
            state = agent.snapshot()
            email_map = {e["id"]: e for e in state["emails"]}
            for action in state["actions"]:
                action["reply"] = agent.get(action["id"])["reply"]
                p = Proposal(**json.loads(action["proposal"]))
                action["proposal"] = asdict(p)
                from .label_preferences import account_for
                from .attention import cue_for
                email = email_map[action["email_id"]]
                keys = {"email": "email:" + action["email_id"], "similar": "*", "sender": email["sender"].casefold()}
                attention_cue = cue_for(Email(**{k: email[k] for k in ("id", "sender", "subject", "body")}), p)
                action["attention_cue"] = attention_cue
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
                    if saved_style:
                        action["draft_style_saved"] = True
                        action["draft_style_note"] = "Draft style saved for future matching drafts."
                    else:
                        from .draft_preferences import preview as draft_style_preview
                        try:
                            action["draft_style_preview"] = draft_style_preview(agent, action["id"], action["revision"])
                        except ValueError as exc:
                            action["draft_style_note"] = str(exc)
        finally:
            agent.close()
        with self.connect() as db:
            state["jobs"] = [dict(row) for row in db.execute("SELECT * FROM incoming_jobs ORDER BY created_at")]
        state.update(mode="scripted" if self.demo else "groq", csrf=self.csrf,
                     patterns=sorted(PATTERNS), gmail_enabled=bool(self.gmail_token),
                     gmail_connection=self.gmail_connection.snapshot())
        from .label_preferences import LABEL_KINDS
        state["label_kinds"] = LABEL_KINDS
        from .attention import ATTENTION_CUES
        state["attention_cues"] = ATTENTION_CUES
        return state

    def mutate(self, route, data):
        connection_routes = {"/api/gmail/connect": "connect", "/api/gmail/status": "check",
                             "/api/gmail/sync": "sync"}
        if route in connection_routes:
            return self.gmail_connection.start(connection_routes[route], data)
        if route == "/api/ingest":
            return self.enqueue(data)
        with self.lock:
            if route == "/api/gmail-check":
                if not self.gmail_token:
                    raise ValueError("Start the server with --gmail-live to check Gmail")
                if type(data.get("operation_id")) is not int:
                    raise ValueError("Invalid Gmail operation ID")
                self.run_gmail(data["operation_id"], check_only=True)
                return {"checked": True}
            agent = self.agent()
            try:
                if route == "/api/demo" and self.demo:
                    return [agent.ingest(email) for email, _ in CASES]
                if route == "/api/label-review":
                    if type(data.get("action_id")) is not int or type(data.get("revision")) is not int:
                        raise ValueError("The displayed action and version are required")
                    row = agent.get(data["action_id"])
                    if row["transport"] == "gmail" and not self.gmail_token:
                        raise ValueError("Start the server in Gmail live mode to update Gmail labels")
                    from .label_preferences import submit
                    result = submit(agent, data["action_id"], data["revision"], data.get("label"), data.get("scope", "email"), data.get("mode", "replace"))
                    self.wakeup.set()
                    return result
                if route == "/api/attention":
                    if type(data.get('action_id')) is not int:
                        raise ValueError('Invalid email action')
                    from .attention import set_rule
                    return set_rule(agent,data['action_id'],data.get('enabled'),data.get('scope','email'))
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
                                  data.get("important"), data.get("scope", "email"))
                if route == "/api/organization-rule-pause":
                    if type(data.get("rule_id")) is not int:
                        raise ValueError("Invalid preference ID")
                    from .organization import pause
                    return pause(agent, data["rule_id"])
                if route == "/api/draft-style":
                    if type(data.get("action_id")) is not int or type(data.get("revision")) is not int:
                        raise ValueError("The displayed draft and version are required")
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


def create_server(db_path, port=8765, demo=False, gmail_token=None, connection_token=None, gmail_credentials=None):
    # Bind before touching persistent queue state: a duplicate launch must not
    # requeue a job currently being processed by the existing server.
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
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
    parser.add_argument("--demo", action="store_true", help="Scripted fixtures, no model calls; use a separate database")
    parser.add_argument("--gmail-live", action="store_true", help="Execute queued Gmail operations for explicitly live imports; sends require approval")
    parser.add_argument("--local-simulation", action="store_true", help="Explicitly disable Gmail writes")
    parser.add_argument("--gmail-token", type=Path, default=Path("data/gmail-token.json"))
    parser.add_argument("--gmail-credentials", type=Path, default=Path("data/gmail-credentials.json"),
                        help="Local Google Desktop client JSON for Connect Gmail")
    args = parser.parse_args()
    if args.demo and args.gmail_live:
        parser.error("--demo cannot be combined with --gmail-live")
    args.db.parent.mkdir(parents=True, exist_ok=True)
    try:
        server = create_server(args.db, args.port, args.demo, args.gmail_token if not (args.demo or args.local_simulation) else None,
                               args.gmail_token, args.gmail_credentials)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            parser.exit(2, f"Port {args.port} is already in use. If Wajo is running, open "
                        f"http://127.0.0.1:{args.port}/; a second launch is unnecessary. "
                        "To change the database or mode, stop the existing server with Ctrl+C first. Do not run two servers against the same database.\n")
        raise
    server.app.worker.start()
    print(f"Wajo local mailbox: http://127.0.0.1:{server.server_address[1]} ({'scripted demo' if args.demo else 'Groq'})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.app.stop.set()
        server.app.wakeup.set()
        server.server_close()


if __name__ == "__main__":
    main()
