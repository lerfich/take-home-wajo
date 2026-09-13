# Verification and limits

## Measured evaluation

The [evaluation report](reports/evaluation/REPORT.md) contains the saved model responses, frozen expectations, component scores, and limitations. The measured set contains 111/111 usable synthetic model responses. The final policy level matched its expectation in 50/72 decision cases; unsafe autonomous primary actions were 0/72, authored instruction-injection cases blocked were 6/6, and Organization/Attention preference-transfer controls were 24/24. These numbers do not measure Gmail delivery, UI usability, or general performance on private mail.

## Application checks

The Python suite passes 281 tests, including policy boundaries, approval revisions, Gmail reconciliation, learning, OAuth connection states, and mocked transport. The browser review-guard script also passes. A clean local Python launch and a clean Docker Compose launch both opened the UI with bundled Groq; a model-catalog request from Docker returned the configured Qwen model. The owner reported that the fresh Docker browser check worked; exact Gmail OAuth steps and synchronization scope were not recorded. These checks are functional evidence, not additional model-quality measurements.

## Submission packaging check — September 13, 2026

A fresh export containing only the submission files installed its pinned dependencies with `./run.sh` and served both `/` and `/api/state` successfully with an empty mailbox and bundled Groq selected. The same export built and started with Docker Compose; both endpoints returned HTTP 200. No Gmail credentials or tokens were supplied. The isolated servers were stopped after verification.

The Python suite passed all 281 tests, and `node tests/test_review_ui.js` passed. Running `./.venv/bin/python -m mail_agent.g1_continue report` in the export rebuilt the saved evaluation offline: 111 usable responses, zero unresolved errors, and unchanged headline scores. Relative Markdown file links resolved within the submission directory. Historical credential-pattern scanning found only the intentionally published bundled evaluation key; no tracked mailbox databases or OAuth credential/token files were found.

These checks confirm clean installation and application startup. They do not establish a new live Gmail OAuth, synchronization, delivery, or model-inference result.

## Known limits

- The bundled Groq account is free and quota-limited. About 65 emails per day is an operational estimate, not a guaranteed allowance.
- The OpenAI adapter has contract and mock coverage but has not been checked with a live OpenAI key.
- Docker does not provide macOS Notification Center banners. Use the local Python launch for those while the server is running.
- An uncertain Gmail write is held for read-only reconciliation and is never blindly retried.
- The saved evaluation includes exact non-English synthetic input and model output; those raw records remain unchanged to preserve the evidence.
