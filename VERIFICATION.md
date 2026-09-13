# September 13: Stage E UI/UX review implementation

Four-stream follow-up: the model schema and triage-v9 prompt now return a separate Label suggestion alongside Archive/Keep, Reply/Action and Event. New unread Gmail mail receives a durable, versioned label decision; the suggested `AI:` label is not written until the user confirms it. Changing or skipping this label does not approve a reply, archive decision or calendar event. Confirmed Gmail label writes are verified; an uncertain result is reconciled read-only rather than repeated. Verified choices can create a suggested Label Skill, but one confirmation does not silently activate a new rule. Older saved proposals and label-review fixtures remain on their legacy path. The Label section uses the same required-review badge and blue 10/4 orbit; the badge is also generated for ambiguous dates, final event confirmation and Escalation. The Whitelist input itself now explains that it only blocks automatic archiving.

The signature check already recognized `Best,` followed by `Nikita`. Its sign-off recognition now covers additional common English and Russian closings, including one-line forms, with tests that reject placeholder/prose as a name. This remains a bounded heuristic rather than universal identity extraction. A new four-stream synthetic regression verifies simultaneous reply draft, Archive/Keep, Label and Event state without a send; additional tests cover skip, confirmed Gmail label readback, no silent Label Skill activation, old read mail exclusion, read-only unknown-result reconciliation, and preservation of a saved Label decision when the primary action is reanalysed. **270 Python tests passed**, JavaScript UI guards and syntax, Python compilation and git diff checks passed. No live Gmail model call, write, send or user pilot was performed for this iteration; independent evaluation and triage-v9 freeze remain pending.

Archive-learning follow-up: inbox placement is now independent from the primary mail action. For every newly analysed unread message, triage-v9 returns Archive or Keep with exact body evidence; either recommendation requires a current user decision until a matching Archive Skill exists. Only three consecutive agreements on semantically matching, real Gmail-bound and currently safe messages automatically create and activate the Skill. Training state is not listed; active and paused Skills are visible with Pause/Resume/Delete. Deleting a Skill removes its matching qualification history. A correction is available for both an automatic archive and an automatic keep: it removes the active Skill, applies the opposite Gmail state with readback, and restarts qualification from zero. Risk, deadline, Attention and whitelist conditions block automatic archiving and are rechecked immediately before the Gmail write. Unknown external outcomes remain read-only reconciliation only.

Escalations now offer `I’ll handle this` and `Keep in Needs attention`. These only record how the user will track the situation; they do not execute or approve the blocked email request. The final confirmation after clarifying an ambiguous date uses the shared decisive-button treatment. The required-section orbit uses a 10/4 dark-blue dash; decisive buttons use the shared pale-green pulse and 19/4 orbit. An isolated scripted browser check confirmed the new Escalation controls and review cue. No Gmail mutation was performed in that check.

Regression after this follow-up: **265 Python tests passed** in 20.862 seconds. The JavaScript UI guard scripts, JavaScript syntax, Python compilation and git diff checks passed. Seven focused Archive Skill tests cover real-only qualification, unsafe/local exclusion, automatic keep and archive, Gmail execution/readback, correction and retraining, Pause, Delete reset, and whitespace-only evidence normalization. A migrated copy of the working database passed SQLite integrity; action #42 remains pending revision 1 with no sent row. The source database was not changed by the copy check. It currently contains 51 emails/actions; this count is state documentation, not a pilot result. triage-v9 remains unfrozen and this remains development verification, not final independent evaluation.

Bundled Groq credential follow-up: by explicit owner request, one revocable free-tier evaluation key is now a hardcoded default in `groq_provider.py` and intentionally committed for reviewer use. User-provided Groq/OpenAI keys still stay in the local owner-only credential store; `.env` may override the bundled default but is not required. A real read-only Groq model-catalog request using the hardcoded fallback with no `.env` confirmed the configured bundled model is available. The key value is not reproduced in this report. The owner plans to revoke it after review; this catalog check is not an email-quality evaluation.

