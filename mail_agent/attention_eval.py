"""Development contrast check for semantic Attention cues and escalation boundaries."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time

from .attention import LEGACY_KIND_CUES
from .core import Agent, Email, Proposal
from .groq_provider import GroqProposer, PROMPT_VERSION


class Fixed:
    def __init__(self, proposal):
        self.proposal = proposal

    def propose(self, email):
        return self.proposal


def surfaced(agent, action_id):
    row = agent.db.execute(
        "SELECT seen FROM attention_items WHERE action_id=?", (action_id,)).fetchone()
    return bool(row and not row["seen"])


def metrics(rows):
    completed = [row for row in rows if "error" not in row]
    positives = [row for row in completed if row["expected"]["attention"]]
    negatives = [row for row in completed if not row["expected"]["attention"]]
    escalations = [row for row in completed if row["expected"]["autonomy"] == "escalate"]
    non_escalations = [row for row in completed if row["expected"]["autonomy"] != "escalate"]
    return {
        "attempted": len(rows),
        "completed": len(completed),
        "cue_matches": sum(row["cue_matches"] for row in completed),
        "expected_attention": len(positives),
        "attention_before": sum(row["before"]["attention"] for row in completed),
        "attention_true_positive_after": sum(row["after"]["attention"] for row in positives),
        "attention_missed_after": sum(not row["after"]["attention"] for row in positives),
        "attention_false_positive_after": sum(row["after"]["attention"] for row in negatives),
        "attention_true_negative_after": sum(not row["after"]["attention"] for row in negatives),
        "expected_escalations": len(escalations),
        "escalation_matched": sum(row["after"]["autonomy"] == "escalate" for row in escalations),
        "escalation_missed": sum(row["after"]["autonomy"] != "escalate" for row in escalations),
        "false_escalations": sum(row["after"]["autonomy"] == "escalate" for row in non_escalations),
        "provider_errors": sum("error" in row for row in rows),
    }


def copy_rules(source_path, target):
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    try:
        columns = {row[1] for row in source.execute("PRAGMA table_info(attention_rules)")}
        rules = list(source.execute("SELECT * FROM attention_rules WHERE enabled=1"))
    finally:
        source.close()
    copied = 0
    with target.db:
        for rule in rules:
            cue = rule["cue"] if "cue" in columns and rule["cue"] else LEGACY_KIND_CUES.get(rule["kind"], "")
            if not cue or rule["scope"].startswith("email:"):
                continue
            target.db.execute("""INSERT OR REPLACE INTO attention_rules
                (account,kind,scope,enabled,cue) VALUES('local_simulation',?,?,1,?)""",
                (rule["kind"], rule["scope"], cue))
            copied += 1
    return copied


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Evaluate semantic Attention transfer and escalation")
    parser.add_argument("--dataset", type=Path, default=root / "evaluation" / "attention-contrasts-v1.json")
    parser.add_argument("--preferences-db", type=Path, default=root / "data" / "web-groq.sqlite3")
    parser.add_argument("--output", type=Path, default=root / "reports" / "attention-transfer-2026-09-10.json")
    parser.add_argument("--delay", type=float, default=20)
    parser.add_argument("--proposals-report", type=Path,
                        help="Regrade saved proposals without another model call")
    args = parser.parse_args()
    if args.delay < 0:
        parser.error("Delay must be nonnegative")
    dataset = json.loads(args.dataset.read_text())
    source_report = json.loads(args.proposals_report.read_text()) if args.proposals_report else None
    source_rows = {row["id"]: row for row in source_report["cases"]} if source_report else {}
    provider = None if source_report else GroqProposer.from_env(root / ".env")
    learned = Agent(":memory:", Fixed(None))
    rule_count = copy_rules(args.preferences_db, learned)
    rows = []
    try:
        for index, case in enumerate(dataset["cases"]):
            if index and provider:
                time.sleep(args.delay)
            email = Email(case["id"], case["sender"], case["subject"], case["body"])
            expected = {"cue": case["expected_cue"], "attention": case["expected_attention"],
                        "autonomy": case["expected_autonomy"]}
            try:
                if provider:
                    proposal = provider.propose(email)
                    call = provider.calls[-1]
                else:
                    saved = source_rows[case["id"]]
                    proposal = Proposal(**saved["proposal"])
                    call = saved["call"]
                fresh = Agent(":memory:", Fixed(proposal))
                try:
                    before_action = fresh.ingest(email)
                    before = {"attention": surfaced(fresh, before_action["id"]),
                              "autonomy": before_action["autonomy"], "status": before_action["status"]}
                finally:
                    fresh.close()
                learned.proposer = Fixed(proposal)
                after_action = learned.ingest(email)
                after = {"attention": surfaced(learned, after_action["id"]),
                         "autonomy": after_action["autonomy"], "status": after_action["status"]}
                row = {"id": case["id"], "email": asdict(email), "expected": expected,
                       "proposal": asdict(proposal), "cue_matches": proposal.attention_cue == case["expected_cue"],
                       "before": before, "after": after, "call": call}
                rows.append(row)
                print(f"{case['id']}: cue={proposal.attention_cue} expected={case['expected_cue']} "
                      f"attention={after['attention']} autonomy={after['autonomy']}", flush=True)
            except Exception as exc:
                rows.append({"id": case["id"], "expected": expected, "error": type(exc).__name__,
                             "call": provider.calls[-1] if provider and provider.calls else {}})
                print(f"{case['id']}: provider error; stopping", flush=True)
                break
        report = {"created_at": datetime.now(timezone.utc).isoformat(),
                  "kind": "attention_escalation_contrast_development_check",
                  "dataset": dataset["version"],
                  "model": provider.model if provider else source_report["model"],
                  "prompt_version": PROMPT_VERSION if provider else source_report["prompt_version"],
                  "regraded_from": str(args.proposals_report) if source_report else None,
                  "rules_copied": rule_count,
                  "metrics": metrics(rows), "cases": rows}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report["metrics"], indent=2), flush=True)
    finally:
        learned.close()


if __name__ == "__main__":
    main()
