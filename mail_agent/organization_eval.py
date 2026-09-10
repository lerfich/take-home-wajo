"""Contrast check for model kind classification and explicit organization transfer."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time

from .core import Agent, Email
from .groq_provider import GroqProposer, PROMPT_VERSION
from .organization import current


class Fixed:
    def __init__(self, proposal):
        self.proposal = proposal

    def propose(self, email):
        return self.proposal


def matches(value, case):
    return (value["topic"] == case["expected_topic"]
            and value["subtype"] == case["expected_subtype"]
            and bool(value["important"]) is case["expected_important"])


def metrics(rows):
    completed = [row for row in rows if "error" not in row]
    expected_important = [row for row in completed if row["expected"]["important"]]
    expected_not_important = [row for row in completed if not row["expected"]["important"]]
    return {
        "attempted": len(rows),
        "completed": len(completed),
        "kind_matches": sum(row["kind_matches"] for row in completed),
        "before_matches": sum(row["before_matches"] for row in completed),
        "after_matches": sum(row["after_matches"] for row in completed),
        "expected_important": len(expected_important),
        "important_transferred_before": sum(row["before"]["important"] for row in expected_important),
        "important_transferred_after": sum(row["after"]["important"] for row in expected_important),
        "false_important_before": sum(row["before"]["important"] for row in expected_not_important),
        "false_important_after": sum(row["after"]["important"] for row in expected_not_important),
        "provider_errors": sum("error" in row for row in rows),
    }


def copy_rules(source_path, target):
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    try:
        rules = list(source.execute("SELECT * FROM organization_rules WHERE active=1 AND scope='*'"))
    finally:
        source.close()
    with target.db:
        for rule in rules:
            target.db.execute("""INSERT INTO organization_rules
                (account,kind,scope,topic,subtype,important,feedback_id,active)
                VALUES('local_simulation',?,?,?,?,?,?,1)""",
                (rule["kind"], rule["scope"], rule["topic"], rule["subtype"],
                 rule["important"], rule["feedback_id"]))
    return len(rules)


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Evaluate new organization contrasts with Qwen")
    parser.add_argument("--dataset", type=Path, default=root / "evaluation" / "organization-contrasts-v1.json")
    parser.add_argument("--preferences-db", type=Path, default=root / "data" / "web-groq.sqlite3")
    parser.add_argument("--output", type=Path, default=root / "reports" / "organization-transfer-2026-09-10.json")
    parser.add_argument("--delay", type=float, default=20)
    args = parser.parse_args()
    if args.delay < 0:
        parser.error("Delay must be nonnegative")
    dataset = json.loads(args.dataset.read_text())
    provider = GroqProposer.from_env(root / ".env")
    learned = Agent(":memory:", Fixed(None))
    rule_count = copy_rules(args.preferences_db, learned)
    rows = []
    try:
        for index, case in enumerate(dataset["cases"]):
            if index:
                time.sleep(args.delay)
            email = Email(case["id"], case["sender"], case["subject"], case["body"])
            try:
                proposal = provider.propose(email)
                fresh = Agent(":memory:", Fixed(proposal))
                try:
                    before_action = fresh.ingest(email)
                    before = current(fresh, before_action["id"])
                finally:
                    fresh.close()
                learned.proposer = Fixed(proposal)
                after_action = learned.ingest(email)
                after = current(learned, after_action["id"])
                expected = {"kind": case["expected_kind"], "topic": case["expected_topic"],
                            "subtype": case["expected_subtype"], "important": case["expected_important"]}
                row = {"id": case["id"], "email": asdict(email), "expected": expected,
                       "proposal": asdict(proposal), "kind_matches": proposal.label_kind == case["expected_kind"],
                       "before": before, "after": after, "before_matches": matches(before, case),
                       "after_matches": matches(after, case), "call": provider.calls[-1]}
                rows.append(row)
                print(f"{case['id']}: kind={proposal.label_kind} expected={case['expected_kind']} "
                      f"before={row['before_matches']} after={row['after_matches']}", flush=True)
            except Exception as exc:
                rows.append({"id": case["id"], "expected": {"kind": case["expected_kind"],
                             "topic": case["expected_topic"], "subtype": case["expected_subtype"],
                             "important": case["expected_important"]},
                             "error": type(exc).__name__, "call": provider.calls[-1] if provider.calls else {}})
                print(f"{case['id']}: provider error; stopping", flush=True)
                break
        report = {"created_at": datetime.now(timezone.utc).isoformat(), "kind": "organization_contrast_development_check",
                  "dataset": dataset["version"], "model": provider.model, "prompt_version": PROMPT_VERSION,
                  "rules_copied": rule_count, "metrics": metrics(rows), "cases": rows}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report["metrics"], indent=2), flush=True)
    finally:
        learned.close()


if __name__ == "__main__":
    main()