Implemented the two user review batches: shared Gmail/account header, sidebar icons and avatar initial, precise Models slider, explicit manually-unvalidated OpenAI caveat, Escape/backdrop dialogs, month/year picker and Current month animation, Superpowers colors, Preferences category grid and glossary, compact single-message review with received/reply columns, whole-word body preview, horizontal preference sections, and exact edited-reply approval flow. Scope selectors now use similar-context preferences while existing narrow rules retain their scope.

256 Python tests passed before the final wording-only backend cleanup. JavaScript syntax and diff checks passed. `node tests/test_review_ui.js` covers whole-word previews, exact reply fields/revision, edit-to-approval sequencing, and stopping after Gmail errors, altered text or account changes. Edited replies are saved and verified before the same explicit click authorizes their exact new revision; verification timeout leaves them unsent. These checks use mocks, not a new live send.

Browser QA used an isolated scripted database: Preferences grid, glossary, Escape/backdrop dismissal, Calendar picker, Models slider endpoints, two-column reply preview and disabled From/To editor fields were inspected. The working server was restarted once in live mode after checking the queue. New-mail polling is always enabled for configured connections at a 10-second interval, subject to error backoff; initial-history pause is independent. The previously user-approved archive operation #21/action #43 completed after restart. Action #42 remained pending revision 1; 48 emails and two saved events remained. The current persisted Superpowers setting was enabled and was preserved, not changed by this work. No new send approval was performed during QA. Working-page browser error/warning logs were empty.

Action #10 remains an old label-verification error after an interrupted write. Retry reconciles uncertain Gmail writes read-only; it does not blindly replay them. CSS visible in older cached messages predates the existing HTML-parser fix; stored historical content was not rewritten. OpenAI remains mock/contract tested without a real key. This is development verification; Stage E awaits Nikita's next UI review and final independent evaluation remains pending.

Follow-up UI correction: an isolated scripted browser run confirmed that Archive, Label and Visibility can remain expanded together after a forced refresh, and that an unapplied OpenAI selection returns to the saved bundled plan on re-entry. Required-review panels have a dark-blue 5 px / 2 px animated dashed outline (independent clockwise/counterclockwise direction, reduced-motion fallback); pending event decisions use the same cue. A second visual check confirmed that the outline remains visible when a required section is collapsed. The in-progress email selection is preserved when its analysis finishes. The legacy persisted new-mail-off flag is explicitly ignored by polling; its prior public representation already forced `true`, but the poller no longer relies on that representation. The full regression suite passed **256 tests**; JavaScript guard and syntax checks passed. No Gmail action was approved in this QA.

During a brief live startup, the saved Gmail connection pointed to a different account from the earlier Wajo-Test context. Nikita subsequently clarified that **he intentionally switched the account himself**; this was not a connection error. The server was stopped; automatic polling had imported two more incoming messages, leaving **50 local emails**. No send or deletion was performed. Read-only checks still show action #42 pending at revision 1 with no sent row, and action #43 executed from its prior approval. The mailbox and OAuth state were not changed by this QA, and the two imported messages remain in the local database. Private-mail pilot scope/mode and model-transfer consent remain separate decisions; subsequent UI verification used only a separate synthetic demo database.

# Stage C closure — September 12, 2026

Pre-commit rerun exposed environmental coupling in the web tests: switching a test server out of demo mode could pick up the workspace's real OAuth token and skip synthetic input during Gmail scope checking. Test servers now use isolated temporary OAuth paths. The background test also waits for terminal job status rather than action creation and requires `done` before checking effects. No real send was triggered by this test.

Stage C is functionally complete under the bounded live scenarios below, complemented by 189 passing development tests. This is not an independent evaluation or a prolonged private-mail pilot. Earlier chronological entries below describe intermediate states and are superseded by this closure summary, not erased.

