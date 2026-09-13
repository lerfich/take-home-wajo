"""Continuation mechanics use local fakes and never call Groq."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mail_agent import g1_continue
from mail_agent.core import Proposal


class FakeProvider:
    calls_made = 0

    def __init__(self, *args, **kwargs):
        self.calls = []
        self.http_attempts = []

    def propose(self, email):
        type(self).calls_made += 1
        return Proposal("none", "Local continuation fixture", label="AI: Test",
                        independent_label=True, archive_recommendation="keep",
                        archive_reason="Keep", archive_evidence=email.body)


class G1ContinuationTests(unittest.TestCase):
    def test_failed_id_gets_separate_second_attempt(self):
        cases = [
            {"id": "one", "phase": "decision", "scenario_id": "test", "order": 1,
             "email": {"sender": "one@synthetic.example", "subject": "One", "body": "First body."},
             "expected": {"level": "silent", "action": "none", "archive": "keep",
                          "label_kind": "unknown", "attention_cue": "none",
                          "event_kind": "none", "suspicious": False}},
            {"id": "two", "phase": "decision", "scenario_id": "test", "order": 2,
             "email": {"sender": "two@synthetic.example", "subject": "Two", "body": "Second body."},
             "expected": {"level": "silent", "action": "none", "archive": "keep",
                          "label_kind": "unknown", "attention_cue": "none",
                          "event_kind": "none", "suspicious": False}},
        ]
        originals = {
            "one": {"id": "one", "status": "ok", "phase": "decision"},
            "two": {"id": "two", "status": "error", "phase": "decision"},
        }
        info = {"dataset_sha256": "fixture", "files_sha256": {}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            FakeProvider.calls_made = 0
            with patch.object(g1_continue.g1, "OUT", root), \
                 patch.object(g1_continue.g1, "preflight", return_value=(cases, info, originals)), \
                 patch.object(g1_continue, "verify_frozen_files"), \
                 patch.object(g1_continue, "GroqProposer", FakeProvider), \
                 patch.object(g1_continue, "report", return_value={"unresolved_errors": 0}):
                g1_continue.run(2)
            result = json.loads((root / "attempts" / "attempt-02" / "two.json").read_text())
            self.assertEqual(FakeProvider.calls_made, 1)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["attempt_number"], 2)
            self.assertEqual(originals["two"]["status"], "error")
            self.assertFalse((root / ".continue.lock").exists())


if __name__ == "__main__":
    unittest.main()
