# Design — current local prototype

One Python application provides an HTTP UI, CLI and shared SQLite-backed policy core. New local emails enter a persistent queue; a background worker calls Qwen through Groq. The model proposes one structured action. Code validates the schema, binds the action to the server-selected email and determines autonomy. The executor checks policy again before making a local change. The browser never receives API credentials.

## Four autonomy levels

Safe labels and drafts use initial permission without notification, unless notification is relevant. Eligible learned archives execute with notification. Unlearned archives and all sends require approval of a specific stored action revision. Unsupported operations, unresolved human judgment and provider failures stop execution and are handed to the user. Suspected injection blocks mail actions and generates an informational notification; an ordinary approval cannot unblock it.

Payment, permanent deletion, shell commands and bulk mail access are not executable operations. A syntactically valid model response grants no authority. Recipient/text edits invalidate older send approvals. Local sends are simulated; explicitly live Gmail replies require approval of the exact saved version. SQLite atomicity only applies to local operations; Gmail delivery handles uncertain outcomes separately as described below.

## Preference adaptation

The model's weights are unchanged. SQLite stores explicit approvals/rejections by semantic purpose, optionally narrowed to an exact sender. Four initial purposes cover acknowledgements, digests, routine success reports and reference information. Three approvals since the most recent negative feedback allow an eligible archive with notification. Sender-specific experience overrides general experience; explicit keep-in-inbox rules override both. Corrections restore the inbox and reset learning in the chosen scope. Silence and autonomous actions never count as approval.

Eligibility also requires no model-indicated action request, deadline, meaningful change, sensitive content, suspicion or notification need, plus a verbatim body quote. This quote only establishes source occurrence: neither it nor the risk flags prove correct understanding. Cross-sender transfer increases coverage but can generalize too broadly. The initial taxonomy does not discover new groups or learn subtler boundaries. Learning cannot authorize sending or unsupported operations. Each learned archive logs the exact supporting feedback IDs and threshold.

Draft-style memory is separate from action permission. After a user saves a reviewed reply revision, the UI derives its approximate length, greeting presence and sign-off presence. For a changed body, the bounded original/user text pair is also retained as an explicit phrasing example; the UI displays both before saving. An unchanged body requires a separate explicit confirmation that its style works for the user. Send approval alone is never interpreted as style feedback. The user chooses whether that style applies to the same evidenced situation kind across senders or only from the exact sender. A later matching reply proposal is passed through a second constrained Qwen rewrite with the structural rule and, when present, the example pair. The prompt forbids copying situation-specific content, but the rewrite remains probabilistic and may change meaning, so it never bypasses exact-version send approval. The original model draft is the fallback if rewriting fails or returns invalid text. Saving a rule does not approve either the current or a future reply.

## Errors and local UI

Inference retries are bounded to two for transient HTTP/transport errors and honor Retry-After within a bounded wait budget. All attempts and redacted error bodies are retained; 403 is not called a quota error. Retries never encompass email sending. Reports containing real input excerpts must stay local.

The UI is loopback-only, checks Host, Origin and CSRF on writes, serves fixed assets and escapes untrusted text under CSP. Counts come from actual mailbox state, and the cards filter the same rows. Queued input survives restarts; core event IDs prevent repeated local effects. A single worker/server per database is required. This is not a public authenticated application.

## Evaluation and remaining work

Unit tests exercise policy boundaries and web requests with adversarial proposals. Live development evaluations classify each synthetic email once, then replay that proposal through fresh and learned policies; this isolates the memory effect and avoids doubling inference costs. Training feedback is supplied by the evaluation's scripted user, and test inputs are separate from training examples. These small developer-written sets were used to refine prompts and are not held-out final evaluation. Full measured results, failures and version distinctions are in `VERIFICATION.md`.

The application runs locally and supports optional real Gmail execution. OAuth, selected-label import and Gmail execution have small live integration checks, not classification accuracy measurements. Gmail polling, richer preference groups, follow-up reminders, time-based summaries, attention/escalation evaluation and final independent evaluation remain future work. Learned preferences may improve a draft, but automatic replies are intentionally out of scope: every send always requires approval of the exact stored version. No claim of completed take-home or universal injection detection is made.

