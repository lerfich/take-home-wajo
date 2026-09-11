# Iteration 1 verification

## September 11: stage B Skills completed on synthetic data and mocks

**145 tests passed** in the latest full run. A shared, account-bound Skills layer now covers Needs attention, Topic/Subtype/Important, up to two additional AI labels, draft style, and conservative archive preferences. Feedback creates a **Suggested** skill that has no future effect until every displayed example is reviewed and the exact current preview token is saved. The same deterministic matcher is used for preview and live application. Tests cover positive, negative and unknown outcomes, sender/account boundaries, literal refinements, exclusions, stale reviews, atomic activation, restart, Pause/Resume/Delete, legacy migration, archive corrections and the three-approval threshold. Sending remains outside Skills and still requires exact-version approval.

Additional-label tests cover transfer of two labels, local third-label conflict, explicit selection of exactly two, preserving existing labels until that choice, Gmail application of two labels, and refusing a third Gmail AI label before any message mutation. Gmail label-set resolution is durable and uses read-only reconciliation after an unknown result; it is covered by policy/mocked transport tests, not a new live Gmail write.

The in-app browser exercised the clean scripted demo at `127.0.0.1:8766` on `data/stage-b-demo.sqlite3`: label, organization, draft-style and archive feedback each opened a Suggested skill; supported meaning, contains/excludes, result settings and up-to-two-label editing were visible; closing left suggestions inactive; a previously reviewed Attention skill covered full review, activation, Pause and Resume, and Delete exposed a separate irreversible confirmation. Archive preview showed **1 of 3 real approvals** and kept asking. The edited reply remained **Approval needed**; the demo database has 7 actions, 4 suggested skills, 0 sent rows and 0 Gmail operations. Browser console warning/error inspection returned no entries.

A temporary copy of `data/web-groq.sqlite3` retained **46 actions before and after** migration, produced 21 active Skills from previously active rules, produced zero retrospective suggestions from old one-email feedback, and had zero queued/processing Gmail operations. The production database was not modified and no server was listening on port 8765. No Gmail mutation, model call, private-mail import or send was performed for this verification. This is development verification, not the final independent evaluation or a private-mail pilot. The general assignment UX remains intentionally scheduled for stage F after real use.

## Stage B: first Attention skill preview

135 tests passed in 7.833 seconds. The preview uses an isolated SQLite copy and the existing Attention matcher, with already processed examples from the same account. Tests cover zero preview mutations, review-before-save, stale state rejection, account isolation, email exclusions, and atomic rollback of a skill plus exclusions after a simulated failure. JavaScript syntax checks pass.

The selected two-column dialog was tested in the in-app browser on a separate synthetic database (`skills-design-demo.sqlite3`, port 8766): positive/negative/unknown outcomes, an email-only exclusion, resetting review after changes, all four example confirmations, final save, reopening, and scope changes. Saving showed the success message and one visible Attention item; no Gmail operations were queued. Browser console error inspection returned no entries. The production database and Gmail were not modified. The demo server remains available on port 8766.

This is the first Attention integration, not completion of stage B. It does not yet offer skills automatically after ordinary feedback, generate new contrasts, support arbitrary semantic corrections, manage Pause/Resume/Delete, or apply the dialog to the other preference types. A saved existing classification is not a new model-quality measurement. Existing direct preference routes remain available. Narrow-layout inspection confirmed stacked columns, but the browser's viewport screenshot had capture artifacts, so exact mobile visual QA remains incomplete.

## September 10: stage A stabilization completed

130 tests passed in 7.754 seconds. Regression coverage now includes email → sender → general Attention precedence with disabled exceptions; enable/disable/enable history and repeated-save idempotency; state changes from another example; explicit none/unknown versus a missing legacy cue; legacy rule migration; restart and duplicate ingestion without historical execution. Existing cross-topic semantic transfer tests also pass. Missing legacy cue fields use an empty-string sentinel; already stored explicit unknown/none values remain authoritative. Historical proposal JSON and feedback are not rewritten or reconstructed.

