# Email agent — development prototype

Local execution and safety prototype for the Wajo take-home. **Not the finished AI agent.**

Two explicit modes are available: **scripted demo** (offline fixtures) and **Groq** (real model inference). Both use a local simulated mailbox and never deliver email. Initial permissions, versioned approvals, a SQLite audit trail and initial archive-preference learning are implemented. Real-model classification with the learning prompt is not yet verified because Groq returned HTTP 429.

## Real model via Groq

Create a Groq API key on a Free account and copy `.env.example` to `.env`, then fill `GROQ_API_KEY`. Keep `.env` untracked. Environment variables take precedence. The default model is `qwen/qwen3.8-27b`; `GROQ_MODEL` can explicitly override it. There is no provider fallback, automatic upgrade, or retry loop. Account billing is controlled in Groq, not by this application.

```sh
python3 -m mail_agent models
python3 -m mail_agent --db data/groq.sqlite3 ingest examples/incoming.json
python3 -m mail_agent.smoke --output data/groq-smoke.json
python3 -m mail_agent.smoke --extended --output data/groq-extended.json
```

`ingest` sends the provided email's sender, subject and body to Groq. Use synthetic data. JSON must contain `id`, `sender`, `subject`, `body` as nonempty strings. Reusing an ID returns its stored result without another model call, including a stored failure; use a fresh database or event ID for a deliberate new attempt. No automatic failed-event retry exists yet.

The adapter uses HTTPS and strict JSON Schema, validates output again locally, rejects truncated results, caps input size, and reports sanitized provider errors. It does not treat a valid JSON response as a safe or correct decision. Input instructions remain untrusted, and unsupported operations remain blocked by code. The model's suspicion detection is fallible; this version is not ready for unattended real-mail use.

