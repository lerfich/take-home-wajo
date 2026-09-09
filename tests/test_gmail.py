import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from mail_agent.gmail import import_label, message_fields, private_write
from mail_agent.web import Application


def payload():
    return {"payload": {"mimeType": "multipart/mixed", "headers": [
        {"name": "From", "value": "Example <test@example.test>"},
        {"name": "Subject", "value": "Test"}], "parts": [
        {"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"Synthetic receipt").decode()}},
        {"mimeType": "text/plain", "filename": "private.txt", "body": {"data": base64.urlsafe_b64encode(b"ATTACHMENT").decode()}}]}}


class GmailTests(unittest.TestCase):
    def test_only_inline_plain_text_and_exact_address(self):
        self.assertEqual(message_fields(payload()), {"sender": "test@example.test", "subject": "Test", "body": "Synthetic receipt"})
        with self.assertRaises(ValueError):
            message_fields({"payload": {"mimeType": "text/html", "body": {"data": ""}}})

    def test_import_deduplicates_and_never_writes_gmail(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Application(Path(directory) / "state.sqlite3")
            api = MagicMock()
            users = api.users.return_value
            users.getProfile.return_value.execute.return_value = {"emailAddress": "owner@example.test"}
            users.labels.return_value.list.return_value.execute.return_value = {"labels": [{"name": "Wajo-Test", "id": "label1"}]}
            messages = users.messages.return_value
            messages.list.return_value.execute.return_value = {"messages": [{"id": "m1"}]}
            messages.get.return_value.execute.return_value = payload()
            self.assertEqual(import_label(api, app, "Wajo-Test", 10)["queued"], 1)
            self.assertEqual(import_label(api, app, "Wajo-Test", 10)["already_imported"], 1)
            self.assertEqual(messages.get.call_count, 1)
            messages.modify.assert_not_called()
            messages.send.assert_not_called()
            self.assertEqual(len(app.state()["jobs"]), 1)
            self.assertEqual(app.state()["actions"], [])

    def test_missing_label_does_not_read_any_messages(self):
        api = MagicMock()
        api.users.return_value.getProfile.return_value.execute.return_value = {"emailAddress": "owner@example.test"}
        api.users.return_value.labels.return_value.list.return_value.execute.return_value = {"labels": []}
        with self.assertRaises(ValueError):
            import_label(api, None, "Wajo-Test", 10)
        api.users.return_value.messages.assert_not_called()

    def test_credentials_written_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token.json"
            private_write(path, json.dumps({"token": "fake-token"}))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text())["token"], "fake-token")

    def test_import_application_does_not_reset_running_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            app = Application(path)
            app.enqueue({"sender": "test@example.test", "subject": "Test", "body": "Synthetic"})
            with app.connect() as db:
                db.execute("UPDATE incoming_jobs SET status='processing'")
            imported = Application(path, recover_jobs=False)
            self.assertEqual(imported.state()["jobs"][0]["status"], "processing")