- Initial private-mail import: 30/30 analyzed after recovery from Groq 403; two pages, restart and Pause/Resume checked. Archive/restore confirmed by Gmail. One earlier unconfirmed label operation remains error and was not replayed.
- Synthetic B and D: two exact manually approved replies confirmed by Gmail, qualifying Draft Skill #24 revision 2.
- E / action #36: send operation #18 done with automatic=1; one Autosent entry with matching sent ID. The next poll did not create another send operation. Gmail confirmation is not proof of recipient inbox placement. Elapsed from Gmail message timestamp to confirmation was 83 seconds: detection dominated; Groq took about 2 seconds. Poll interval remains one minute.
- F / action #37: after skill permission revocation, style applied but only a draft was saved; no send queued. Qualification reset to 0/2 while the global switch stayed on.
- Same-account reconnect preserved 37 emails/actions, active skill, sync state and revoked qualification. Keep previous data on the other account preserved prior records and disabled current-account Superpowers.
- G / action #42: after explicit user authorization to change the test skill to All senders while retaining the literal test phrase, revision 3 applied across accounts. Gmail draft saved, action pending, sent ID empty, qualification 0/2 and global Superpowers off. The previous account's signature was intentionally not supplied; output ends with `Best,` without a name. A signature for the new account remains a separate user preference.

Safety-block combinations, unknown-outcome recovery, and broad scope variations are covered by synthetic/mocked tests, not all exercised live. The test is narrow and does not establish general semantic reliability. Test drafts remain in Gmail; no cleanup or extra sends were performed. Remaining UX/operational limitations include minute polling, one stale label error and an SSL ResourceWarning during tests. D and final evaluation remain future work.

# Iteration history

## Live account switch with Keep previous data

Switching from the private pilot account to the user's synthetic-test account reached the explicit account-choice state. Keep previous data preserved 31 emails, 31 actions and both suggested Archive Skills. The new account connected with manage access and new-only synchronization; historical test mail was not imported. This confirms preservation, not application of an active Skill across accounts: both preserved Skills are still suggested, and there is no active Draft Skill yet.

## New incoming message: automatic polling verified

The user sent the synthetic “Wajo C incoming check” message from another account. With no manual Sync request, the running server polled Gmail at 10:36:17 UTC, queued the message at 10:36:17.660 and completed analysis at 10:36:24.253 on September 12. The saved action is none/executed. The original 30 jobs remain complete. The full current test suite passes 185 tests (a ResourceWarning about an unclosed SSL socket was emitted; no test failed). This supersedes the pending incoming-message check below.

## Stage C resumed live validation

All 30 imported jobs now have status done, including the 23 manually resumed analyses. The previous restore was reconciled read-only first (not in Inbox), then explicitly resumed under the user's restore-validation request; operation 4 is done with Gmail readback. Six label operations are done; the earlier unconfirmed label remains error and was not replayed.

Pause/Resume was exercised through the local HTTP API and confirmed in SQLite. After stopping the sole executor and restarting through run.sh, 30 unique bindings and 30 completed jobs remained intact, getProfile succeeded, and integrity_check returned ok. No send was performed.

A separate ignored diagnostic database exercised a fixed synthetic draft proposal against one already authorized Gmail binding. One Gmail draft addressed to the connected account was created and updated in place to revision 2, with exact readback and pending status. Its subject is “Wajo Stage C draft verification — do not send”; it remains in Gmail. This validates transport, not model draft quality. No send operation was queued. A fresh incoming-mail polling scenario is still awaiting a user-created test message; Stage C is not yet closed.

184 unit tests pass. New regressions cover HTML style/script/head exclusion and bounded Gmail refresh; Gmail requests now use a 25-second timeout with no implicit replay after 401. Existing cached mail was not rewritten. The UI has one timeline ordered by Gmail date and separates processing errors from escalation counts.

## Connectivity recheck and three-worker limit

The analysis pool now has three workers; Groq requests remain serialized by the shared gate. All 17 synchronization tests pass, including the updated concurrency bound. Groq model listing and one synthetic triage request succeeded, and the saved Gmail connection passed getProfile. No private-mail analysis was resumed and no Gmail mutation was performed during this connectivity check. Full Stage C revalidation awaits the user's go-ahead. Separately, the earlier restore attempt (operation 4) ended unknown and its subsequent read-only check still found no INBOX membership; reconcile before any further write.

## September 12: reconnect, portable Skills and Superpowers — synthetic/mocked only

