# Email agent — first iteration

Local execution and safety prototype for the Wajo take-home. **Not the finished AI agent.**

This iteration uses a **scripted proposer selected by fixture ID**, not an LLM. It makes no network requests, does not connect to Gmail, and never delivers email. It demonstrates four autonomy decisions, initial permissions, versioned approvals, a SQLite audit trail, and local mailbox effects. It does not measure model quality or prompt-injection detection.

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
- Live model integration, adaptation, optional learned auto-replies, Gmail, web UI, summaries, reminders, Docker, evaluation harness with measured model results, and final DESIGN.md/transcripts are not implemented yet.
- No credentials are needed. Use synthetic data; local database files are excluded from Git.
