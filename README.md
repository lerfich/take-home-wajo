# Email agent — development prototype

## Start the current Gmail workspace

From `task/`, run `./run.sh` and open http://127.0.0.1:8765. This uses the project virtual environment and `data/web-groq.sqlite3`. Install dependencies once in that environment as described in GMAIL_SETUP.md. Do not run two servers against one database.

Gmail is enabled by default in the web server. The saved token is checked on startup; Gmail settings displays the verified account. Connect/Reconnect opens Google and also provides a **Continue with Google** link while authorization is pending. If verification finds a different account, synchronization and analysis stop until the user chooses **Keep previous data** or **Start fresh**. Keep preserves the shared local timeline and its portable Skills; Start fresh shows deletion counts and atomically clears Wajo's local mail-derived state without changing Gmail. `--local-simulation` explicitly selects local execution; existing local records never become live actions. The standalone Gmail import CLI still requires `--gmail-live` to create live bindings.

## Review labels and attention

The 30-email synthetic exercise uses `labels-v1`, a label-only model prompt. **To review** is a category-review queue, separate from **Pending** action approvals. Confirm a label, select an existing name, or type a new one. Wajo verifies the Gmail change before marking a review complete. Unknown outcomes require read-only reconciliation, never automatic mutation retries. Choose **Replace / confirm current label** to replace the displayed AI label, or **Add another label** to keep existing labels and add one more. A label Skill can carry at most two additional `AI: ` labels; Topic and Subtype do not count toward that limit. If applying it would produce a third additional label, Wajo preserves the current labels and asks the user to select exactly two. Unrelated Gmail labels remain.

**This email only** corrects the present label without teaching a general rule. Choosing **Future similar emails** or **This situation from this sender** creates a Suggested Skill, not an active rule. There are 15 initial semantic kinds. A model supplies the kind and supporting body quote, so matching remains fallible. The user must review its real saved examples and explicitly save the current preview before it can affect later mail.

Topic, subtype and importance are edited separately from the Gmail label. The initial hierarchy maps the 15 evidenced situation kinds to pairs such as **Applications / Receipt** and **Applications / Interview**. A user can keep an edit on one email or propose a Skill for the same kind across senders or from one exact sender. Sender-specific active Skills take priority. **Important** is an explicit user marker; there is no automatic Important/Normal/Low scale. Organization never authorizes archiving, sending, attention alerts or any other mail action. Existing label reviews remain unchanged.

## Learn draft style from an edit

When the agent proposes a reply, review and save its body before approving it. Wajo then shows a short style summary derived from that exact revision: approximate length, whether it starts with a greeting, and whether it ends with a sign-off. A changed body appears as **Learn from your edit**. An unchanged body instead asks whether the proposed style works for you; it is learned only if you explicitly select **This style works for me**. Choose **future drafts for this kind of email** or narrow the preference to the same kind from the exact sender. Either choice creates a Suggested Skill that remains inactive until its preview is reviewed and saved.

An active Draft Skill contains those three structural choices and, when wording changed, the bounded before/after edit as a tone example. For a later matching proposal, Qwen receives the original draft plus the explicit style rule and example, and may rewrite the body. It is instructed to infer reusable wording tendencies without copying people, facts, dates, promises, attachments or requested actions from the old example. If rewriting fails or returns invalid text, Wajo keeps the original draft and records the fallback. The model can still alter meaning incorrectly. By default every send remains pending until the user approves its exact saved version; saving a Draft Skill alone does not approve or send anything.

## Optional Superpowers auto-send

Superpowers is off by default. Turning on its global toggle is the only path that permits a reply without a separate per-message approval, and it applies only to the currently verified Gmail account. A matching active Draft Skill must first qualify on that account through **two successful manual Gmail sends through Wajo with unchanged recipient, subject and body**. Permission is bound to the exact Skill revision and account; editing, pausing or deleting the Skill, rejecting or changing an applied draft, or disabling that Skill's permission revokes or invalidates qualification. Switching accounts does not transfer the toggle or confirmations to the newly connected account.

