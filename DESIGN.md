# Design — current local prototype

One Python application provides an HTTP UI, CLI and shared SQLite-backed policy core. New local emails enter a persistent queue; a background worker calls Qwen through Groq. The model proposes one structured action. Code validates the schema, binds the action to the server-selected email and determines autonomy. The executor checks policy again before making a local change. The browser never receives API credentials.

## Four autonomy levels

Safe labels and drafts use initial permission without notification, unless notification is relevant. Eligible learned archives execute with notification. Unlearned archives and all sends require approval of a specific stored action revision. Unsupported operations, unresolved human judgment and provider failures stop execution and are handed to the user. Suspected injection blocks mail actions and generates an informational notification; an ordinary approval cannot unblock it.

Payment, permanent deletion, shell commands and bulk mail access are not executable operations. A syntactically valid model response grants no authority. Recipient/text edits invalidate older send approvals. Sending is currently simulated, never delivered through Gmail. SQLite atomicity only applies to local operations; a future Gmail executor needs explicit handling of uncertain delivery outcomes.

## Preference adaptation

The model's weights are unchanged. SQLite stores explicit approvals/rejections by semantic purpose, optionally narrowed to an exact sender. Four initial purposes cover acknowledgements, digests, routine success reports and reference information. Three approvals since the most recent negative feedback allow an eligible archive with notification. Sender-specific experience overrides general experience; explicit keep-in-inbox rules override both. Corrections restore the inbox and reset learning in the chosen scope. Silence and autonomous actions never count as approval.

Eligibility also requires no model-indicated action request, deadline, meaningful change, sensitive content, suspicion or notification need, plus a verbatim body quote. This quote only establishes source occurrence: neither it nor the risk flags prove correct understanding. Cross-sender transfer increases coverage but can generalize too broadly. The initial taxonomy does not discover new groups or learn subtler boundaries. Learning cannot authorize sending or unsupported operations. Each learned archive logs the exact supporting feedback IDs and threshold.

## Errors and local UI

Inference retries are bounded to two for transient HTTP/transport errors and honor Retry-After within a bounded wait budget. All attempts and redacted error bodies are retained; 403 is not called a quota error. Retries never encompass email sending. Reports containing real input excerpts must stay local.

The UI is loopback-only, checks Host, Origin and CSRF on writes, serves fixed assets and escapes untrusted text under CSP. Counts come from actual mailbox state, and the cards filter the same rows. Queued input survives restarts; core event IDs prevent repeated local effects. A single worker/server per database is required. This is not a public authenticated application.

## Evaluation and remaining work

Unit tests exercise policy boundaries and web requests with adversarial proposals. Live development evaluations classify each synthetic email once, then replay that proposal through fresh and learned policies; this isolates the memory effect and avoids doubling inference costs. Training feedback is supplied by the evaluation's scripted user, and test inputs are separate from training examples. These small developer-written sets were used to refine prompts and are not held-out final evaluation. Full measured results, failures and version distinctions are in `VERIFICATION.md`.

The current interface and executor operate locally. Optional Gmail read-only OAuth and selected-label import are implemented but await user credentials and live validation. Gmail writes/polling, richer preference groups, follow-up reminders, time-based summaries, urgent notifications, optional learned replies and final independent evaluation remain future work. No claim of completed take-home or universal injection detection is made.