The state API exposes the effective rule, and the form uses it for its checkbox and scope. Disabled exceptions are displayed in Preferences. The state API regression, JavaScript syntax check and isolated form execution passed for enabled/disabled sender overrides. This is focused UI verification, not a full browser usability test.

A SQLite backup of the existing database retained an identical snapshot after reopening: 46 actions, 4 Attention rules and 7 Attention feedback entries. The original database was not modified. No server was listening on port 8765; all 63 stored Gmail operations were done. The server remains stopped.

A4: a read-only Gmail get by the saved sent_id of the previously approved revision 2 returned the same ID, matching X-Wajo-Reply-Key, exact approved headers and plain-text body, and both SENT and INBOX labels on that specific message. This establishes current Inbox membership of the reply itself. No Gmail write, send, retry, import or model call was performed. This supplements the original self-delivery report without rewriting it. These checks are development verification on synthetic mail, not final independent evaluation or a private-mail pilot.

## September 10: follow-up audit and limits of previous claims

The final independent evaluation remains pending until feature completion, real-mail piloting and resulting fixes. The earlier development measurements below retain their original results.

Three Attention edge cases were reproduced in an isolated in-memory database: a disabled sender rule does not override an enabled general rule; enable/disable/enable records only the first two choices and incorrectly reports the last one as unchanged; an explicit `attention_cue=none` can fall back to a legacy label kind. These are open defects found after the 125-test suite; passing that suite did not cover them.

Exact-sender Attention matching is an address filter, not sender authentication or an escalation policy. The contrast evaluator invokes Qwen without the copied Attention rules, then applies those rules locally. Therefore the other-account message's escalation cannot be attributed to a learned trusted-sender boundary. The regraded escalation expectations are development judgments made after inspecting outputs.

The previous self-delivery check searched Inbox for the subject, which could also match the original incoming message. It does not establish that the specific sent reply was delivered to Inbox. Sent readback and exact approved-content verification remain supported; exact Inbox delivery needs a message/marker-and-body check. The original report is preserved as recorded, with this correction qualifying its self-delivery claim.

## September 10: exact-version live reply approval

A new synthetic message addressed only to the connected test account exercised the complete live path with real Qwen and `triage-v9`. The model proposed a reply, Gmail verified draft revision 1, the user edited the body, and Gmail verified revision 2. Nothing was sent until the user personally clicked **Approve and send via Gmail** for that displayed revision. The worker then completed `send:2`; a read-only check confirmed the Sent label, the exact approved recipient/subject/body and self-delivery in Inbox. There was one send to the same test account and zero automatic sends or external recipients. See [exact-reply-approval-2026-09-10.json](reports/exact-reply-approval-2026-09-10.json).

An initial negative contrast explicitly described a fictional scenario with no real request. Qwen selected no action, so no draft or send was created. These two cases check conservative gating, transport and exact-version permission binding; they are not a general reply-quality evaluation. The existing **125 tests** still pass.

## September 10: semantic attention transfer and escalation contrasts

**125 tests passed.** Attention transfer now uses an independently classified semantic cue with an exact supporting quote. This lets confirmed personal commitments transfer across topics while keeping ordinary team schedule changes separate. Exact-sender account rules do not trust a different sender. Repeated identical saves are idempotent, distinct examples remain auditable, and disabling a shared semantic cue disables all legacy kind rows for that scope. The UI deduplicates those legacy rows and calls dismissal **Clear from Needs attention**.

A 12-email developer-written contrast set was run with real Qwen and `triage-v9`: 12/12 attention cues matched. With the saved user preferences, 4/4 expected emails entered Needs attention, with 0 misses and 0 false positives; 5/5 expected escalations matched, with 0 misses and 0 false escalations. The first v8 run and raw v9 run remain in separate reports. Two expectations were corrected after review—an actionable service failure should escalate, and an account-security message from an untrusted sender should be handled conservatively—then the already saved v9 proposals were regraded without another model call. This is a transparent development check, not an independent final evaluation. No Gmail mutation or send occurred.