Even then, Wajo only queues the exact verified Gmail draft for the exact incoming sender. Attachments, a changed recipient or draft, suspected injection, human-judgment or sensitive flags, and money/legal/security-sensitive wording block auto-send. Authority is revalidated immediately before Gmail delivery. Unknown delivery outcomes are recorded and never automatically retried. The **Autosent** view records the exact recipient, subject, body, Skill revision and delivery state; the user can disable one qualified Skill or turn off the global toggle at any time.

This path currently has only unit/integration verification with fixtures, synthetic SQLite databases and mocked Gmail transport. No automatic reply has been sent through a live mailbox, and no private-mail pilot has been performed.

This workflow also works on locally added synthetic emails, so draft learning can be tested without Gmail. Use a supported, clearly evidenced situation such as an interview scheduling request, a work review request, or a substantive support reply. Model classification remains fallible; unknown or unevidenced situations cannot create a future style rule.

Run the organization contrast check after saving explicit future preferences in the main local database:

```sh
python3 -m mail_agent.organization_eval
```

It sends twelve new synthetic emails to Qwen, then replays each returned proposal through a fresh organization policy and through a copy of the user's active general organization rules. This separates model kind classification from preference transfer and never imports the examples into Gmail. The committed September 10 development report recorded **12/12** kind matches, organization matches **8/12 before → 12/12 after**, **4/4** expected Important transfers, and **0/8** false Important results. This small developer-written set is not the final independent evaluation.

The separate **Keep this in Needs attention** control updates the current email and can propose a visibility Skill with semantic-similarity or exact-sender scope. Future scopes require a semantic attention cue supported by an exact quote, rather than the same Topic/Subtype. Once reviewed and activated, matching emails appear in **Needs attention** and do not qualify for learned automatic archiving. **Clear from Needs attention** clears the current attention item without disabling the future Skill. Visibility does not change autonomy to **Notify**, mark an email **Important**, create or suppress an **Escalation**, send an OS push notification or authorize sending. The synthetic labels run is not an evaluation of those features.

## Review and manage Skills

Feedback can propose Skills for Attention, Topic/Subtype/Important, additional labels, draft style and conservative archive preferences. Suggested Skills have no effect. The preview uses already processed messages across saved accounts, shows **Applies**, **Does not apply** or **Needs confirmation**, and does not call the model or mutate mail. Missing positive or negative contrasts are reported rather than invented. The user can change sender/similar scope, supported meaning, literal body requirements, result settings and per-message exclusions; any change invalidates earlier reviews. Activation requires every displayed example to be reviewed and saves the exact current preview atomically.

Preferences lists the Skill's source account, family, source feedback, scope, conditions and exceptions. **Pause** keeps its history but stops matching; **Resume** restores it; **Delete** permanently removes the Skill and its training examples after a separate confirmation while retaining the action audit. Ordinary matching is portable across saved accounts and deterministic between preview and application; sender scope still means one exact address and is not authentication. Portability never carries send authority: only the separately account-bound, qualified Draft Skill revision plus the current-account Superpowers toggle can authorize auto-send.

Both the email list and detail pane scroll independently on desktop. Filters include Pending, Needs attention, Escalated, Archived, To review and Reviewed.

Local execution and safety prototype for the Wajo take-home. **Not the finished AI agent.**

Two explicit modes are available: **scripted demo** (offline fixtures) and **Groq** (real model inference). Local simulation remains the default; explicit Gmail live mode supports labels, archive, restore, drafts and approved replies. Live Gmail replies create a draft and send only after exact-version approval; local sends remain simulations. Initial permissions, versioned approvals, a SQLite audit trail, managed Skills and a local web interface are implemented. Small real-model development checks are recorded in `VERIFICATION.md`; they are not final evaluation. Gmail OAuth/import and limited reversible writes have been exercised; the latest private-mail Stage C attempt imported 30 messages, but Groq access later returned HTTP 403, so it is explicitly recorded as partial rather than successful end-to-end validation. See `GMAIL_SETUP.md` and `VERIFICATION.md`.

