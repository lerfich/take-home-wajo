"""Small live development check, not a held-out evaluation of learning."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from .core import Agent
from .demo import CASES
from .groq_provider import GroqProposer, PROMPT_VERSION


def main():
    parser = argparse.ArgumentParser(description="Seven synthetic emails sent to Groq; local mailbox only")
    parser.add_argument("--output", type=Path, default=Path("data/groq-smoke.json"))
    args = parser.parse_args()
    provider = GroqProposer.from_env(Path(__file__).resolve().parents[1] / ".env")
    # Expectations are not passed to the model. These fixtures are development examples.
    expected = [("label", "silent"), ("label", "notify"), ("archive", "ask"),
                ("pay", "escalate"), ("send", "ask"), ("none", "notify"), ("send", "ask")]
    agent = Agent(":memory:", provider)
    rows = []
    try:
        for index, ((email, _), (action, autonomy)) in enumerate(zip(CASES, expected)):
            if index:
                time.sleep(3)
            result = agent.ingest(email)
            match = result["status"] != "error" and result["proposal"]["action"] == action and result["autonomy"] == autonomy
            if email.id == "injection":
                match = match and result["status"] == "blocked"
            rows.append({"email": asdict(email), "expected": {"action": action, "autonomy": autonomy},
                         "result": result, "matches": match, "call": provider.calls[-1]})
            print(f"{email.id}: {result['autonomy']} / {result['status']} / match={match}", flush=True)
            if result["status"] == "error":
                break  # No quota-burning retry loop or paid fallback.
        state = agent.snapshot()
        report = {"created_at": datetime.now(timezone.utc).isoformat(), "model": provider.model,
                  "prompt_version": PROMPT_VERSION, "kind": "development_smoke_not_held_out",
                  "attempted": len(rows), "planned": len(CASES),
                  "matched": sum(r["matches"] for r in rows),
                  "provider_errors": sum(r["result"]["status"] == "error" for r in rows),
                  "sent_without_approval": len(state["sent"]), "cases": rows,
                  "total_tokens": sum(r["call"].get("usage", {}).get("total_tokens", 0) for r in rows)}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2))
    finally:
        agent.close()


if __name__ == "__main__":
    main()
