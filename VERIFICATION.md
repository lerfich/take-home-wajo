# Iteration 1 verification

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