**182 tests passed** in the current full unit run (`python -m unittest discover -s tests -v`). New regressions cover a different-account reconnect that blocks reads until the user explicitly keeps previous local data or starts fresh; exact atomic local reset counts with no Gmail calls; legacy-account detection and queued-write resume; at most ten initial-history label IDs; preservation of the original history-label filter when retrying incomplete fetches; attachment metadata; ordinary reviewed Skill matching across saved accounts while exact-sender scope remains exact; deterministic Superpowers blocks for prompt injection and financial requests even when model flags are false; revoked applications; crash recovery; unknown automatic-send outcomes without retry; a bare Groq 403 without automatic retry; and a four-message maximum analysis pool.

Superpowers tests cover the default-off, current-account global toggle; qualification of an exact active Draft Skill revision only after two verified manual Gmail sends with unchanged recipient, subject and text; account isolation and revocation after draft or Skill changes; verified-draft queuing; immediate pre-send revalidation; hard blocks for attachments, different recipients, risk/sensitive/human-judgment conditions and stale revisions; Autosent journaling; and unknown outcomes without requeue. The Gmail reply integration test exercises the complete policy/queue/readback sequence against mocks and synthetic data.

These results are **only mocked/synthetic development verification**. No reconnect flow, ten-label selection, cross-account Skill transfer or Superpowers reply has been exercised end to end against a live mailbox in this iteration. In particular, no automatic reply has been sent live. The earlier explicitly approved synthetic self-send reports below validate the older manual path only. A private-mail pilot and the final independent evaluation have not been performed.

Auto-send remains disabled by default. It requires all three positive grants together: a qualified exact Draft Skill revision, **two unchanged successful manual sends through Wajo on the same Gmail account**, and the user-enabled global Superpowers toggle for that currently verified account. Safety and identity checks can still force review, and an unknown Gmail outcome is never retried automatically.

## September 11: stage C implementation completed on mocks and synthetic data

An earlier 164-test milestone established the resumable sync baseline. The web Gmail path supports last 30, last 100, all existing mail or only future mail; optional Gmail labels constrain only initial history. Page cursors, progress, Pause/Resume, automatic-sync state and Gmail History cursor persist in SQLite. Repeating a selection and restarting continue from the saved page, while exact account/message IDs prevent duplicate actions. Polling rechecks the account, catches up after being disabled, backs off after read failures without advancing the cursor, and retries incomplete messages no more than once per hour.

Message loading and analysis are separate. Interrupted or failed model work remains incomplete and is retried with persisted exponential delay; up to four analyses run concurrently. Gmail `UNREAD` is rechecked immediately before analysis without marking a message read. Already-read mail keeps organization and safe additional-label handling while Needs attention, archive, draft, escalation and their risk flags are suppressed. Tests cover provider failure on read mail without false escalation, and retain retry status rather than claiming completion.

Sent and Gmail Draft messages are stored as conversation context and do not become agent jobs; Spam and Trash are excluded even when they have no readable body. A message moved into a special folder before analysis is dropped from the incoming queue without a model call. Inline HTML is reduced to plain text and attachments are not downloaded. Gmail thread IDs group multiple incoming actions into one conversation card. A saved `X-Wajo-Reply-Key` distinguishes a Wajo draft from a user draft; the latter is never analyzed, edited, treated as feedback or sent. The browser showed four history choices, initial-history label section, explicit Groq consent, disabled pre-connection start, 2 messages grouped into 1 conversation, a locked incomplete `!` card and no read-state caption on local mail. Browser warning/error logs were empty.

The full suite, JavaScript syntax, Python compilation and diff whitespace checks pass. A temporary copy of `data/web-groq.sqlite3` retained **46 actions and 32 Gmail bindings**, added empty synchronization state, and returned SQLite `integrity_check = ok`; the working database was not modified. Its 46 incoming jobs and 63 Gmail operations were already done, and port 8765 was free. Two isolated local UI servers use separate ignored databases on ports 8766 and 8767. No Gmail request, model call, mail mutation, private-mail import or send was performed in this verification.

Stage C is not yet marked fully closed: the new paginated/polling path and broadened exact-message write scope still require an explicitly authorized live test-mailbox run. Before that run the user must choose the account, one history mode, optional history labels and allow the selected synthetic message text to be sent to Groq. This is development verification, not a private-mail pilot or final independent evaluation.

