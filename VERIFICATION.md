# Iteration 1 verification

## Optional Gmail manage access (2026-09-09)

**50 tests passed**, 0 failures/errors, unittest duration 3.725 seconds. New mocked OAuth checks cover both access profiles, refusal to replace a token when the requested scope is missing, private token permissions, and preserving stored scopes while loading/refreshing credentials. The explicit `auth --access manage` profile requests only `gmail.modify`; default authorization remains read-only. Expanded live consent has not yet been performed or verified. No Gmail write/send endpoint is implemented by this change.


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