The smoke command runs seven synthetic development cases, saving expectations, actual decisions, token counts and timings. It is not a held-out evaluation and does not measure adaptation. The committed run in `reports/` includes failures rather than concealing them. Official API documentation: [Groq structured outputs](https://console.groq.com/docs/structured-outputs).

The current instruction is in `mail_agent/prompts/triage-v4.txt`; historical v3 results do not establish v4 quality. V4 adds semantic pattern and risk fields to the existing safety priorities. The model is a fallible classifier; code still enforces action permissions separately. The `smoke` suite now expects a benign informational security article to propose archive with approval (previously a silent label); historical saved reports retain their original expectations. Rerunning v4 regression cases remains necessary after quota recovery.

`--extended` adds eight synthetic contrast cases (15 total). The smoke check verifies action, autonomy and execution status, exits nonzero for errors/mismatches, and stops at provider errors. Calls are spaced 20 seconds apart by default. After a quota interruption, use `--start INDEX` with a new output filename to run remaining cases; it is a zero-based index and does not rerun skipped cases. Keep the partial report. Free-tier quota may still interrupt a run; no billing upgrade or fallback occurs. Thresholds and prompts must be frozen before a future held-out evaluation; these are development checks.

## Run

Requires Python 3.11+; no third-party dependencies for this iteration. Run commands from this directory (`task/`).

Check `python3 --version` first. If it selects an older Python, use the path to your installed Python 3.11+ interpreter instead. Actual first-iteration check results are in `VERIFICATION.md`.

```sh
python3 -m mail_agent demo
python3 -m mail_agent show
```

The demo inserts seven synthetic incoming events. Repeating it does not duplicate processing. `data/demo.sqlite3` persists the mailbox and pending actions. Use a separate database for a fresh run:

```sh
python3 -m mail_agent --db data/another-demo.sqlite3 demo
```

The displayed `id` and `revision` identify the exact action to approve or reject. Inspect the proposal before approving. On a fresh demo database, the send is action 5:

```sh
python3 -m mail_agent approve 5 --revision 1
python3 -m mail_agent show
```

This inserts a **simulated** message into the local `sent` table. Approval cannot be reused. You can edit a pending send; editing increments its revision and invalidates old displayed approvals:

```sh
python3 -m mail_agent edit 5 --text 'Thank you, received.' --recipient colleague@example.test
# Only works while action 5 is pending; approve its new displayed revision.
```

Use `reject ACTION_ID --revision REVISION` to reject a pending action. The CLI represents the trusted local user's input; untrusted incoming text cannot approve actions.

## Verify

```sh
python3 -m unittest discover -s tests -v
```

Tests cover all four modes, actual local changes, blocked operations, required approvals, stale/replayed approvals, rejection, malformed proposer output, persistence and archive learning. The fake provider's `suspicious` flag is only a test input: these tests do not measure detection of real attacks. Code still blocks unsupported operations even when that flag is absent.

## Archive preference learning

Learning groups emails by communicative purpose across senders: `acknowledgement_only`, `periodic_digest`, `routine_success`, `informational_reference`. These are initial broad semantic categories, not automatically discovered clusters. Unknown cases do not qualify. Job applications, support tickets and material submissions can all be receipt acknowledgements; an interview invitation or substantive rejection is a different outcome.

For learning eligibility the model must propose archive, supply an exact body quote, and flag no required action, deadline, significant change, sensitive content, suspicion or notification need. These are fallible model assessments, not deterministic semantic guarantees. Three explicit archive approvals in the selected scope enable subsequent eligible archives **with notification**. Auto actions and lack of complaints never count as approvals. Sending always requires approval.

Approvals and rejections default to general experience for that pattern. Use `--scope sender` for experience limited to the exact sender and pattern; sender experience takes priority over general experience. Rejection or correction resets the approval count in its selected scope. Correction also restores the local inbox. An explicit keep rule overrides all learned archives and is rechecked before execution, including approval of an older pending action.

```sh
python3 -m mail_agent approve ACTION_ID --revision 1 --scope general
python3 -m mail_agent reject ACTION_ID --revision 1 --scope sender
python3 -m mail_agent correct-archive ACTION_ID --scope general
python3 -m mail_agent archive-rule --sender recruiter@example.test
python3 -m mail_agent archive-rule --sender recruiter@example.test --remove
```

Replace `ACTION_ID` with the displayed integer. Use the same `--db` path for ingest and feedback. Rules optionally accept `--pattern PATTERN`; omitted means all patterns. Sender defaults to `*` (all senders). Company affiliation is not inferred: add each exact address explicitly. This is preference scope, not authenticated sender trust. Feedback, rules and the approval IDs supporting each automatic archive are stored in SQLite and visible through `show`. Existing databases are upgraded by adding tables; legacy proposals lack eligibility fields and cannot train or use learned permission.

Run the small development comparison with a new output filename:

```sh
python3 -m mail_agent.learning_eval --scripted --output data/learning-policy.json
python3 -m mail_agent.learning_eval --output data/learning-groq.json
```

Both use three separate training fixtures with scripted evaluation-user approvals followed by six contrasting checks. The live version classifies each email once with Qwen and replays that same proposal through fresh and learned policies, isolating the memory effect and conserving quota. It writes results after every case and stops on provider error; rerun with a fresh filename after quota recovery. This is a development set, not a final held-out evaluation. The scripted version makes no API calls and measures only policy behavior. Reports include actual local archive effects, audit records and errors. See `VERIFICATION.md` for measured results and limitations.

## Boundaries and next iterations

- Sending requires approval; archiving initially asks and can use learned permission as described above. `AI: ` labels and drafts use initial permissions.
- One proposal per incoming event in this first slice; multi-step draft/send workflows are future work.
- Local SQLite operations execute in one transaction. This does **not** promise atomic or exactly-once Gmail delivery.
- Optional learned auto-replies, Gmail, web UI, summaries, reminders, Docker, comprehensive evaluation, and final DESIGN.md/transcripts are not implemented yet. Archive learning is limited to the initial semantic categories and still needs live quality measurements.
- The scripted demo requires no credentials. Groq commands need a key. Local databases and `.env` are excluded from Git.
