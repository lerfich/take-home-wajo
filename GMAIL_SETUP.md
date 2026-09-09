# Test Gmail setup

Status: the user completed OAuth and imported ten synthetic Gmail messages. Local database inspection confirmed ten completed Gmail jobs. This is an integration check, not a classification evaluation. The default access profile requests `gmail.readonly`; an explicit `manage` profile requests `gmail.modify` for planned mail actions. Import reads messages from a selected label. Optional live labeling, archiving and restoration are implemented; Gmail drafts and sending remain unavailable. The application never receives your Gmail password.

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create a separate test project, such as `Wajo test`. Do not enable paid billing, trial credits or a cloud server for this local integration.
2. Under **APIs & Services → Library**, enable **Gmail API**.
3. Open **Google Auth Platform → Branding → Get started**. Name the app `Wajo local test` and provide your contact email. For a personal Gmail account, select **External**. Review and accept any Google terms yourself if you agree.
4. In **Audience**, keep **Testing** and add the dedicated Gmail address under **Test users**. Publishing the application is unnecessary.
5. Under **Clients → Create client**, select **Desktop app**, name it `Wajo local`, and download its JSON.
6. Save it locally as `task/data/gmail-credentials.json`. The data directory is ignored by Git. Never paste credentials into chat or commit them.

## Authorize and import

From `task/`, create a Python 3.11+ environment and install the optional dependencies (already installed on the development machine):

```sh
python3 -m venv .venv
./.venv/bin/pip install -r requirements-gmail.txt
./.venv/bin/python -m mail_agent.gmail auth
```

Google opens the consent screen. Select the test mailbox and approve **read-only access** yourself. Desktop OAuth uses PKCE and a loopback callback. The token is saved as `data/gmail-token.json` with permissions `0600`. Authorization alone does not send email content to Groq.

In Gmail, create the label `Wajo-Test` and apply it only to synthetic messages. Then run:

```sh
./.venv/bin/python -m mail_agent.gmail import --label Wajo-Test --limit 10 --allow-groq
./.venv/bin/python -m mail_agent.web --db data/web-groq.sqlite3
```

If a server using this database is already running, it automatically picks up queued messages; do not start another one. An occupied-port error explains how to open the existing server. To change its database or mode, stop it with Ctrl+C first.

The import flag permits sending selected messages' sender, subject and body to Groq. Local copies enter the queue; Gmail originals are unchanged. Reimporting the same IDs does not duplicate actions. Each invocation fetches up to the requested limit. `more_available` indicates another result page; pagination and periodic polling are not implemented, so repeating the same command may only encounter already imported messages.

HTML-only messages and messages without supported text are counted as `manual_review`. Attachments are not downloaded. Inline plain-text UTF-8 is supported; more complex encodings need further work.

Testing-mode OAuth may require reauthorization after token expiry. To disconnect, stop importing and revoke the application's access in Google Account settings. Never include the local `data/` directory, which can contain message copies and tokens, in a submission archive.

References: [Gmail Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python), [OAuth consent configuration](https://developers.google.com/workspace/guides/configure-oauth-consent), and [Gmail API quotas](https://developers.google.com/workspace/gmail/api/reference/quota).

## Upgrade access for planned Gmail actions

The same Desktop OAuth client can request read, label/archive, draft and send access:

```sh
./.venv/bin/python -m mail_agent.gmail auth --access manage
```

Run from `task/`. In the Google consent screen, select the dedicated test account and approve the requested Gmail access yourself. No new client JSON is needed. If Google requires the scope to be configured in the project, add `https://www.googleapis.com/auth/gmail.modify` under Google Auth Platform's data access configuration and retry.

The new token replaces the old local token only after successful authorization and scope checking. Loading and refreshing credentials preserves the saved scope profile. A rejected consent or missing requested permission does not overwrite the existing token.

As documented in [Google's scope reference](https://developers.google.com/workspace/gmail/api/auth/scopes), `gmail.modify` covers reading, composing and sending, without immediate permanent deletion bypassing Trash. The application never requests `https://mail.google.com/`, mailbox settings or delegation access. OAuth scopes apply to the mailbox, not only the Wajo-Test label; the selected-label boundary is enforced by application code.

Granting access alone does not enable writes. Explicit live import and live server flags enable only labels, archive and restore. Sending remains unavailable in live mode. Synthetic messages can be prepared locally; live delivery requires an implemented sender and authorization for the intended test destination.

## Execute reversible Gmail actions

Use the existing token with manage access. To import **new** test messages for real actions:

```sh
./.venv/bin/python -m mail_agent.gmail import --db data/web-groq.sqlite3 --label Wajo-Test --limit 20 --allow-groq --gmail-live
./.venv/bin/python -m mail_agent.web --db data/web-groq.sqlite3 --gmail-live
```

Do not start a second server if the same database is already being served. The application shows **Gmail · real action** or **Local simulation** for each decision. Previously queued or processed local imports never become live when reimported. To test an already imported email independently, use a separate database deliberately; it creates separate decisions and preferences.

Live execution checks the connected account, exact stored Wajo-Test label ID/name, current membership and absence from Trash, Spam or Drafts. Only AI labels and Inbox membership can be changed. Labels may execute under initial permission; archives require an exact action approval or an applicable learned preference. A correction immediately resets the selected preference and queues restoration. Positive learning feedback is recorded only after Gmail confirms the archive state.

Operations persist before network I/O. Successful writes are read back. An uncertain response or interrupted operation is marked unknown and is never automatically replayed. **Check Gmail status (read only)** verifies the desired state without issuing a write. If it cannot confirm the state, review the message in Gmail manually; this version has no write-retry button. An AI label may have been created even if applying it to the message failed.

Use one server per database; do not run CLI preference mutations concurrently with the live server. Within that server, mutations are serialized with execution. Gmail can still change between the scope check and the write, or after verification; this finite race window is not eliminated. The app is not a continuous mirror of Gmail.

## Reviewer sign-in

A second Gmail account is not required: a reviewer can authorize an existing account through Google's OAuth screen. In the current **Testing** project, their address must first be added to **Test users**. They need the configured Desktop client locally, or can create their own project/client following the setup above. Never distribute your authorized-user token. A Connect Gmail button is not implemented yet; authorization uses the CLI.

Testing-mode Gmail authorization expires after seven days, and organization policies may block access. Broad public availability with restricted Gmail scopes has additional verification requirements. See [Google's audience documentation](https://support.google.com/cloud/answer/15549945?hl=en). Local demo/evaluation remains available without Gmail. Current import consent is specifically for synthetic messages sent to Groq; using private mail requires a separate explicit data-sharing decision.
