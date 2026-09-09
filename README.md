# Email agent — development prototype

Local execution and safety prototype for the Wajo take-home. **Not the finished AI agent.**

Two explicit modes are available: **scripted demo** (offline fixtures) and **Groq** (real model inference). Both use a local simulated mailbox and never deliver email. Initial permissions, versioned approvals and a SQLite audit trail are implemented; learning is not implemented yet.

## Real model via Groq

Create a Groq API key on a Free account and copy `.env.example` to `.env`, then fill `GROQ_API_KEY`. Keep `.env` untracked. Environment variables take precedence. The default model is `qwen/qwen3.8-27b`; `GROQ_MODEL` can explicitly override it. There is no provider fallback, automatic upgrade, or retry loop. Account billing is controlled in Groq, not by this application.

```sh
python3 -m mail_agent models
python3 -m mail_agent --db data/groq.sqlite3 ingest examples/incoming.json
python3 -m mail_agent.smoke --output data/groq-smoke.json
```

`ingest` sends the provided email's sender, subject and body to Groq. Use synthetic data. JSON must contain `id`, `sender`, `subject`, `body` as nonempty strings. Reusing an ID returns its stored result without another model call, including a stored failure; use a fresh database or event ID for a deliberate new attempt. No automatic failed-event retry exists yet.

The adapter uses HTTPS and strict JSON Schema, validates output again locally, rejects truncated results, caps input size, and reports sanitized provider errors. It does not treat a valid JSON response as a safe or correct decision. Input instructions remain untrusted, and unsupported operations remain blocked by code. The model's suspicion detection is fallible; this version is not ready for unattended real-mail use.

The smoke command runs seven synthetic development cases, saving expectations, actual decisions, token counts and timings. It is not a held-out evaluation and does not measure adaptation. The committed run in `reports/` includes failures rather than concealing them. Official API documentation: [Groq structured outputs](https://console.groq.com/docs/structured-outputs).

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

Tests cover all four modes, actual local changes, blocked operations, required approvals, stale/replayed approvals, rejection, malformed proposer output, and persistence. The fake provider's `suspicious` flag is only a test input: detecting real attacks remains future work. Code still blocks unsupported operations even when that flag is absent.

## Boundaries and next iterations

- Baseline mode only: sending and archiving require approval; `AI: ` labels and drafts use initial permissions.
- One proposal per incoming event in this first slice; multi-step draft/send workflows are future work.
- Local SQLite operations execute in one transaction. This does **not** promise atomic or exactly-once Gmail delivery.
- Adaptation, optional learned auto-replies, Gmail, web UI, summaries, reminders, Docker, comprehensive evaluation, and final DESIGN.md/transcripts are not implemented yet.
- The scripted demo requires no credentials. Groq commands need a key. Local databases and `.env` are excluded from Git.
