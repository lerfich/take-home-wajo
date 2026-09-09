# Iteration 1 verification

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