## September 10: attention is separate from autonomy and escalation

**122 tests passed** after separating explicit visibility preferences from the four autonomy levels. A matching Attention rule now adds the email to **Needs attention** without changing a silent action into **Notify**. If the same email would otherwise qualify for learned automatic archiving, it remains pending for review. At this initial slice, future similar/sender rules still used the model's situation kind; the later semantic-transfer iteration above replaces that behavior. Unit checks cover visibility without autonomy changes, archive review, sender boundaries, disabling and missing evidence.

The local browser now shows separate **Needs attention** and **Escalated** counters and filters. **Important** remains a separate organization marker. JavaScript syntax and the live local rendering on the existing database were checked; the database contained no queued Gmail operations before restart, and no Gmail mutation or send was performed. At this point the user attention preferences and contrast measurement were still outstanding; their later result is recorded above.

## September 10: explicit draft-style learning

**120 tests passed** after adding separate draft-style feedback and application. Tests cover deriving length/greeting/sign-off and a bounded before/after wording example from a real body edit, general and exact-sender scope, cross-sender transfer, rejecting unknown or unevidenced kinds both when saving and applying a rule, provider rewrite fallback, persistence of local revisions, and the unchanged requirement for exact send approval. Web regressions confirm that a locally simulated pending reply can be edited and used to save a style rule while the sent table remains empty, and that an unchanged reviewed body can become style feedback only through a separate explicit confirmation. The Groq adapter test checks the constrained body-only rewrite response, inclusion of the confirmed wording example, and confirms that internal action IDs are not sent to the provider.

No live Gmail mutation, delivery or real-model before/after measurement was performed for this iteration. The tests verify policy and transport boundaries with fixtures and mocks; they do not prove that Qwen preserves meaning during a rewrite. The next development measurement requires user edits on several synthetic drafts followed by new matching and contrasting messages. Automatic sending remains disabled.

After two explicit user edits, a one-case real-Qwen development check replayed a new support-resolution proposal through a copy of the user's active preference state. The baseline body was `Received, thank you for the update.`; the preference rewrite produced the user's latest phrasing, `yes, I received, thank you.\nWill try again`. The action remained pending, with zero sends and no Gmail mutation. Two preceding contrasts did not apply the rule: one was escalated because it requested a new human commitment, and one was classified as `service_success` rather than `support_response`. Full report: [draft-tone-transfer-2026-09-10.json](reports/draft-tone-transfer-2026-09-10.json).

This is a developer-written check performed after inspecting earlier classifications, with one positive transfer case. It demonstrates that the stored example reaches the constrained rewrite and that the situation boundary is enforced in these cases; it is not independent quality evidence. The resulting wording still needs user judgment, and a larger before/after edit-distance evaluation remains outstanding.

A second real-Qwen development check used the user's third draft edit for `job_interview`. On a new sender, the baseline `Hi, I confirm that I have received the interview invitation for September 21. Best regards.` became `Hi there. I received the invitation, thank you`, reducing 15 words to 8 while preserving pending approval. A contrast asking the user to choose between two times remained `none` / escalated and did not apply the style rule. Zero sends and zero Gmail mutations occurred. Full report: [interview-draft-style-transfer-2026-09-10.json](reports/interview-draft-style-transfer-2026-09-10.json).

The first attempt exposed the free-account output-token ceiling: triage requested 1600 maximum completion tokens while the provider enforced 1000 OTPM at that moment. The maximum was reduced to 800, which is sufficient for the bounded schema and short generated text; both model calls then completed. A regression asserts that the configured maximum does not exceed 1000. The two-case set is developer-written and observed after training, so it remains development evidence rather than independent evaluation.

## September 10: topic, subtype and explicit importance

