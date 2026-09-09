import argparse
import json
from pathlib import Path

from .core import Agent
from .demo import CASES, ScriptedProposer
from .groq_provider import GroqProposer


def main():
    parser = argparse.ArgumentParser(description="Email agent: scripted demo or real Groq model, local mailbox only")
    parser.add_argument("--db", default="data/demo.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo")
    commands.add_parser("show")
    commands.add_parser("models")
    incoming = commands.add_parser("ingest", help="Process a JSON email using Groq; no real email delivery")
    incoming.add_argument("file", type=Path)
    for command in ("approve", "reject"):
        sub = commands.add_parser(command)
        sub.add_argument("action_id", type=int)
        sub.add_argument("--revision", required=True, type=int)
    edit = commands.add_parser("edit")
    edit.add_argument("action_id", type=int)
    edit.add_argument("--text", required=True)
    edit.add_argument("--recipient", required=True)
    args = parser.parse_args()
    if args.command in {"models", "ingest"}:
        try:
            proposer = GroqProposer.from_env(Path(__file__).resolve().parents[1] / ".env")
            if args.command == "models":
                print(json.dumps(proposer.models(), indent=2))
                return
        except ValueError as exc:
            parser.exit(2, f"Error: {exc}\n")
    else:
        proposer = ScriptedProposer()
    if args.db != ":memory:":
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    agent = Agent(args.db, proposer)
    try:
        if args.command == "demo":
            result = {"mode": "SCRIPTED DEMO — no real model; all sends are local simulations",
                      "actions": [agent.ingest(email) for email, _ in CASES]}
        elif args.command == "ingest":
            from .core import Email
            raw = json.loads(args.file.read_text())
            if type(raw) is not dict or set(raw) != {"id", "sender", "subject", "body"} or not all(type(v) is str and v.strip() for v in raw.values()):
                raise ValueError("Email requires nonempty string fields: id, sender, subject, body")
            action = agent.ingest(Email(**raw))
            result = {"mode": "groq_model_local_mailbox", "action": action, "model_calls": proposer.calls}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if action["status"] == "error":
                parser.exit(1, "Model processing failed; no action executed.\n")
            return
        elif args.command == "show":
            result = agent.snapshot()
        elif args.command == "edit":
            result = agent.revise_send(args.action_id, args.text, args.recipient)
        else:
            result = getattr(agent, args.command)(args.action_id, args.revision)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    finally:
        agent.close()


if __name__ == "__main__":
    main()