## September 12: authorized private-mail Stage C attempt — partial

The user selected a private Gmail account, **Start fresh**, **Last 30**, no history-label filter, reversible Gmail actions, and consented to provide the selected sender, subject and body to Groq. The local reset removed **917 local Wajo records only**; Gmail was not changed. The live sync scanned and imported **30/30** messages over **two Gmail list pages**, with zero incomplete fetches. SQLite `integrity_check` passed; the resulting data had 30 distinct Gmail message IDs, 29 threads and one current Gmail account. No message content, addresses, tokens or model prompts are retained in this report.

One label operation and one archive operation completed and were confirmed with Gmail readback. A second label operation had been interrupted while processing; it was checked through the read-only reconciliation endpoint only. Gmail did not confirm the requested state, so it remains an error and was not retried. The archive restore was not completed because the local server stopped before that separate action; it must be reviewed explicitly rather than inferred from this run.

Model analysis was partial: **7 completed** and **23 stopped in error**. After temporary Groq rate-limit responses, the provider began returning a bare HTTP 403 both for analysis and for the read-only model-list endpoint, without retry metadata. The scheduler was changed so this condition does not loop indefinitely; the 23 jobs were left non-retrying pending a successful provider-access check and an explicit manual resume. No live draft, reply or auto-send was created. This is a private-mail pilot attempt, not final evaluation, and Stage C remains open.

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
# September 12: live Draft Skill preparation

Keep previous data after switching to the private pilot account preserved all 37 previous actions and active Draft Skill #24 revision 2. Automatic polling added two new actions, bringing the total to 39. Current-account Superpowers is disabled with no rules. No send was queued for previous pending draft actions #32/#34/#37. Positive live cross-account skill application is still untested: the retained skill's exact sender is now the mailbox owner, so a message from the test account is outside its scope. Scope must not be silently broadened to manufacture a passing test.

Same-account OAuth reconnect completed live: connection returned to the same test account without account-choice; all 37 emails/actions, active Skill #24 revision 2, New only sync settings, pending draft #37 and the single Autosent record remain. Global Superpowers stayed on, but revoked qualification stayed 0/2. No permission was restored by reconnecting.

Live revocation check F: after the user disabled the skill's auto-send permission, global Superpowers remained on but qualification reset to 0/2. Action #37 applied the same Draft Skill and saved Gmail draft operation #19 (done), then remained pending with an empty sent ID and no send operation. This verifies live revocation without removing the ordinary drafting preference. Before same-account OAuth reconnect, the workspace contains 37 emails and 37 actions.

Synthetic message D / action #35: the fixed rewrite applied Skill #24 revision 2 and produced the correct signature. Nikita approved unchanged reply revision 1; send operation #16 is done, approved=1, automatic=0, with Gmail-confirmed sent ID. Qualification reached 2/2 for the same skill revision. Superpowers remains off; the settings dialog was opened for explicit user activation. No live automatic-send result is claimed yet.

Identity fix follow-up: account-matched managed Draft Skills supply a conventional signature extracted from the saved edited example as separate reply-author identity context; cross-account style reuse does not supply that signature. Incoming author and reply recipient are distinct fields. Payload and account-isolation regression tests pass; full suite: 189 tests, with an existing SSL ResourceWarning. Restarted the server with the fix and placeholder guard. A standalone live Groq rewrite of synthetic C content returned `Best, / Nikita` without a placeholder, with no Gmail mutation. This is a focused generation check, not a second qualification send; action #34 retains its original pending draft.

Live follow-up C exposed an actual generation defect: action #34 saved a pending revision-1 Gmail draft containing `[User Name]`, despite application of Skill #24. No send or second qualification confirmation occurred. Added a rewrite instruction against placeholders and a conservative known-name-placeholder guard shared by automatic authorization and pre-send validation; 12 Superpowers tests pass. Code changes have not yet been loaded by the running server. This prevents treating the live Superpowers test as passed; generation retry and server restart remain required.