**111 tests passed** after adding a local organization layer. Topic, subtype and importance are stored separately from Gmail labels, attention and action permissions. New checks cover one-email edits, general and exact-sender transfer, sender precedence, pausing, unknown or unevidenced kinds, input validation, receipt/interview contrast, and the fact that Important never removes archive approval. A consistent SQLite read transaction also closes a UI snapshot race exposed while the background worker committed a new email.

Browser verification confirmed the compact editor, list/detail chips, persistence and the Preferences section with no console-visible failure. The existing `web-groq.sqlite3` opened successfully with all **41 actions and 30 label reviews** intact; no old review was rewritten and no Gmail mutation occurred. The hierarchy is still an initial product hypothesis. Its usefulness and the model's kind classification must be checked on new contrast emails before reporting a transfer result.

The user then saved seven general organization rules. The separate [12-case contrast dataset](evaluation/organization-contrasts-v1.json) used new senders and paired meanings including interview/application receipt/outcome and work review/status. Qwen with `triage-v7` matched the expected situation kind **12/12**, with no provider errors. Replaying each exact model proposal through fresh and preference-enabled policies produced organization matches **8/12 before → 12/12 after**. Expected Important transfer improved **0/4 → 4/4**; false Important remained **0/8 → 0/8**. Full report: [organization-transfer-2026-09-10.json](reports/organization-transfer-2026-09-10.json).

This is a user-preference development check on a small dataset written after the feature design, not the final held-out evaluation. The same proposal is replayed before and after, so the improvement measures stored-rule application rather than a model-weight change. No email was imported, changed or sent in this run.

## September 10: label review and interface iteration

101 unit tests pass. Thirty realistic synthetic messages were inserted in the test Gmail (no sends), classified by Qwen with `labels-v1`, and reviewed by the actual user. All 30 reviews are verified. Recovery of initially uncertain reviews confirmed 22 by reading and applied eight saved replacements once following the user's request to finish. Original generic errors did not retain their exception type; their exact cause is unproven. No automatic retry of uncertain mutations was added. The aggregate report is [labels-review-2026-09-10.json](reports/labels-review-2026-09-10.json).

Browser checks confirmed the saved Gmail connection, 30 reviewed rows and no console errors. These are integration/UI checks, not a held-out accuracy score or a measured reduction in questions. The new general triage-v7 prompt has no independent model-quality measurement yet. New OAuth consent was tested with mocks, not a fresh live login. Priority/tree organization and skill import previews remain unimplemented.


## Gmail connection and manual sync UI (2026-09-09)

**83 tests passed**, 0 failures/errors, 4.927 seconds. New checks cover CSRF-protected asynchronous routes, no network activity on state reads, explicit Groq consent, account/mode/limit validation, missing labels and access, account changes between checks, single-flight OAuth and executor serialization, sanitized OAuth failures, partial imports, deduplication, sample-mode rejection and simulation-mode access.

Live browser check reused the existing Google authorization: profile, manage access and Wajo-Test verified. Sync returned **0 queued, 10 already imported, 0 manual review**, with more pages available. The database retained 11 completed jobs and zero Gmail operations: **zero model calls and zero sends** in this check. The consent checkbox initially disabled sync; progress, disabled controls and the final result were observed. The modal was visually checked and browser JavaScript errors were empty.

A new Google consent flow and new-message import were covered with mocks in this iteration, not a new live authorization or new live message. One-page manual sync, process-local connection/summary state and required local Desktop-client setup remain limitations. This is an integration check, not Qwen accuracy or independent final evaluation. See the sanitized [UI integration report](reports/gmail-connect-ui-2026-09-09.json).


## Gmail drafts and approved replies (2026-09-09)

**71 tests passed**, 0 failures/errors, 4.362 seconds. Reply checks cover draft-before-approval, recipient/subject/body revisions, stale/replayed approvals, header injection, blocked risky proposals, external draft edits, missing/forged approval, source scope removal, draft-creation timeout reconciliation, send timeout reconciliation after restart, Message-ID rewriting with exact marker/content matching, and no automatic resend when Sent search is inconclusive.

