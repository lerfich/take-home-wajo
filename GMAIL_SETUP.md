# Test Gmail setup

Status: the user completed OAuth and imported ten synthetic Gmail messages. Local database inspection confirmed ten completed Gmail jobs. This is an integration check, not a classification evaluation. The integration requests only `gmail.readonly` and imports messages from a selected label. Archiving, labeling, drafts and sending remain local simulations. The application never receives your Gmail password.

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