Live follow-up B: action #33 used Draft Skill #24 revision 2 (`draft_style_applications.status=applied`). Following Nikita's personal UI approval of unchanged reply revision 1, Gmail send operation #13 completed with `done`, `approved=1`, `automatic=0`; action status is `executed` and reason is `Gmail confirmed the sent message`, with a saved sent ID. Qualification advanced to 1/2 for the same skill revision. Superpowers remains disabled and its Autosent journal is empty. This confirms provider-side send, not recipient-side inbox placement.

Preparation action #32 remains a pending Gmail draft at revision 2; no send was performed. The UI review activated Draft Skill #24 after checking one matching synthetic receipt-confirmation example and two non-matching sender examples. Its scope is the source sender, work progress updates, and the literal body phrase `sample project update`. Style: concise, greeting included, sign-off included. This is a narrow transport/workflow check, not an independent evaluation or evidence of broad semantic matching quality.

## September 12: Stage D implementation verification

The agreed Events, Event Skills, local Calendar, macOS notification scheduler and Models scope is implemented. Python 3.14 ran **242 tests successfully** with no failures or errors. New coverage includes UTC/timezone and all-day handling, clarification revisions, later same-thread reschedule/cancel, independent mail/event decisions, positive-only 2-check Event Skill qualification, cross-account portability, exact-revision mistake revocation, restart-safe reminders and urgent notification deduplication, notification source/policy rechecks, all three model modes, local `0600` credentials, key redaction, stale validation rejection, dynamic concurrency settings, label-review isolation from Events, and the OpenAI Responses API contract with strict structured output, `reasoning.effort=none` and `store:false`.

An isolated synthetic browser database was used for visual QA. The September 2026 calendar rendered the local 2:30 PM event card, details popup and source-email transition correctly. Models rendered the connected yellow/green/purple selector, fixed disabled bundled slider, enabled user slider, OpenAI paid warning and disabled Apply before validation. JavaScript syntax, unique HTML IDs and `git diff --check` passed. The temporary server, browser tab and database were removed afterward.

Three bundled Groq development requests used synthetic text only. The clear Moscow-time meeting returned a valid `calendar_event` at `2026-09-18T14:30:00+03:00`. The first vague “sometime next week” attempt produced an internally inconsistent none/ambiguous combination and was rejected by validation; the prompt was clarified without relaxing the guard. The repeat returned a valid ambiguous event requiring clarification. These are prompt-development checks, not independent evaluation. OpenAI has no real key and remains mock/contract tested only. Notification delivery is tested with controlled clocks and notifier mocks; actual macOS banner duration and permission behavior are not yet claimed as live-verified.

A read-only check of the working database after these tests still showed 42 emails, 42 actions, action #42 pending at revision 1, zero sent rows for action #42, and no Stage D tables yet because the updated server has not been started on that database. The additive migration was then exercised on a temporary copy: it retained all 42 emails/actions and pending action #42, added empty Events/Event Skills state and selected bundled Groq without creating a send. No working data was reset, no Gmail write was made, and no private mail or secret was added to Git.

After commit `af755db`, the updated server was started once on the working database with `--local-simulation`. The additive migration retained 42 emails, 42 actions and 42 completed jobs; there were no queued, processing or error jobs. Action #42 remained pending at revision 1 with zero sent rows, Superpowers remained off, active model was bundled Groq with concurrency 3, and Events/Event Skills were initially empty. The startup notification job reached `sent`, confirming that macOS accepted the banner request. The saved Gmail token was present but its startup verification returned the existing safe generic connection error; the server remains local-simulation and no reconnect, Gmail write or new mail import was attempted.

The earlier preparation exposed a sign-off extraction defect: `Best,` followed by an author name was treated as no sign-off. Recognition and regression tests cover named English/Russian closings and a negative body-text case. Existing suggested configuration was explicitly refined through UI; saved skills were not silently migrated. The subsequent completed Stage C sequence is recorded in the closure summary at the top of this file.

### Stage D post-implementation audit

A separate read-only review of Events, Event Skills, Calendar UI, Models and notifications found defects that the first 242-test pass did not expose. The fixes and new regressions bring the full suite to **250 passing tests**.

