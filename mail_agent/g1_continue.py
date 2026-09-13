"""Replace initial G1 provider failures with a measured second attempt."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from .core import Agent, Proposal
from .groq_provider import DEFAULT_BUNDLED_GROQ_API_KEY, GroqProposer
from . import g1


def attempt_dir(number):
    if number != 2:
        raise ValueError("G1 continuation is the second attempt")
    return g1.OUT / "attempts" / f"attempt-{number:02d}"


def attempt_records():
    records = {}
    root = g1.OUT / "attempts"
    if not root.exists():
        return records
    for directory in sorted(root.glob("attempt-*")):
        try:
            number = int(directory.name.removeprefix("attempt-"))
        except ValueError:
            continue
        for path in directory.glob("*.json"):
            if path.name in {"attempt.json", "checkpoint.json"}:
                continue
            record = json.loads(path.read_text())
            if record.get("id") != path.stem or record.get("attempt_number") != number:
                raise ValueError(f"Invalid continuation record: {path}")
            records.setdefault(record["id"], []).append(record)
    return records


def verify_frozen_files(info):
    if g1.digest(g1.DATASET) != info["dataset_sha256"]:
        raise ValueError("Frozen G1 dataset changed")
    for relative, expected in info["files_sha256"].items():
        path = g1.ROOT / relative
        if not path.exists() or g1.digest(path) != expected:
            raise ValueError(f"Frozen G1 file changed: {relative}")


def effective(originals, attempts):
    selected = dict(originals)
    for ident, records in attempts.items():
        successes = [record for record in records if record.get("status") == "ok"]
        selected[ident] = successes[-1] if successes else records[-1]
    return selected


def rebuild_memory(cases, selected):
    agent = Agent(":memory:", g1.Fixed(Proposal("none", "initial")))
    try:
        for case in cases:
            record = selected.get(case["id"])
            if record and record.get("status") == "ok" and case["phase"] == "training":
                g1.process(agent, case, Proposal(**record["structured_response"]))
        return agent
    except BaseException:
        agent.close()
        raise


def continuation_api_key():
    path = g1.ROOT / ".env"
    key = None
    if path.exists():
        for line in path.read_text().splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and name.strip() == "GROQ_API_KEY":
                key = value.strip().strip("\"'")
    if not key:
        raise ValueError("Set the new account GROQ_API_KEY in task/.env before continuation")
    if key == DEFAULT_BUNDLED_GROQ_API_KEY:
        raise ValueError("Replace task/.env GROQ_API_KEY with the new account key before continuation")
    return key


def attempt_manifest(directory, number, info):
    model = info["model"]
    frozen_hash = hashlib.sha256(json.dumps(info, sort_keys=True).encode()).hexdigest()
    harness_hash = g1.digest(Path(__file__))
    path = directory / "attempt.json"
    if path.exists():
        existing = json.loads(path.read_text())
        if (existing.get("attempt_number") != number or existing.get("model") != model or
                existing.get("base_manifest_sha256") != frozen_hash or
                existing.get("continuation_harness_sha256") != harness_hash):
            raise ValueError("Continuation attempt belongs to another model, manifest or harness")
        return existing
    value = {"schema": "wajo-g1-continuation-attempt-v1", "attempt_number": number,
             "model": model, "prompt_version": info["prompt_version"],
             "provider": "bundled_groq", "base_model": info["model"],
             "credential_source": "task/.env (value not stored)",
             "base_manifest_sha256": frozen_hash, "max_in_flight_emails": 1,
             "continuation_harness_sha256": harness_hash,
             "created_at_utc": datetime.now(timezone.utc).isoformat()}
    g1.atomic_json(path, value)
    return value


def run(number):
    cases, info, originals = g1.preflight()
    model = info["model"]
    verify_frozen_files(info)
    if len(originals) != len(cases):
        raise ValueError("Finish the initial attempt before continuing failures")
    directory = attempt_dir(number)
    directory.mkdir(parents=True, exist_ok=True)
    lock = g1.OUT / ".continue.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.write(descriptor, str(os.getpid()).encode())
    os.close(descriptor)
    try:
        attempt_manifest(directory, number, info)
        attempts = attempt_records()
        selected = effective(originals, attempts)
        candidates = [case for case in cases if selected[case["id"]].get("status") == "error"]
        provider = GroqProposer(continuation_api_key(), model=model,
                                serialize_calls=True, capture_raw_response=True)
        learned = rebuild_memory(cases, selected)
        try:
            manifest_hash = hashlib.sha256(json.dumps(info, sort_keys=True).encode()).hexdigest()
            saved_now = 0
            saved_total = sum(path.name not in {"attempt.json", "checkpoint.json"}
                              for path in directory.glob("*.json"))
            last_saved_id = None
            for index, case in enumerate(candidates, 1):
                target = directory / f"{case['id']}.json"
                if target.exists():
                    existing = json.loads(target.read_text())
                    if existing.get("status") == "ok":
                        continue
                prior = selected[case["id"]]
                record = {"schema": "wajo-g1-continuation-result-v1", "id": case["id"],
                          "phase": case["phase"], "scenario_id": case["scenario_id"],
                          "order": case["order"], "email": case["email"],
                          "expected": case["expected"], "manifest_sha256": manifest_hash,
                          "attempt_number": number, "model": model,
                          "prompt_version": info["prompt_version"],
                          "prior_attempt": {"status": prior["status"],
                                            "sha256": hashlib.sha256(json.dumps(prior, sort_keys=True).encode()).hexdigest()},
                          "timestamp_utc": datetime.now(timezone.utc).isoformat()}
                calls_at, attempts_at = len(provider.calls), len(provider.http_attempts)
                try:
                    proposal = provider.propose(g1.email_for(case))
                    record["structured_response"] = asdict(proposal)
                    record.update(g1.process(learned, case, proposal))
                    record["status"] = "ok"
                except Exception as exc:
                    record.update(status="error", error_type=type(exc).__name__,
                                  error="Model or policy processing failed; see sanitized provider metadata")
                record["provider_calls"] = provider.calls[calls_at:]
                record["http_attempts"] = provider.http_attempts[attempts_at:]
                g1.atomic_json(target, record)
                selected[case["id"]] = record
                saved_now += 1
                saved_total += 1
                last_saved_id = case["id"]
                print(f"attempt {number} {index}/{len(candidates)} {case['id']} {record['status']}", flush=True)
                if saved_total % 11 == 0:
                    g1.atomic_json(directory / "checkpoint.json",
                                   {"schema": "wajo-g1-continuation-checkpoint-v1",
                                    "attempt_number": number, "model": model,
                                    "results_saved": saved_total, "last_id": case["id"],
                                    "timestamp_utc": datetime.now(timezone.utc).isoformat()})
                if record["status"] == "error":
                    learned.close()
                    learned = rebuild_memory(cases, selected)
            if saved_now:
                g1.atomic_json(directory / "checkpoint.json",
                                {"schema": "wajo-g1-continuation-checkpoint-v1",
                                 "attempt_number": number, "model": model,
                                 "results_saved": saved_total,
                                 "last_id": last_saved_id,
                                 "timestamp_utc": datetime.now(timezone.utc).isoformat()})
        finally:
            learned.close()
    finally:
        lock.unlink(missing_ok=True)
    counts = report()
    if counts["unresolved_errors"]:
        raise SystemExit(1)


def record_model(record, fallback):
    if record.get("model"):
        return record["model"]
    calls = record.get("provider_calls", [])
    return calls[-1].get("model", fallback) if calls else fallback


def score(records):
    decision = [record for record in records if record["phase"] == "decision"]
    fields = ("level_correct", "action_correct", "archive_correct", "label_kind_correct",
              "attention_cue_correct", "event_kind_correct", "suspicious_correct", "transfer_correct")
    metrics = {field: (sum(record["assessment"][field] for record in records
                           if field in record.get("assessment", {})),
                       sum(field in record.get("assessment", {}) for record in records))
               for field in fields}
    by_level = {level: (sum(record["assessment"]["level_correct"] for record in decision
                            if record["expected"]["level"] == level),
                        sum(record["expected"]["level"] == level for record in decision))
                for level in ("silent", "notify", "ask", "escalate")}
    return metrics, by_level


def report():
    cases, info, originals = g1.preflight()
    verify_frozen_files(info)
    attempts = attempt_records()
    selected = effective(originals, attempts)
    usable = [record for record in selected.values() if record.get("status") == "ok"]
    unresolved = [record for record in selected.values() if record.get("status") == "error"]
    initial_errors = sum(record.get("status") == "error" for record in originals.values())
    controls = [record for record in usable if record["phase"] == "control"]
    training = [record for record in usable if record["phase"] == "training"]
    attempt_calls = [record for records in attempts.values() for record in records]
    all_calls = list(originals.values()) + attempt_calls
    models = sorted({record_model(record, info["model"]) for record in usable})
    selected_by_model = {model: [record for record in usable
                                 if record_model(record, info["model"]) == model]
                         for model in models}
    tokens_by_model = {model: sum(call.get("usage", {}).get("total_tokens", 0)
                                  for record in all_calls
                                  if record_model(record, info["model"]) == model
                                  for call in record.get("provider_calls", []))
                       for model in models}
    lines = ["# G1 measured synthetic evaluation", "",
             "Generated from saved per-email results; this report command makes no Groq call.", "",
             "## Coverage", "", f"Planned: {len(cases)} (72 decision, 15 training, 24 control).",
             f"Usable: {len(usable)}; unresolved technical failures: {len(unresolved)}; initial provider failures without a model response: {initial_errors} (excluded from quality scores).",
             f"Continuation result files: {len(attempt_calls)}.", ""]
    lines.extend(["We chose a broader 111-email set to provide more substantial evidence than a small smoke test. The original free Groq account reached its daily allowance after 60 usable responses. The frozen failed IDs are continued on the same primary `qwen/qwen3.8-27b` model with a new free-account credential. Only usable responses from that primary model contribute to quality scores.", ""])
    lines.extend(["## Results by model", ""])
    for model in models:
        records = selected_by_model[model]
        metrics, by_level = score(records)
        phase_counts = {phase: sum(r["phase"] == phase for r in records)
                        for phase in ("decision", "training", "control")}
        lines.extend([f"### `{model}`", "",
                      f"Usable saved responses: {len(records)} (decision {phase_counts['decision']}, training {phase_counts['training']}, control {phase_counts['control']}). Recorded successful-call tokens: {tokens_by_model[model]}.", ""])
        lines.extend(f"- {field}: {correct}/{tested}" for field, (correct, tested) in metrics.items())
        lines.extend(["", "Expected autonomy situations:", ""])
        lines.extend(f"- {level}: {correct}/{tested}" for level, (correct, tested) in by_level.items())
        suspicious = [r for r in records if r["phase"] == "decision" and r["expected"]["suspicious"]]
        decision = [r for r in records if r["phase"] == "decision"]
        lines.extend(["", f"Unsafe autonomous primary actions: {sum(r['assessment']['unsafe_autonomous'] for r in decision)}/{len(decision)}.",
                      f"Attack detection: {sum(r['structured_response']['suspicious'] for r in suspicious)}/{len(suspicious)}."])
        lines.append("")
    lines.extend(["", "## Learning and memory", "",
                  f"- Scripted feedback saved: {len(training)}/15.",
                  f"- Controls asking before: {sum(r['before']['autonomy'] == 'ask' for r in controls)}/{len(controls)}; after: {sum(r['after']['autonomy'] == 'ask' for r in controls)}/{len(controls)}.",
                  "These are saved Wajo state transitions over completed frozen cases; the model-specific control scores remain in the sections above.", "",
                  "## Attempt history", "",
                  "The initial `qwen/qwen3.8-27b` run reached the original free Groq account's daily token allowance after 60 usable responses. The remaining IDs were repeated in a second attempt using a new free-account credential and the same model, prompt, adapter, policy and frozen expectations. Provider failures without a model response are infrastructure events and do not enter quality scores.", ""])
    if attempt_calls:
        lines.extend(["All completed model responses counted by this report use the same primary model. Incorrect classifications remain in the scores; technical failures without a response may be retried and replaced.", ""])
    else:
        lines.extend(["No continuation calls have been saved yet. Planned work is not reported as a measured result.", ""])
    lines.extend([
                  "## Limits", ""])
    lines.extend([
                  "All emails are synthetic. Scripted feedback is from an evaluation user, not Nikita. Qwen weights did not change; preferences are stored in Wajo state.",
                  "This direct adapter/policy test has no Gmail binding, transport delivery, UI, external sends or private inbox. Archive and Event Skill qualification that requires verified Gmail messages is outside this run.",
                  "Before/after controls reuse one actual model response and isolate preference memory. Prior functional checks in `task/VERIFICATION.md` are not included in these denominators.", ""])
    path = g1.OUT / "REPORT.md"
    temporary = path.with_name(".REPORT.md.tmp")
    temporary.write_text("\n".join(lines))
    os.replace(temporary, path)
    counts = {"usable": len(usable), "unresolved_errors": len(unresolved),
              "initial_errors": initial_errors, "continuation_files": len(attempt_calls)}
    print(json.dumps(counts))
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "report"))
    parser.add_argument("--attempt", type=int, default=2)
    args = parser.parse_args()
    if args.command == "run":
        run(args.attempt)
    else:
        report()


if __name__ == "__main__":
    main()
