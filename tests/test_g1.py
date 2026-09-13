"""G1 preparation checks; every provider in this module is a local fake."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evaluation.g1_dataset import build
from mail_agent import g1
from mail_agent.core import Agent, Proposal


class FakeProvider:
    attempts_total = 0
    interrupt_at = None
    error_at = None

    def __init__(self, *args, **kwargs):
        assert kwargs["serialize_calls"] is True
        self.calls = []
        self.http_attempts = []

    def propose(self, email):
        type(self).attempts_total += 1
        if type(self).interrupt_at == type(self).attempts_total:
            raise KeyboardInterrupt("simulated interruption")
        if type(self).error_at == type(self).attempts_total:
            raise ValueError("simulated provider failure")
        case = next(c for c in build()["cases"] if c["id"] == email.id)
        kind = (case["expected"]["target_kind_or_cue"] if case["phase"] == "control" and
                case["expected"]["preference_family"] == "organization" and
                case["expected"]["relation"] == "similar" else "unknown")
        if case["phase"] == "training":
            kind = case["expected"].get("label_kind", "unknown")
        cue = case["expected"].get("attention_cue", "none")
        if case["phase"] == "control" and case["expected"]["preference_family"] == "attention":
            cue = case["expected"]["target_kind_or_cue"] if case["expected"]["relation"] == "similar" else "none"
        return Proposal("none", "Synthetic local fixture", label="AI: Test",
                        label_kind=kind, pattern_evidence=email.body,
                        attention_cue=cue, attention_evidence=email.body if cue != "none" else "",
                        independent_label=True, archive_recommendation="keep",
                        archive_reason="Keep for review", archive_evidence=email.body)


class G1Tests(unittest.TestCase):
    def test_dataset_coverage_and_links(self):
        cases = build()["cases"]
        self.assertEqual(len(cases), 111)
        for level in ("silent", "notify", "ask", "escalate"):
            self.assertEqual(sum(c["phase"] == "decision" and c["expected"]["level"] == level for c in cases), 18)
        self.assertEqual(sum(c["phase"] == "training" for c in cases), 15)
        self.assertEqual(sum(c["phase"] == "control" for c in cases), 24)
        for case in cases:
            if case["phase"] == "control":
                self.assertEqual(len(case["expected"]["source_training_ids"]), 3)
                self.assertTrue(all(any(t["id"] == ident and t["order"] < case["order"] for t in cases)
                                    for ident in case["expected"]["source_training_ids"]))

    def test_training_and_same_response_before_after(self):
        from mail_agent.core import Email
        from mail_agent import organization
        learned = Agent(":memory:", g1.Fixed(Proposal("none", "fixture")))
        try:
            source = {"id": "train", "phase": "training", "email": {"sender": "a@synthetic.example",
                      "subject": "Application", "body": "Your application was received. No reply needed."},
                      "expected": {}, "scripted_feedback": {"family": "organization", "topic": "Applications",
                                            "subtype": "Receipt", "important": True, "scope": "similar"}}
            proposal = Proposal("none", "Receipt", label_kind="job_application_receipt",
                                pattern_evidence=source["email"]["body"], archive_recommendation="keep",
                                archive_reason="Review", archive_evidence=source["email"]["body"])
            trained = g1.process(learned, source, proposal)
            self.assertTrue(trained["preference_after"]["organization_rules"])
            control = {"id": "control", "phase": "control", "email": {"sender": "b@synthetic.example",
                       "subject": "Application", "body": "Your application was received. No reply needed."},
                       "expected": {"preference_family": "organization", "memory_should_apply": True,
                                    "source_training_ids": ["train"]}}
            result = g1.process(learned, control, proposal)
            self.assertEqual(result["before"]["organization"]["source"], "Agent suggestion")
            self.assertEqual(result["after"]["organization"]["source"], "Your preference")
            self.assertTrue(result["assessment"]["transfer_correct"])
            self.assertEqual(result["after"]["gmail_operations"], 0)
        finally: learned.close()

    def test_interrupted_run_resumes_without_recalling_completed_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / "dataset.json").write_bytes(g1.DATASET.read_bytes())
            FakeProvider.attempts_total = 0
            FakeProvider.interrupt_at = 13
            FakeProvider.error_at = None
            with patch.object(g1, "OUT", out), patch.object(g1, "DATASET", out / "dataset.json"), \
                 patch.object(g1, "RESULTS", out / "results"), patch.object(g1, "GroqProposer", FakeProvider), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt): g1.run()
                self.assertEqual(len(list((out / "results").glob("*.json"))), 12)
                self.assertEqual(json.loads((out / "checkpoint.json").read_text())["count"], 11)
                self.assertFalse((out / ".run.lock").exists())
                first = (out / "results" / "D-SILENT-01.json").read_bytes()
                FakeProvider.interrupt_at = None
                g1.run()
                self.assertEqual(len(list((out / "results").glob("*.json"))), 111)
                self.assertEqual((out / "results" / "D-SILENT-01.json").read_bytes(), first)
                self.assertEqual(FakeProvider.attempts_total, 112)  # one interrupted, never saved
                self.assertEqual(json.loads((out / "checkpoint.json").read_text())["count"], 111)
                self.assertIn("Saved: 111", (out / "REPORT.md").read_text())
                # Offline report rebuild cannot construct the fake provider either.
                with patch.object(g1, "GroqProposer", side_effect=AssertionError("provider called")):
                    g1.report()

    def test_error_record_is_sticky_on_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / "dataset.json").write_bytes(g1.DATASET.read_bytes())
            FakeProvider.attempts_total = 0
            FakeProvider.interrupt_at = None
            FakeProvider.error_at = 7
            with patch.object(g1, "OUT", out), patch.object(g1, "DATASET", out / "dataset.json"), \
                 patch.object(g1, "RESULTS", out / "results"), patch.object(g1, "GroqProposer", FakeProvider), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit): g1.run()
                error_path = out / "results" / "D-SILENT-07.json"
                original = error_path.read_bytes()
                self.assertEqual(json.loads(original)["status"], "error")
                FakeProvider.error_at = None
                with self.assertRaises(SystemExit): g1.run()
                self.assertEqual(error_path.read_bytes(), original)
                self.assertEqual(FakeProvider.attempts_total, 111)
                self.assertIn("errors: 1", (out / "REPORT.md").read_text())


if __name__ == "__main__": unittest.main()