- Already-read Gmail messages now stay organization/label-only and cannot create or automatically save events. Original suspicious/human-review provenance is stored on event proposals, survives date clarification, blocks Event Skill matching, and prevents unsafe examples from training a Skill even when a user manually adds the clarified event.
- Nonexistent and repeated local times at DST transitions now require clarification; an explicit timestamp offset inconsistent with its named IANA timezone is also rejected as ambiguous. Multiple current events in one thread are never superseded by guessing: title/semantic identity must select one unique existing event.
- Cancel and reschedule proposals now use truthful action labels. Combined mail/event pending state says `Awaiting decisions`; all-day end dates are rendered as local dates; Event Skill cards show their stored meaning. Model validation supports overlapping checks, expires visibly, and clears stale UI after Apply failure or success.
- Startup notifications replace an older undelivered startup job. Delivery is claimed durably before the OS call, so a crash with an unknown external outcome is not replayed. The application supplies an explicit notification allowlist, schedules urgent notification after deadline clarification, restores missing timed-event reminders on restart, and allows the macOS notifier timeout to finish during shutdown.
- Integration regressions now prove that a user-provider setting actually permits seven simultaneous analyses rather than merely persisting the number, that an Application restart restores OpenAI mode, local key presence and concurrency together, and that the HTTP workflow reaches two Event Skill confirmations, a third automatic local save, then removal and qualification reset through “This shouldn’t have been added”.
- A migration on a temporary SQLite backup of the current working database retained 42 emails/actions, action #42 pending at revision 1, and zero sent IDs for #42 while adding the new safety column. The temporary copies were deleted immediately because they contained private local data. The only server was then restarted on the working database in `--local-simulation`; all 42 jobs remain done, Superpowers is off, Events/Event Skills are empty, bundled Groq concurrency is 3, SQLite integrity is `ok`, and #42 remains pending and unsent.

The saved Gmail token was diagnosed without a write or reconnect. Refresh returns OAuth error code `disabled_client`; no token, client ID, email content or secret was printed or committed. Re-enabling the existing Google OAuth client/project or replacing the local Desktop client credentials is required before the limited live Event scenario can proceed; reconnect alone is unlikely to repair a disabled client. This is an external setup blocker, not a failed event-policy test. OpenAI remains contract/mocked because no user key was supplied. The work is still development verification, not final independent evaluation.

### Stage D limited live closure — September 12, 2026

Google reinstated the project; token refresh and Gmail getProfile succeeded. Four user-sent synthetic meeting emails were processed with real Groq inference and Gmail polling while the server remained in local-simulation for mail execution. Two personal event confirmations produced a qualified Event Skill (2/2); the third event was automatically saved. The user selected “This shouldn’t have been added”: that event was cancelled, the first two remained current, and qualification reset to 0/2. The fourth proposal remained awaiting_confirmation, automatic=0, with no calendar event created. No Gmail sends were performed in this D exercise; action #42 remained pending at revision 1 and unsent.

The first attempt exposed a MIME line-wrap mismatch between the body and model evidence. Whitespace-only normalization now allows the quote while changed wording remains rejected. The original saved model proposal was recovered locally with the server stopped, rather than counting its initial failure as success. A second message incorrectly set needs_human solely because a calendar confirmation was required. The prompt now explicitly separates ordinary calendar confirmation from substantive human judgment. That synthetic message was reanalysed after removing its unapproved failed proposal, with the server stopped for the repair; the subsequent live result correctly asked for confirmation. These are development fixes on observed examples, not independent evaluation.

Full regression: **252 tests passed** in 18.853 seconds; JavaScript syntax, Python compilation and git diff checks passed. The user observed the startup macOS banner and reported a short display duration. Copy now says “Active model: Groq · bundled free plan”. Exact banner duration remains controlled by macOS. Timed reminders and urgent notification scheduling remain controlled-clock/mocked checks, not a live elapsed-time reminder test. OpenAI remains contract/mocked without a supplied key.

Account-wide polling also processed two new service emails outside the synthetic four. The agent paused polling and disclosed this; the user accepted those incoming messages and explicitly requested retaining them. Local state contains 48 emails; no mail contents or credentials are included here or in Git. Polling remains paused at closure. Stage D is functionally closed with the above verification limits; private-use pilot and final independent evaluation remain later stages in PLAN.md.
