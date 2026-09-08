import argparse
import json
from pathlib import Path

from .core import Agent
from .demo import CASES, ScriptedProposer


def main():
    parser = argparse.ArgumentParser(description="Local email-agent prototype; scripted proposer, no AI or Gmail calls")
    parser.add_argument("--db", default="data/demo.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo")
    commands.add_parser("show")
    for command in ("approve", "reject"):
        sub = commands.add_parser(command)
        sub.add_argument("action_id", type=int)
        sub.add_argument("--revision", required=True, type=int)
    edit = commands.add_parser("edit")
    edit.add_argument("action_id", type=int)
    edit.add_argument("--text", required=True)
    edit.add_argument("--recipient", required=True)
    args = parser.parse_args()
    if args.db != ":memory:":
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    agent = Agent(args.db, ScriptedProposer())
    try:
        if args.command == "demo":
            result = {"mode": "SCRIPTED DEMO — no real model; all sends are local simulations",
                      "actions": [agent.ingest(email) for email, _ in CASES]}
        elif args.command == "show":
            result = agent.snapshot()
        elif args.command == "edit":
            result = agent.revise_send(args.action_id, args.text, args.recipient)
        else:
            result = getattr(agent, args.command)(args.action_id, args.revision)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ValueError as exc:
        parser.exit(2, f"Error: {exc}\n")
    finally:
        agent.close()


if __name__ == "__main__":
    main()