The application UI and new generated explanations use English (prompt triage-v6). Original email content and exact evidence retain their language. Historical decisions are preserved. Archive learning is the first adaptation feature; learning labels, drafting preferences, notification preferences and replies requires separate feedback scopes and evaluation. Archive experience never authorizes another action.

## Reversible Gmail execution

The web connection panel reuses Desktop OAuth with PKCE and a loopback callback. CSRF-protected POST requests initiate a single background connection/check/sync operation; state reads perform no Gmail I/O. The server fixes OAuth access and import transport from its startup mode, requires explicit Groq consent and the displayed account/mode, and rechecks account and scope before import. OAuth/token refresh and imports share the Gmail executor lock, with a fresh client per operation. Raw provider errors and credentials are excluded from public state. Connection verification and sync summaries are process-local; queued emails persist. This first panel reads one bounded page and does not implement polling or pagination.

Explicit live imports atomically store a trusted account/message/test-label binding alongside the queued email. Action transport is persisted, with old actions defaulting to local simulation. The core writes a durable Gmail operation in the same transaction as the decision or approval. A single-server worker revalidates policy and current mailbox scope before using messages.modify for AI labels or INBOX membership. New live send/draft proposals stage a Gmail draft before any send approval.

Network I/O is outside the database transaction. The operation is first committed as processing, then verified by reading Gmail before local success and positive feedback are recorded. Crashes and ambiguous failures become unknown; no automatic mutation replay occurs. A read-only reconciliation button may confirm the intended state. Corrections reset learning immediately and queue restoration. Failures do not pretend the message was restored. An unused created AI label can remain after partial failure.

This is a single-server design. Concurrent CLI mutations or multiple servers per database are unsupported. Gmail changes between preflight and mutation remain a race; post-read state can subsequently become stale. The implementation cannot atomically lock an external mailbox and SQLite together.

## Exact-version reply delivery

Reply records store recipient, subject, body and a unique per-version marker separately from model output. Draft creation/update and send operations have distinct durable queue keys. Every edit increments the revision; approval is unavailable until the draft is saved and verified. An approval hashes the stored outgoing MIME payload and is bound to the action/revision. Archive learning never grants send permission.

Before sending, the worker validates current source scope and policy, verifies the saved draft content and includes the approved MIME bytes in drafts.send. This avoids a draft-edit race substituting different outgoing content. Sending remains a separate user decision even when the model requested only a draft. External draft edits or disappearance stop execution. Rejection leaves the unsent Gmail draft available for the owner.

Gmail rewrites draft Message-ID, discovered during live verification. Matching therefore uses the per-revision X-Wajo-Reply-Key and decoded From/To/Subject/Body, rejects extra recipients and attachments, and compares threading headers. A send response ID is saved before readback. Unknown outcomes are reconciled read-only by known ID or a bounded search/scan of Sent; lack of evidence remains unknown, never permission to retry. No exactly-once delivery guarantee is claimed. Independent manual sends, multiple databases, indexing delays and edits/removal of tracking headers are residual limitations. One server per database remains required.

API basis: [Google draft creation/update/send guide](https://developers.google.com/workspace/gmail/api/guides/drafts), including supplying approved MIME in the send request.

## Explicit label and attention preferences

Label feedback is stored separately from archive approvals. Versioned reviews replace only the previous AI label, verify Gmail state, and then activate account/kind/sender-scoped preferences. Unknown external outcomes are not retried automatically. A finite model-classified purpose taxonomy groups paraphrases; an exact evidence quote is required before applying a label preference. This is explicit preference memory, not model fine-tuning. Sender-specific label rules override general rules. Attention is an explicit visibility preference with its own feedback history and email/kind/sender scope. A match surfaces mail in **Needs attention** and prevents learned automatic archiving, but does not change autonomy to Notify, mark the email Important or create/suppress an escalation. Future rules require an evidenced situation kind. No label or organization name changes hard action permissions.

Topic, subtype and importance now form a separate local organization layer. The finite evidenced situation kind supplies a conservative initial topic/subtype pair. A user edit can apply to one email, that kind across senders, or that kind from one exact sender; the narrow rule wins and either rule can be paused. Importance is a boolean user marker rather than a mandatory model-generated scale. These fields do not change Gmail labels, attention, action selection or permissions. A 12-case development contrast check measured correct kind classification and transfer for the selected preferences, but it is small and developer-written; independent model-quality evaluation remains required.