Live development run: one Gmail draft created, two updates, version 3 approved through the web UI, **one send operation**, Sent readback and self-delivery Inbox membership confirmed. Recipient, subject and body matched the approved payload. The recipient was only the connected test account. Initial draft verification failed because Gmail rewrote Message-ID; the existing draft was reconciled without duplication after fixing matching. The failure is retained in the aggregate [reply report](reports/gmail-replies-2026-09-09.json) and local audit.

Browser checks confirmed the saved version and approval button, editing the subject disabled approval until saved, and UI approval queued the real send. This used a fixed synthetic proposal and real Gmail transport, with **zero model calls**; it is not a live v6 quality evaluation or final independent evaluation. No account address, token or original message content is in the committed report.


## Reversible Gmail execution (2026-09-09)

**59 tests passed**, 0 failures/errors, 3.889 seconds. New checks cover durable queuing, explicit approval, deferred positive feedback, restoration, learned-permission revocation, current account/label/Trash checks, unknown outcome after a write, read-only reconciliation without replay, crash recovery, legacy transport isolation, unsupported live sends, and the background worker.

Live transport development check: **3/3 operations verified** on one user-created synthetic Wajo-Test message: AI label, archive, restore. The original Inbox membership was restored. The diagnostic label AI: Integration check remains. The shared core, durable queue and Gmail executor were used with fixed proposals and scripted explicit approval in isolated diagnostic databases. Zero model calls, zero sends, zero deletions. This does not measure Qwen quality or final task performance. Aggregate report: [gmail-writes-2026-09-09.json](reports/gmail-writes-2026-09-09.json). Original message content and account identifiers are excluded.

Browser verification: the main database displays Gmail live mode while existing decisions still show Local simulation; no JavaScript errors were observed. An isolated diagnostic UI displayed Gmail · real action and Restored to inbox; its screenshot was visually checked.


## Optional Gmail manage access (2026-09-09)

**50 tests passed**, 0 failures/errors, unittest duration 3.725 seconds. New mocked OAuth checks cover both access profiles, refusal to replace a token when the requested scope is missing, private token permissions, and preserving stored scopes while loading/refreshing credentials. The explicit `auth --access manage` profile requests only `gmail.modify`; default authorization remains read-only. After user consent, inspection confirmed gmail.modify in the saved token scopes. Live Gmail getProfile and labels.list requests succeeded, and Wajo-Test was found. No address, token or message content was logged. These read requests do not validate write execution. No Gmail write/send endpoint is implemented by this change.


## English application and Gmail import follow-up (2026-09-09)

**48 tests passed**, 0 failures/errors, Python 3.14, unittest duration 3.707 seconds. A separate subprocess check on an occupied port returned exit code 2 with an English explanation and no traceback. The existing bind-before-queue-initialization regression passed.

UI labels, validation errors and demo fixtures now use English. Browser checks confirmed the inbox counters and preference screen render in English; the preference screen was visually inspected. Prompt **triage-v6** requests English generated text and preserves verbatim evidence. No live v6 quality evaluation was run; previous v5 metrics do not apply to this prompt.

The user reported successful Gmail OAuth and a ten-message test import. Read-only aggregate inspection of the local database confirmed **10 Gmail jobs done**, no remaining Gmail jobs queued/processing/failed. There were 11 completed jobs total, including one earlier local message. This confirms recorded processing completion, not correct classification of those messages. No message content, credentials or tokens are included in this report. Gmail actions remain local simulations.


## Iteration 5 — live pattern checks, bounded retries and local web (2026-09-09)

**47 tests passed**, 0 failures/errors, Python 3.14.3, 3.215 seconds. Includes HTTP diagnostics/retry limits, CSRF/Origin/Host checks, exact action revisions, archive correction/exceptions, persistent queue handling and mocked Gmail read-only import/deduplication. Optional Google dependencies installed in `.venv`; exact environment versions saved in `requirements-gmail.lock.txt`. No live OAuth connection has been made.

