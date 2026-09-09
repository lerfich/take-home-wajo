import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from mail_agent.core import Proposal
from mail_agent.web import create_server


class WebTests(unittest.TestCase):
    def test_duplicate_start_does_not_reset_processing_queue(self):
        self.server.app.demo = False
        self.server.app.enqueue({"sender": "test@example.test", "subject": "Test", "body": "Synthetic"})
        with self.server.app.connect() as db:
            db.execute("UPDATE incoming_jobs SET status='processing'")
        with self.assertRaises(OSError):
            create_server(self.server.app.db_path, self.server.server_address[1])
        self.assertEqual(self.server.app.state()["jobs"][0]["status"], "processing")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.server = create_server(Path(self.directory.name) / "web.sqlite3", 0, True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.app.stop.set()
        self.server.app.wakeup.set()
        if self.server.app.worker.is_alive():
            self.server.app.worker.join(3)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def get(self, path="/api/state"):
        try:
            with urlopen(self.url + path, timeout=3) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            exc.close()
            raise

    def post(self, path, data, **headers):
        request = Request(self.url + path, data=json.dumps(data).encode(), headers={
            "Content-Type": "application/json", "Origin": self.url,
            "X-CSRF-Token": self.server.app.csrf, **headers})
        try:
            with urlopen(request, timeout=3) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            exc.close()
            raise

    def test_host_origin_csrf_and_static_paths(self):
        for headers in ({"Host": "evil.test"}, {"Origin": "https://evil.test"}, {"X-CSRF-Token": "wrong"}):
            with self.assertRaises(HTTPError) as error:
                self.post("/api/demo", {}, **headers)
            self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError):
            self.get("/../.env")
        with urlopen(self.url) as response:
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
            self.assertIn(b"app.js", response.read())
        self.assertEqual(self.get()["emails"], [])

    def test_demo_approval_correction_and_count_sources(self):
        rows = self.post("/api/demo", {})
        self.post("/api/demo", {})
        self.assertEqual(len(self.get()["emails"]), 7)
        archive = next(r for r in rows if r["proposal"]["action"] == "archive")
        args = {"action_id": archive["id"], "revision": 1}
        self.post("/api/approve", args)
        self.assertEqual(sum(e["archived"] for e in self.get()["emails"]), 1)
        with self.assertRaises(HTTPError):
            self.post("/api/approve", args)
        self.post("/api/correct", args)
        self.assertEqual(sum(e["archived"] for e in self.get()["emails"]), 0)

    def test_blocked_action_not_approvable_and_edit_revision_required(self):
        rows = self.post("/api/demo", {})
        blocked = next(r for r in rows if r["status"] == "blocked")
        with self.assertRaises(HTTPError):
            self.post("/api/approve", {"action_id": blocked["id"], "revision": 1})
        send = next(r for r in rows if r["proposal"]["action"] == "send")
        args = {"action_id": send["id"], "revision": 1, "recipient": "new@example.test", "text": "Received"}
        updated = self.post("/api/edit", args)
        self.assertEqual(updated["revision"], 2)
        with self.assertRaises(HTTPError):
            self.post("/api/approve", args)
        self.assertEqual(self.get()["sent"], [])

    def test_explicit_rule_blocks_existing_pending_archive(self):
        rows = self.post("/api/demo", {})
        archive = next(r for r in rows if r["proposal"]["action"] == "archive")
        self.post("/api/rule", {"sender": "news@example.test"})
        with self.assertRaises(HTTPError):
            self.post("/api/approve", {"action_id": archive["id"], "revision": 1})
        self.assertEqual(sum(e["archived"] for e in self.get()["emails"]), 0)

    def test_arbitrary_input_cannot_use_scripted_mode(self):
        with self.assertRaises(HTTPError):
            self.post("/api/ingest", {"sender": "x@example.test", "subject": "hello", "body": "test"})

    def test_background_processing_and_diagnostics(self):
        self.server.app.demo = False
        class FakeProvider:
            calls = [{"status": "ok", "http_attempts": [{"http_status": 200}]}]
            def propose(self, email):
                return Proposal("label", "test", label="AI: Работа")
        with patch("mail_agent.web.GroqProposer.from_env", return_value=FakeProvider()):
            self.server.app.worker.start()
            self.post("/api/ingest", {"sender": "x@example.test", "subject": "<script>alert(1)</script>", "body": "<img src=x onerror=alert(1)>"})
            deadline = time.monotonic() + 3
            state = self.get()
            while not state["actions"] and time.monotonic() < deadline:
                time.sleep(.02)
                state = self.get()
            self.assertEqual(len(state["labels"]), 1)
            self.assertIn("<script>", state["emails"][0]["subject"])
            # The frontend must render this as text, never as markup.
            self.assertEqual(state["actions"][0]["status"], "executed")


if __name__ == "__main__":
    unittest.main()
