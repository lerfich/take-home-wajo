"""Loopback-only local UI. Same policy core; background local inbox processing."""
import argparse
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
    def __init__(self, db_path, demo=False, recover_jobs=True):
        self.db_path = str(db_path)
        self.demo = demo
        self.csrf = secrets.token_urlsafe(32)
        self.stop = threading.Event()
        self.wakeup = threading.Event()
        self.lock = threading.Lock()
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS incoming_jobs (
                id TEXT PRIMARY KEY, email TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, diagnostics TEXT NOT NULL DEFAULT '[]')""")
            # Only local operations: a completed core event is idempotently returned.
            if recover_jobs:
                db.execute("UPDATE incoming_jobs SET status='queued' WHERE status='processing'")
        agent = self.agent()
        agent.close()
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

    def enqueue(self, payload, event_id=None):
        if self.demo:
            raise ValueError("Произвольные письма доступны в режиме Groq; демо использует заданные сценарии")
        if type(payload) is not dict or set(payload) != {"sender", "subject", "body"}:
            raise ValueError("Нужны отправитель, тема и текст")
        if not all(type(v) is str and v.strip() for v in payload.values()):
            raise ValueError("Заполните все поля письма")
        if "@" not in payload["sender"] or any(c in payload["sender"] for c in "\r\n,;"):
            raise ValueError("Укажите один email отправителя")
        if len(json.dumps(payload)) > 24000:
            raise ValueError("Письмо слишком длинное для текущей версии")
        email = Email(event_id or uuid.uuid4().hex, **payload)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO incoming_jobs(id,email,status,created_at) VALUES(?,?,'queued',?)",
                       (email.id, json.dumps(asdict(email)), datetime.now(timezone.utc).isoformat()))
        self.wakeup.set()
        return {"id": email.id, "status": "queued"}

    def work(self):
        while not self.stop.is_set():
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
                provider = GroqProposer.from_env(Path(__file__).resolve().parents[1] / ".env")
                agent = self.agent(provider)
                try:
                    result = agent.ingest(Email(**json.loads(row["email"])))
                finally:
                    agent.close()
                status = "error" if result["status"] == "error" else "done"
                diagnostics = provider.calls
            except Exception:
                status = "error"
                diagnostics = [{"status": "error", "error": "Обработка недоступна. Проверьте локальную настройку Groq."}]
            with self.connect() as db:
                db.execute("UPDATE incoming_jobs SET status=?,diagnostics=? WHERE id=?",
                           (status, json.dumps(diagnostics, ensure_ascii=False), row["id"]))

    def state(self):
        agent = self.agent()
        try:
            state = agent.snapshot()
            email_map = {e["id"]: e for e in state["emails"]}
            for action in state["actions"]:
                p = Proposal(**json.loads(action["proposal"]))
                action["proposal"] = asdict(p)
                action["preference"] = agent.preference(p, Email(**{
                    k: email_map[action["email_id"]][k] for k in ("id", "sender", "subject", "body")}))
                action["learning_eligible"] = learnable(p, Email(**{
                    k: email_map[action["email_id"]][k] for k in ("id", "sender", "subject", "body")}))
        finally:
            agent.close()
        with self.connect() as db:
            state["jobs"] = [dict(row) for row in db.execute("SELECT * FROM incoming_jobs ORDER BY created_at")]
        state.update(mode="scripted" if self.demo else "groq", csrf=self.csrf,
                     patterns=sorted(PATTERNS))
        return state

    def mutate(self, route, data):
        if route == "/api/ingest":
            return self.enqueue(data)
        with self.lock:
            agent = self.agent()
            try:
                if route == "/api/demo" and self.demo:
                    return [agent.ingest(email) for email, _ in CASES]
                if route == "/api/rule":
                    if type(data.get("keep", True)) is not bool:
                        raise ValueError("Некорректное правило")
                    return agent.set_archive_rule(data.get("sender", "*"), data.get("pattern", "*"), data.get("keep", True))
                if route in {"/api/approve", "/api/reject", "/api/correct", "/api/edit"}:
                    if type(data.get("action_id")) is not int:
                        raise ValueError("Некорректный ID действия")
                    action_id = data["action_id"]
                    if route == "/api/correct":
                        return agent.correct_archive(action_id, data.get("scope", "general"))
                    if type(data.get("revision")) is not int:
                        raise ValueError("Нужна версия показанного действия")
                    if route == "/api/edit":
                        if agent.get(action_id)["revision"] != data["revision"]:
                            raise ValueError("Действие изменилось. Обновите письмо")
                        if not all(type(data.get(k)) is str for k in ("text", "recipient")):
                            raise ValueError("Нужны текст и получатель")
                        return agent.revise_send(action_id, data["text"], data["recipient"])
                    method = agent.approve if route == "/api/approve" else agent.reject
                    return method(action_id, data["revision"], data.get("scope", "general"))
                raise ValueError("Неизвестная операция")
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
            return self.send(403, {"error": "Обновите страницу приложения и повторите действие"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 65536 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(400, {"error": "Ожидается JSON размером до 64 КБ"})
            data = json.loads(self.rfile.read(size))
            if type(data) is not dict:
                raise ValueError("Ожидается объект JSON")
            result = self.server.app.mutate(self.path, data)
            self.send(200, result)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self.send(400, {"error": str(exc)})
        except Exception:
            self.send(500, {"error": "Не удалось выполнить операцию. Обновите страницу и проверьте состояние письма."})


def create_server(db_path, port=8765, demo=False):
    app = Application(db_path, demo)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.app = app
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/web.sqlite3"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--demo", action="store_true", help="Scripted fixtures, no model calls; use a separate database")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    server = create_server(args.db, args.port, args.demo)
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