Real Groq/Qwen development runs:

| Report | Prompt | Completed tests | Matched | Autoarchives / eligible | Incorrect autoarchives | Questions before → after | Reported tokens |
|---|---|---:|---:|---:|---:|---:|---:|
| `learning-v4-retry-2026-09-09.json` | v4 | 6/6 | 6 | 2/2 | 0/4 contrasts | 2 → 0 | 10,475 |
| `patterns-v4-2026-09-09.json` | v4 | 9/12 | 7 | 4/6 | 0/3 completed contrasts | 4 → 0 | 20,967 |
| `reference-v5-2026-09-09.json` | v5 | 4/4 | 4 | 2/2 | 0/2 contrasts | 2 → 0 | 8,282 |
| `patterns-v5-2026-09-09.json` | v5 | 12/12 | 12 | 6/6 | 0/6 contrasts | 6 → 0 | 24,963 |

The first run used three training acknowledgements from different subjects; the full pattern runs used nine training examples (three per digest/success/reference pattern). Training and testing use different senders. Each model interpretation was replayed against fresh and learned policies. All training approvals were **scripted evaluation-user feedback**, not actual user preferences. No real emails were delivered. Counts exclude the separate UI diagnostic email.

The v4 pattern run wrongly proposed labels for two eligible references despite correctly identifying their semantic pattern. It then stopped on one **HTTP 403**, exact body `{"error":{"message":"Forbidden"}}`; the unfinished cases were not counted as passes. V5 clarified archive eligibility before the generic label fallback. The targeted regression passed, then the entire 21-call pattern run completed without HTTP errors. Summed inference latency for that run: **12.640 seconds**, excluding deliberate inter-call pacing. All original partial reports are retained. These remain developer-written, prompt-tuned **development measurements**, not final held-out quality or proof of safe unattended mail handling.

Browser verification: loaded seven offline cases, approved one local archive and restored it; card counts changed 0 → 1 → 0. In Groq mode, submitted a synthetic support acknowledgement through the web form, observed asynchronous classification, approved its archive and verified the preference screen showed **1/3** general approvals. Screens were visually inspected at desktop and narrow viewport widths. The user-facing app is served locally at `127.0.0.1:8765`. Period summaries, Gmail writes, reminders and final evaluation remain incomplete.

## Iteration 4 — archive preference memory (2026-09-09)

**34 unit tests passed**, 0 failures/errors, Python 3.14, unittest duration 0.043 seconds. Includes all four initial semantic categories, cross-sender transfer, sender-only scope, explicit keep exceptions, rejection, correction/reset, persistence, no learning from silence or automatic execution, risk flags, replay prevention and unchanged send/unsupported-operation restrictions.

Offline evaluation: [policy report](reports/learning-policy-2026-09-09.json). Three scripted-user approvals on separate training emails; six subsequent checks. Questions **2 → 0**, eligible autoarchives **2/2**, inappropriate autoarchives **0/4** negative cases, expectation matches **6/6**. Classifications were supplied by fixtures, so these numbers measure the policy only. Test emails include job/support acknowledgements, an interview invitation, a rejection, injection and payment. The report includes full simulated state and approval evidence. No real mail was sent.

Live evaluation: [partial Groq report](reports/learning-v4-2026-09-09.json). **One request attempted, one HTTP 429, zero successful classifications, zero test cases completed.** No automatic retry or paid fallback. The new `triage-v4` prompt and schema are therefore **not live-validated**; v3 accuracy numbers below do not apply to v4. This iteration is not final evaluation or proof of safe unattended archive learning. The exact-quote check establishes only that evidence occurs in the body; semantic flags may still be wrong.

## Iteration 3 — triage priority correction (2026-09-09)