## Real model via Groq

Create a Groq API key on a Free account and copy `.env.example` to `.env`, then fill `GROQ_API_KEY`. Keep `.env` untracked. Environment variables take precedence. The default model is `qwen/qwen3.8-27b`; `GROQ_MODEL` can explicitly override it. There is no provider fallback or automatic upgrade. Account billing is controlled in Groq, not by this application.

Transient inference failures (HTTP 408/429/500/502/503/504 and transport failures) get at most two retries. `Retry-After` is respected; without it pauses are 5 and 10 seconds. A delay above 45 seconds or total planned waiting above 60 seconds stops the request. Other HTTP errors, including 401/403, and invalid model responses are not automatically retried. This retry applies only to model inference/catalog calls, never email delivery. Every HTTP attempt, response status, safe diagnostic headers and error body (up to 64 KiB, with explicit truncation flag) is recorded; API keys are redacted. A 429 alone is not reported as an exhausted daily quota. Failed responses can contain input excerpts: keep real-mail diagnostics local. See [Groq rate-limit headers](https://console.groq.com/docs/rate-limits).

```sh
python3 -m mail_agent models
python3 -m mail_agent --db data/groq.sqlite3 ingest examples/incoming.json
python3 -m mail_agent.smoke --output data/groq-smoke.json
python3 -m mail_agent.smoke --extended --output data/groq-extended.json
```

`ingest` sends the provided email's sender, subject and body to Groq. Use synthetic data. JSON must contain `id`, `sender`, `subject`, `body` as nonempty strings. Reusing an ID returns its stored result without another model call. The web inbox retries incomplete analysis with a persisted exponential delay until a complete decision is stored; the standalone CLI remains explicitly idempotent and does not retry an existing failed event unless its caller requests that behavior.

The adapter uses HTTPS and strict JSON Schema, validates output again locally, rejects truncated results, caps input size, and reports sanitized provider errors. It does not treat a valid JSON response as a safe or correct decision. Input instructions remain untrusted, and unsupported operations remain blocked by code. The model's suspicion detection is fallible; this version is not ready for unattended real-mail use.

The smoke command runs seven synthetic development cases, saving expectations, actual decisions, token counts and timings. It is not a held-out evaluation and does not measure adaptation. The committed run in `reports/` includes failures rather than concealing them. Official API documentation: [Groq structured outputs](https://console.groq.com/docs/structured-outputs).

The current instruction is in `mail_agent/prompts/triage-v9.txt`. It requests English explanations, labels and reply text while preserving verbatim evidence, and supplies the finite semantic fields used by the current preference layers. The model is a fallible classifier; code still enforces action permissions separately. Historical measurements are versioned in `VERIFICATION.md`; do not transfer results from an older prompt to triage-v9, which is not yet frozen for final evaluation.

`--extended` adds eight synthetic contrast cases (15 total). The smoke check verifies action, autonomy and execution status, exits nonzero for errors/mismatches, and stops if a provider call still fails after bounded retries. Calls are spaced 20 seconds apart by default. After an interruption, use `--start INDEX` with a new output filename to run remaining cases; it is a zero-based index and does not rerun skipped cases. Keep the partial report. No billing upgrade or fallback occurs. Thresholds and prompts must be frozen before a future held-out evaluation; these are development checks.

## Run

Requires Python 3.11+; core, Groq, web and evaluations use the standard library. Optional Gmail import needs `requirements-gmail.txt`. Run commands from this directory (`task/`).

## Local web interface

```sh
python3 -m mail_agent.web --db data/web-groq.sqlite3
```

Open [the local app](http://127.0.0.1:8765/). Add a synthetic email; background workers invoke Groq for at most seven messages concurrently and the page refreshes the resulting decisions. View the message, confirm or reject the exact pending action, edit a pending reply, optionally save its structural style for matching future drafts, correct an archive, choose general/sender feedback scope, manage sender exceptions and inspect approval evidence/history. Queue state, retry timing and provider diagnostics persist in SQLite. Interrupted or failed analysis is retried because an email is not considered processed until its complete decision is stored. Repeated Gmail message IDs do not duplicate actions.

The overview counts actual current state for **all time**; pending actions have no age cutoff. Cards filter the same underlying message list. A time-range/last-visit summary and urgent-delivery notifications are not implemented in this slice. Notifications are recorded and visible within the page/history; no OS delivery is promised.

**Connect Gmail** opens connection settings in the UI. With the local Google Desktop client configured, sign in through Google or verify an existing connection. Select last 30, last 100, all existing mail, or only future mail. Optional Gmail-label selections are displayed with a live counter and limited to ten; they constrain initial history only, while every later new message is considered regardless of those labels. Review the account and execution mode, then explicitly permit the selected email text to be sent to Groq before clicking **Start synchronization**. History is paginated, deduplicated by Gmail message ID and can be paused or resumed with persisted progress. Automatic catch-up is enabled by default and can be turned off without losing its history cursor. Sending requires exact-version approval unless the explicitly enabled, narrowly qualified Superpowers path above applies. See [GMAIL_SETUP.md](GMAIL_SETUP.md) for setup and limitations.

For an offline UI demo with prewritten decisions, use a separate database:

```sh
python3 -m mail_agent.web --demo --db data/web-demo.sqlite3
```

Click **Load sample cases** to insert seven fixtures without model calls. Repeated loading is idempotent. Arbitrary input is disabled in scripted mode. Do not run two servers against one database or mix scripted and Groq data. The UI labels each action as local simulation or real Gmail execution. Gmail preparation is described in [GMAIL_SETUP.md](GMAIL_SETUP.md).

The server binds only to `127.0.0.1`, checks Host/Origin and a per-process CSRF token for writes, limits request size, serves only an explicit allowlist of fixed assets, and applies CSP. Email/model content is escaped before DOM rendering; email HTML is not executed. This is a single-owner local prototype, not an authenticated public web service. Start the server again after code/prompt changes; closing the browser does not stop a running server.

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

For learning eligibility the model must propose archive, supply an exact body quote, and flag no required action, deadline, significant change, sensitive content, suspicion or notification need. These are fallible model assessments, not deterministic semantic guarantees. Three explicit archive approvals in the selected scope enable subsequent eligible archives **with notification**. Auto actions and lack of complaints never count as approvals. Sending requires approval by default; archive learning never grants the separate Superpowers permission.

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

- Sending requires approval by default; only an enabled, account-bound Superpowers permission for a qualified Draft Skill revision can bypass per-message approval. Archiving initially asks and can use learned permission as described above. `AI: ` labels and drafts use initial permissions.
- One model proposal per incoming event; live replies can progress through draft creation, edits and exact-version approved sending.
- Local SQLite operations execute in one transaction. This does **not** promise atomic or exactly-once Gmail delivery.
- Period summaries, reminders, Docker and comprehensive final evaluation are not implemented yet. Gmail pagination, reconnect account choice, persisted Pause/Resume, cursor-based polling, thread grouping, special-message context, portable Skills and Superpowers are implemented with mocks and synthetic databases; these additions have not received an end-to-end live mailbox check. Earlier Gmail import, reversible writes and an explicitly approved synthetic self-send are small transport checks for older paths, not validation of reconnect or automatic replies. No automatic reply has been sent live, and no private-mail pilot or final independent evaluation has been performed.
- The scripted demo requires no credentials. Groq commands need a key. Local databases and `.env` are excluded from Git.

Default live reply workflow: review From/To/Subject/Body, save edits as a new verified Gmail draft revision, then explicitly approve sending. Only the separately enabled and qualified Superpowers path can omit that last per-message approval. Unknown delivery outcomes allow read-only reconciliation and never automatic resend. See [GMAIL_SETUP.md](GMAIL_SETUP.md) for bounds and manual-intervention cases.
