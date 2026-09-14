# Verification and limits

## Measured evaluation

The [evaluation report](reports/evaluation/REPORT.md) contains the saved model responses, frozen expectations, component scores, and limitations. The measured set contains 111/111 usable synthetic model responses. The final policy level matched its expectation in 50/72 decision cases; unsafe autonomous primary actions were 0/72, authored instruction-injection cases blocked were 6/6, and Organization/Attention preference-transfer controls were 24/24. These numbers do not measure Gmail delivery, UI usability, or general performance on private mail.

## Application checks

The Python suite passes 285 tests, including policy boundaries, approval revisions, Gmail reconciliation, learning, OAuth connection states, and mocked transport. The browser review-guard script also passes. A clean local Python launch and a clean Docker Compose launch both opened the UI with bundled Groq; a model-catalog request from Docker returned the configured Qwen model. The owner reported that the fresh Docker browser check worked; exact Gmail OAuth steps and synchronization scope were not recorded. These checks are functional evidence, not additional model-quality measurements.

## Submission packaging check — September 13, 2026

A fresh export containing only the submission files installed its pinned dependencies with `./run.sh` and served both `/` and `/api/state` successfully with an empty mailbox and bundled Groq selected. The same export built and started with Docker Compose; both endpoints returned HTTP 200. No Gmail credentials or tokens were supplied. The isolated servers were stopped after verification.

The Python suite passed all 281 tests, and `node tests/test_review_ui.js` passed. Running `./.venv/bin/python -m mail_agent.g1_continue report` in the export rebuilt the saved evaluation offline: 111 usable responses, zero unresolved errors, and unchanged headline scores. Relative Markdown file links resolved within the submission directory. Historical credential-pattern scanning found only the intentionally published bundled evaluation key; no tracked mailbox databases or OAuth credential/token files were found.

These checks confirm clean installation and application startup. They do not establish a new live Gmail OAuth, synchronization, delivery, or model-inference result.

## Bundled Google sign-in correction — September 13, 2026

The initial packaging check above covered startup only and missed an absent Google client in a fresh copy. With the owner's explicit permission, Mailward now ships its existing Desktop OAuth client in `mail_agent/gmail-credentials.json`; Docker copies it with the application. A local `data/gmail-credentials.json` remains an optional override. An explicitly configured missing path fails without silently choosing another client.

A fresh export, without a custom client or saved user token, installed with `./run.sh` and reached `Gmail connected` with manage access using the bundled client. No emails, analysis jobs or Gmail actions were created, and synchronization was not started. A separate clean Docker data directory exposed an enabled Google connection, generated the expected Google authorization URL with PKCE and `gmail.modify`, and received a simulated denial through its published loopback callback on port 8766. No Docker user token was created. This verifies the container callback wiring, not a completed live Google consent flow in Docker; that check was completed in the follow-up below.

All 285 Python tests and the browser review-guard script passed. Four new regression tests cover fresh-copy defaults, custom overrides, missing explicit paths and keeping client secrets/user tokens out of public state. The bundled client is intentionally distributed; user tokens, mailbox databases and user API keys remain outside the repository and image.

## Known limits

- The original bundled Groq key has a reported revocation scheduled for September 16, 2026. A replacement evaluation key can be supplied privately and entered through Models → Your Groq → Apply, without an `.env` file.
- The bundled Groq account is free and quota-limited. About 65 emails per day is an operational estimate, not a guaranteed allowance.
- The OpenAI adapter has contract and mock coverage but has not been checked with a live OpenAI key.
- Docker does not provide macOS Notification Center banners. Use the local Python launch for those while the server is running.
- An uncertain Gmail write is held for read-only reconciliation and is never blindly retried.
- The saved evaluation includes exact non-English synthetic input and model output; those raw records remain unchanged to preserve the evidence.

## Docker Google sign-in follow-up — 2026-09-14

A fresh export of the committed submission built and started with Docker Compose and an empty data directory. Using the bundled Desktop client, the previously authorized test account completed Google sign-in through the published loopback callback on port 8766. The API reported `connected` with `manage` access and no error. The user token was saved to the host-mounted `data/gmail-token.json` with mode 0600; after restarting the container, the connection returned to `connected` / `manage` without another sign-in. No history or new-mail synchronization was enabled: emails, incoming jobs, actions, Gmail operations and bindings all remained at zero. The isolated container was stopped and removed after the check. This verifies authentication and persistence, not Gmail synchronization or model evaluation.