Instruction moved to `mail_agent/prompts/triage-v3.txt`. Outstanding financial requests and attempts to forge assistant authorization now take priority over routine sorting. Settled receipts and benign security quotations are distinguished explicitly. This is a prompt/classification correction, not a claim of a complete injection defense.

**20 unit tests passed.** Real Groq development checks: original seven scenarios **7/7 matched** across completed calls. Extended suite: **14 of 15 unique cases received successful responses, all 14 matched**; the remaining `normal-approval` case was not verified because of quota errors. That is 14/15 completion coverage, not a 100% end-to-end pass rate.

There were **21 case attempts: 14 successful responses and 7 HTTP 429 errors**, across separately saved partial runs (`groq-smoke-v3*-2026-09-09.json`). Reported successful-call usage total: **10,449 tokens**, excluding two successful diagnostic calls. No simulated messages were sent without approval; no real mailbox was connected. Calls stopped on quota errors and resumed explicitly at the interrupted index. All partial failures are retained. The last unresolved quota error was not retried indefinitely.

The grading helper was tightened during development to require execution status as well as action/autonomy; saved successful results were rechecked against that helper without new inference. The first partial processes used the earlier grader, with equivalent outcomes on these cases. The CLI now returns nonzero for incomplete/mismatched checks and supports `--extended`, `--start`, and `--delay`.

These are development regressions after editing a prompt based on observed failures; neither held-out quality nor adaptive learning has been measured. Remaining work includes the unverified benign approval example and a larger independent adversarial evaluation.

## Iteration 2 — Groq integration (2026-09-09)

Python 3.14.3: **17 unit tests passed** (12 core + 5 adapter tests). Adapter tests use mocked HTTP, not inference.

Real development smoke run with `qwen/qwen3.8-27b`, prompt `triage-v2`: **5/7** expected action/autonomy pairs matched, **0 provider errors in the completed run**, **0 sends without approval**, **2933 tokens** reported by Groq. Full synthetic inputs, outputs and per-call timings: [JSON report](reports/groq-smoke-2026-09-09.json).

Failures: an invoice received a work label instead of escalation; an injection claiming approval was escalated but not identified and blocked as an attack. No payment, deletion or delivery occurred. These seven development fixtures do not establish safety, general accuracy or learning performance.

Iteration trail: initial access check succeeded; first generation received HTTP 429 and stopped safely. A minimal diagnostic request without the application User-Agent received a non-JSON HTTP 403. A subsequent minimal request using the adapter headers succeeded. An initial `triage-v1` meeting example incorrectly escalated; the prompt was clarified to distinguish notification from human judgment before the recorded v2 run. The completed report's token count excludes those diagnostic/development requests. Paid billing was not enabled by this application.

Measured locally on 2026-09-08 with Python 3.14.3.

Command: `python3 -m unittest discover -s tests -v`

Result: **12 tests passed, 0 failures, 0 errors** (0.017 seconds reported by unittest).

The seven scripted demo events produced:

| Autonomy | Events |
|---|---:|
| Silent | 2 |
| Notify | 2 |
| Ask | 2 |
| Escalate | 1 |

Initial effects: two labels and one draft created, zero messages sent, zero emails archived. The CLI approval flow was also run against a persistent database: approving the pending send changed its status to `executed` in the local simulation.

These are execution/policy checks, **not measured AI accuracy, learning performance, or prompt-injection detection results**. The proposer is scripted, and no real messages were delivered. Full evaluation remains to be implemented.

Environment issue found during verification: one shell resolved `python3` to Python 3.6. The successful checks explicitly used the installed Python 3.14 interpreter. The application now exits with a clear Python 3.11+ requirement when invoked with an older interpreter.

September 10 follow-up: 104 tests pass after adding explicit Replace/Add review modes. New tests cover preservation of original/unrelated labels, replacement after addition, rejected unsupported future-add scope, and read-only reconciliation after an uncertain addition. Gmail transport uses mocks for these new cases; no live mailbox mutation was performed. Browser mode-switch check passed with no console errors.
