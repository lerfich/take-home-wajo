# Email agent — development prototype

Local execution and safety prototype for the Wajo take-home. **Not the finished AI agent.**

Two explicit modes are available: **scripted demo** (offline fixtures) and **Groq** (real model inference). Both use a local simulated mailbox and never deliver email. Initial permissions, versioned approvals, a SQLite audit trail, archive-preference learning and a local web interface are implemented. Small real-model development checks are recorded in `VERIFICATION.md`; they are not final evaluation. Optional read-only Gmail import is coded but awaits the user's OAuth setup and live verification.

## Real model via Groq

Create a Groq API key on a Free account and copy `.env.example` to `.env`, then fill `GROQ_API_KEY`. Keep `.env` untracked. Environment variables take precedence. The default model is `qwen/qwen3.8-27b`; `GROQ_MODEL` can explicitly override it. There is no provider fallback or automatic upgrade. Account billing is controlled in Groq, not by this application.

Transient inference failures (HTTP 408/429/500/502/503/504 and transport failures) get at most two retries. `Retry-After` is respected; without it pauses are 5 and 10 seconds. A delay above 45 seconds or total planned waiting above 60 seconds stops the request. Other HTTP errors, including 401/403, and invalid model responses are not automatically retried. This retry applies only to model inference/catalog calls, never email delivery. Every HTTP attempt, response status, safe diagnostic headers and error body (up to 64 KiB, with explicit truncation flag) is recorded; API keys are redacted. A 429 alone is not reported as an exhausted daily quota. Failed responses can contain input excerpts: keep real-mail diagnostics local. See [Groq rate-limit headers](https://console.groq.com/docs/rate-limits).

```sh
python3 -m mail_agent models
python3 -m mail_agent --db data/groq.sqlite3 ingest examples/incoming.json
python3 -m mail_agent.smoke --output data/groq-smoke.json
python3 -m mail_agent.smoke --extended --output data/groq-extended.json
```

`ingest` sends the provided email's sender, subject and body to Groq. Use synthetic data. JSON must contain `id`, `sender`, `subject`, `body` as nonempty strings. Reusing an ID returns its stored result without another model call, including a stored failure; use a fresh database or event ID for a deliberate new attempt. No automatic failed-event retry exists yet.

The adapter uses HTTPS and strict JSON Schema, validates output again locally, rejects truncated results, caps input size, and reports sanitized provider errors. It does not treat a valid JSON response as a safe or correct decision. Input instructions remain untrusted, and unsupported operations remain blocked by code. The model's suspicion detection is fallible; this version is not ready for unattended real-mail use.

The smoke command runs seven synthetic development cases, saving expectations, actual decisions, token counts and timings. It is not a held-out evaluation and does not measure adaptation. The committed run in `reports/` includes failures rather than concealing them. Official API documentation: [Groq structured outputs](https://console.groq.com/docs/structured-outputs).

The current instruction is in `mail_agent/prompts/triage-v5.txt`. V4 added semantic pattern and risk fields; v5 clarifies that eligible informational patterns propose archive before the general label fallback. The model is a fallible classifier; code still enforces action permissions separately. The `smoke` suite now expects a benign informational security article to propose archive with approval (previously a silent label); historical saved reports retain their original expectations. Measurements are versioned in `VERIFICATION.md`; do not transfer historical v3 results to newer prompts.

`--extended` adds eight synthetic contrast cases (15 total). The smoke check verifies action, autonomy and execution status, exits nonzero for errors/mismatches, and stops if a provider call still fails after bounded retries. Calls are spaced 20 seconds apart by default. After an interruption, use `--start INDEX` with a new output filename to run remaining cases; it is a zero-based index and does not rerun skipped cases. Keep the partial report. No billing upgrade or fallback occurs. Thresholds and prompts must be frozen before a future held-out evaluation; these are development checks.

## Run

Requires Python 3.11+; core, Groq, web and evaluations use the standard library. Optional Gmail import needs `requirements-gmail.txt`. Run commands from this directory (`task/`).

## Local web interface

```sh
python3 -m mail_agent.web --db data/web-groq.sqlite3
```

Open [the local app](http://127.0.0.1:8765/). Add a synthetic email; a background worker invokes Groq and the page refreshes the resulting decision. View the message, confirm or reject the exact pending action, edit a pending reply, correct an archive, choose general/sender feedback scope, manage sender exceptions and inspect approval evidence/history. Queue state and provider diagnostics persist in SQLite. Interrupted local jobs resume on server restart; repeated core event IDs do not duplicate mail actions. Failed jobs remain visible with diagnostic details and are not silently reprocessed.

The overview counts actual current state for **all time**; pending actions have no age cutoff. Cards filter the same underlying message list. A time-range/last-visit summary and urgent-delivery notifications are not implemented in this slice. Notifications are recorded and visible within the page/history; no OS delivery is promised.

For an offline UI demo with prewritten decisions, use a separate database:

```sh
python3 -m mail_agent.web --demo --db data/web-demo.sqlite3
```

Click **Загрузить демо** to insert seven fixtures without model calls. Repeated loading is idempotent. Arbitrary input is disabled in scripted mode. Do not run two servers against one database or mix scripted and Groq data. The UI labels the selected mode; both transports are still local simulations. Gmail preparation is described in [GMAIL_SETUP.md](GMAIL_SETUP.md); no Gmail access exists yet.

The server binds only to `127.0.0.1`, checks Host/Origin and a per-process CSRF token for writes, limits request size, serves only three fixed assets, and applies CSP. Email/model content is escaped before DOM rendering; email HTML is not executed. This is a single-owner local prototype, not an authenticated public web service. Start the server again after code/prompt changes; closing the browser does not stop a running server.

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
python3 -m mail_agent.learning_eval --dataset evaluation/patterns-v1.json --output data/patterns-groq.json
```

The default set uses three training fixtures with scripted evaluation-user approvals followed by six checks; `patterns-v1.json` uses nine training fixtures and twelve checks for three other semantic patterns. The live version classifies each email once with Qwen and replays that same proposal through fresh and learned policies, isolating the memory effect and conserving quota. It writes results after every case and stops on a final provider error; rerun with a fresh filename after resolving the reported cause. These are development sets, not final held-out evaluation. The scripted version makes no API calls and measures only policy behavior. Reports include actual local archive effects, audit records and errors. See `VERIFICATION.md` for measured results and limitations.

## Boundaries and next iterations

- Sending requires approval; archiving initially asks and can use learned permission as described above. `AI: ` labels and drafts use initial permissions.
- One proposal per incoming event in this first slice; multi-step draft/send workflows are future work.
- Local SQLite operations execute in one transaction. This does **not** promise atomic or exactly-once Gmail delivery.
- Optional learned auto-replies, Gmail writes/polling, period summaries, reminders, Docker and comprehensive final evaluation are not implemented yet. Read-only Gmail OAuth/import awaits live verification. The web interface is a local first slice. Archive learning is limited to the initial semantic categories and has only small development measurements, not established production quality.
- The scripted demo requires no credentials. Groq commands need a key. Local databases and `.env` are excluded from Git.
