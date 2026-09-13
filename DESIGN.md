# Wajo design

Wajo is a local-first proactive email agent. One Python process serves the browser UI, polls Gmail, calls the selected language model and applies a SQLite-backed policy. The model interprets each email and returns four independent suggestions: Archive or Keep, an `AI:` label, a reply or human action, and a local calendar event. The application validates that structured response and decides what authority, if any, the suggestion has. Email text can influence classification, but it cannot grant permission.

## Autonomy and safety

Wajo exposes four user-facing levels:

| Level | Meaning | Typical outcome |
| --- | --- | --- |
| Act silently | The operation is local and allowed without interruption. | Store a safe suggestion or apply an existing local preference. |
| Act and notify | Wajo may complete an allowed operation and surface what happened. | Apply a qualified, reversible organization preference. |
| Ask first | The proposed operation needs approval of its exact saved revision. | Send a reply, archive an unlearned situation or confirm a calendar event. |
| Escalate | Wajo cannot safely complete the request because judgment or an unsupported action is required. | Payment, a legal decision, permanent deletion or missing facts. |

These levels describe the final policy decision, not confidence supplied by the model. Suspected instruction injection is blocked. A blocked attack may notify the user without offering an approval that could authorize the injected request. Payment, permanent deletion, shell commands, bulk forwarding and arbitrary mailbox access are unsupported operations.

Every reply is stored with its recipient, subject, body and revision. Editing any field invalidates the previous approval. By default, Gmail sending requires the user to approve that exact saved version. An unknown Gmail outcome is checked read-only and is never automatically retried.

Superpowers is the only configured exception to per-message send approval. It is off by default and bound to the current verified Gmail account. A matching Draft Skill revision must qualify through two successful manual Wajo sends whose saved recipient, subject and body were unchanged. Before an automatic reply is queued and immediately before delivery, Wajo checks the toggle, account, exact Skill revision, incoming sender, stored draft and safety flags again. Editing, rejecting, pausing or changing the Skill revokes or invalidates that authority. Automatic outcomes are recorded in the Autosent journal.

## Preference memory

Feedback changes Wajo's saved state; it does not fine-tune Qwen or update model weights. Preferences are separated by purpose so one choice cannot grant unrelated authority:

- Archive Skills learn Archive or Keep for a similar semantic situation after three consecutive eligible confirmations. Risk, deadlines, Important, Needs attention and explicit keep rules prevent automatic archiving.
- Label Skills remember one or two reviewed `AI:` labels. Gmail writes still use a separate versioned label decision.
- Organization Skills remember Topic, Subtype and the user's Important marker.
- Attention Skills decide whether matching mail appears in Needs attention. They do not create an escalation, OS notification or send permission.
- Draft Skills remember reviewed length, greeting, sign-off and a bounded before/after wording example. A constrained model rewrite may apply that style, but sending still follows the separate approval policy.
- Event Skills qualify after two consecutive confirmations of similar events and may then save matching events automatically to Wajo's local calendar. A correction removes the event and revokes the matching qualification.

New Skills are reviewed before activation. Matching starts with model-extracted semantic meaning; subject and sender only add context. Exact sender scope compares an address but does not authenticate its owner. Ordinary Skills can be reused across saved accounts, while Superpowers send authority remains account-bound.

## Gmail and local execution

Gmail uses Desktop OAuth with `gmail.modify`; Wajo does not request full mailbox or settings access. Imported content and account bindings are cached locally. New messages are polled every ten seconds for a configured account. Initial history can be limited to the latest 30, latest 100, all mail or new mail only, with up to ten optional Gmail labels for the history import.

Archiving removes the Gmail `INBOX` label; it does not delete the message. Applying a label, changing inbox membership, creating a draft and sending a reply are durable operations. Wajo records an operation before network I/O, reads Gmail afterward and only then records success. If the external result is uncertain, it preserves that state for read-only reconciliation rather than repeating the mutation.

Calendar events remain in local SQLite and never write to Google Calendar. Clear dates require confirmation until an Event Skill qualifies. Ambiguous dates or time zones require clarification. Later messages in the same Gmail thread can reschedule or cancel an existing event. Timed events can create a local reminder; urgent reply deadlines can create a macOS notification while the server is running.

The browser is served only on loopback. State-changing requests require the expected Host, Origin and CSRF token, and untrusted text is escaped under a content security policy. API keys stay server-side. One process must own a database; concurrent servers or CLI mutations against that same database are unsupported.

## Model boundary

The bundled path uses `qwen/qwen3.8-27b` through Groq with [email-analysis-prompt-v9](mail_agent/prompts/email-analysis-prompt-v9.txt). User-supplied Groq and OpenAI modes use their corresponding adapters. Only one provider mode is active. There is no second model acting as a Prompt Guard.

The model returns a strict structured proposal and exact evidence quotes. It never selects the stored email ID, executes a tool or grants autonomy. Code rejects invalid schemas and independently blocks unsupported or unsafe operations. This separation limits the effect of a bad classification, but it does not make semantic detection infallible.

## Evaluation

G1 uses 111 new synthetic emails to provide broader evidence than a small smoke test: 72 decision cases, 15 training cases with scripted evaluation-user feedback, and 24 linked controls. Calls use the production Groq adapter and current policy and are serialized for the free quota. Each model response is saved atomically, checkpoints are written every 11 emails, and an interrupted run skips completed responses. The report can be rebuilt from saved responses without another model call. The original free Groq account reached its daily allowance after 60 usable responses, so the remaining 51 frozen IDs were completed in a second attempt with a new free-account credential on the same primary `qwen/qwen3.8-27b` model. Provider failures without a model response are retryable infrastructure events and are not quality measurements; incorrect classifications in received model responses remain measured.

The completed G1 run produced the following headline results:

| Measure | Result |
| --- | ---: |
| Usable model responses | 111/111 |
| Final policy level | 50/72 (69.4%) |
| Unsafe autonomous primary actions | 0/72 |
| Authored instruction-injection cases blocked | 6/6 |
| Preference-transfer controls | 24/24 |

Silent and Escalate were strongest at 18/18 and 17/18. Notify (6/18) and Ask (9/18) were substantially weaker, and Archive/Keep was the weakest component classification at 46/72. Preference transfer covered 14 similar and 10 contrasting controls for Organization and Attention only; it does not demonstrate fewer Ask prompts. Full component scores, frozen hashes, attempt history and limitations are in [reports/g1/REPORT.md](reports/g1/REPORT.md). Scripted feedback represents an evaluation user, not project-owner activity, and before/after controls measure Wajo's stored memory rather than model training.

G1 measures model interpretation and local policy/memory on synthetic inputs. It does not measure UI usability, private-mail usefulness, Gmail delivery or Gmail-only qualification flows. Those paths have separate functional evidence and limitations in [VERIFICATION.md](VERIFICATION.md). A finite synthetic set cannot establish correctness for arbitrary mail or universal prompt-injection detection.

Synthetic flows and links to their saved G1 records are in [examples/transcripts.md](examples/transcripts.md). Any continuation beyond the direct harness boundary is explicitly marked as illustrative.
