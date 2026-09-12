# Test Gmail setup

Status: Stage C completed a bounded 30-message live integration exercise, including reconnect, reversible Gmail actions, drafts, manually approved sends and the explicitly enabled Superpowers path. Risky-input coverage remains primarily synthetic/mocked, and this is not an independent evaluation or a broad private-mail pilot. The default access profile requests `gmail.readonly`; an explicit `manage` profile requests `gmail.modify`. The application never receives your Gmail password.

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create a separate test project, such as `Wajo test`. Do not enable paid billing, trial credits or a cloud server for this local integration.
2. Under **APIs & Services → Library**, enable **Gmail API**.
3. Open **Google Auth Platform → Branding → Get started**. Name the app `Wajo local test` and provide your contact email. For a personal Gmail account, select **External**. Review and accept any Google terms yourself if you agree.
4. In **Audience**, keep **Testing** and add the dedicated Gmail address under **Test users**. Publishing the application is unnecessary.
5. Under **Clients → Create client**, select **Desktop app**, name it `Wajo local`, and download its JSON.
6. Save it locally as `task/data/gmail-credentials.json`. The data directory is ignored by Git. Never paste credentials into chat or commit them.

## Authorize and import

### Through the web interface

After configuring the Desktop client and installing the optional dependencies, start the app from `task/`:

```sh
./run.sh
```

Open Wajo and click **Connect Gmail**. If a saved token exists, the panel checks it without starting another sign-in. Otherwise click **Connect with Google**, complete sign-in in the system browser on the computer running Wajo, and return to the panel. Sign-in waits up to three minutes; closing the panel does not cancel it. **Reconnect with Google** renews or upgrades access. If the Google client is missing, expand the setup instructions. Custom local paths are available through `--gmail-credentials` and `--gmail-token`.

The panel shows the verified account, access profile and available Gmail labels. **Check connection** reads the Gmail profile and label list; it does not import email text or invoke Groq. Failed verification disables sync until access is checked again. If Google returns a different account, Wajo pauses synchronization and analysis and requires an explicit **Keep previous data** or **Start fresh** choice. Keep preserves the shared timeline and portable Skills. Start fresh displays current local record counts and atomically clears mail-derived Wajo data, including Skills and Superpowers history, without deleting or changing anything in Gmail. Raw provider errors and token contents never enter the panel.

Review the account and action mode, choose **Last 30**, **Last 100**, **All existing mail**, or **Only new mail**, then optionally choose up to ten Gmail labels for the initial history. The panel shows the selected count and disables additional unchecked labels at the limit; the server enforces the same maximum. The 30/100 limit is applied before that filter, so a filtered result can contain fewer messages. Labels never restrict later new mail. Explicitly allow the selected message text to be sent to the active model provider, then click **Start synchronization**. Live mode permits real Gmail actions under the existing policy. With `--local-simulation`, imported actions remain local and Connect requests read-only access. Sample mode hides Gmail and rejects its connection/sync routes. The server independently validates consent, account, live mode, history mode and selected label IDs.

History is read in bounded pages with persisted cursor and progress. **Pause import** stops before the next page; **Resume import** continues the same selection. Repeating the same selection or restarting the server retains progress, and Gmail message IDs remain unique. Changing the mode or history labels intentionally starts a new history scan, while already cached IDs still do not duplicate actions. Automatic synchronization is on by default; turning it off preserves the Gmail history cursor, and turning it back on catches up messages added while it was off.

Message download and model analysis have separate durable states. A message that cannot be fully loaded appears with `!`, cannot be opened, and is retried during synchronization no more than once per hour. An interrupted or failed analysis is retried with backoff and is complete only after the full decision is saved. Bundled Groq uses three analysis workers; user-provided Groq or OpenAI modes allow 3–12. Gmail reads do not mark mail read. Immediately before analysis, Wajo checks `UNREAD`; if the message is already read, it keeps organization/label behavior but suppresses Needs attention, archive, draft, event and escalation proposals. Sent and Draft messages are context only; Wajo does not analyze, edit or send user-created drafts. Trash and Spam are excluded. Plain text and HTML-only bodies are supported; attachments are not downloaded.

Connection, sign-in and each import/resume operation are single-flight. Gmail reads and writes are serialized; analysis uses the limit selected in Models. Browser refresh and server restart preserve sync cursors, queue state and execution state, although the connection itself is verified again after restart. Do not run concurrent CLI mutations. Connecting alone does not import emails, change the server's execution mode or approve sending.

### Through the CLI

From `task/`, create a Python 3.11+ environment and install the optional dependencies (already installed on the development machine):

```sh
python3 -m venv .venv
./.venv/bin/pip install -r requirements-gmail.txt
./.venv/bin/python -m mail_agent.gmail auth
```

Google opens the consent screen. Select the test mailbox and approve **read-only access** yourself. Desktop OAuth uses PKCE and a loopback callback. The token is saved as `data/gmail-token.json` with permissions `0600`. Authorization alone does not send email content to Groq.

The standalone CLI remains a legacy bounded diagnostic path. For that path, create the label `Wajo-Test`, apply it only to synthetic messages, then run:

```sh
./.venv/bin/python -m mail_agent.gmail import --label Wajo-Test --limit 10 --allow-groq
./.venv/bin/python -m mail_agent.web --db data/web-groq.sqlite3
```

If a server using this database is already running, it automatically picks up queued messages; do not start another one. An occupied-port error explains how to open the existing server. To change its database or mode, stop it with Ctrl+C first.

The CLI import flag permits sending selected messages' sender, subject and body to Groq. It intentionally remains one bounded Wajo-Test page and does not drive the web synchronizer. Use the web panel for resumable history and automatic polling. The default CLI import uses simulation; explicit live imports can change Gmail through approved or otherwise permitted actions. Reimporting the same IDs does not duplicate actions.

HTML-only bodies are reduced to safe plain text. Attachments are not downloaded. A message without a usable body remains incomplete and follows the hourly retry/manual-review path.

Testing-mode OAuth may require reauthorization after token expiry. To disconnect, stop importing and revoke the application's access in Google Account settings. Never include the local `data/` directory, which can contain message copies and tokens, in a submission archive.

References: [Gmail Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python), [OAuth consent configuration](https://developers.google.com/workspace/guides/configure-oauth-consent), and [Gmail API quotas](https://developers.google.com/workspace/gmail/api/reference/quota).

## Upgrade access for planned Gmail actions

The same Desktop OAuth client can request read, label/archive, draft and send access:

```sh
./.venv/bin/python -m mail_agent.gmail auth --access manage
```

Run from `task/`. In the Google consent screen, select the dedicated test account and approve the requested Gmail access yourself. No new client JSON is needed. If Google requires the scope to be configured in the project, add `https://www.googleapis.com/auth/gmail.modify` under Google Auth Platform's data access configuration and retry.

The new token replaces the old local token only after successful authorization and scope checking. Loading and refreshing credentials preserves the saved scope profile. A rejected consent or missing requested permission does not overwrite the existing token.

As documented in [Google's scope reference](https://developers.google.com/workspace/gmail/api/auth/scopes), `gmail.modify` covers reading, composing and sending, without immediate permanent deletion bypassing Trash. The application never requests `https://mail.google.com/`, mailbox settings or delegation access. OAuth scopes apply to the mailbox. In the web synchronizer, any selected labels constrain initial history only; subsequent new mail is intentionally account-wide.

Granting access alone does not enable writes. Explicit live import and live server flags enable Gmail operations. Sending requires approval of the displayed reply version by default. The only exception is the separately enabled, narrowly qualified Superpowers path described below. Synthetic messages can be prepared locally; live delivery requires an implemented sender and authorization for the intended test destination.

## Execute reversible Gmail actions

Use the existing token with manage access. To import **new** test messages for real actions:

```sh
./.venv/bin/python -m mail_agent.gmail import --db data/web-groq.sqlite3 --label Wajo-Test --limit 20 --allow-groq --gmail-live
./run.sh
```

Do not start a second server if the same database is already being served. The application shows **Gmail · real action** or **Local simulation** for each decision. Previously queued or processed local imports never become live when reimported. To test an already imported email independently, use a separate database deliberately; it creates separate decisions and preferences.

Legacy CLI bindings still check the exact stored Wajo-Test label ID/name. Web-synchronized bindings instead check the exact connected account and saved Gmail message ID, plus current absence from Trash, Spam, Drafts and Sent. Only AI labels and Inbox membership can be changed. Labels may execute under initial permission; archives require an exact action approval or an applicable learned preference. A correction immediately resets the selected preference and queues restoration. Positive learning feedback is recorded only after Gmail confirms the archive state.

Operations persist before network I/O. Successful writes are read back. An uncertain response or interrupted operation is marked unknown and is never automatically replayed. **Check Gmail status (read only)** verifies the desired state without issuing a write. If it cannot confirm the state, review the message in Gmail manually; this version has no write-retry button. An AI label may have been created even if applying it to the message failed.

Use one server per database; do not run CLI preference mutations concurrently with the live server. Within that server, mutations are serialized with execution. Gmail can still change between the scope check and the write, or after verification; this finite race window is not eliminated. The app is not a continuous mirror of Gmail.

## Reviewer sign-in

A second Gmail account is not required: a reviewer can authorize an existing account through Google's OAuth screen. In the current **Testing** project, their address must first be added to **Test users**. They need the configured Desktop client locally, or can create their own project/client following the setup above. Never distribute your authorized-user token. Connect Gmail is available in the web interface after local client setup; CLI authorization remains available.

Testing-mode Gmail authorization expires after seven days, and organization policies may block access. Broad public availability with restricted Gmail scopes has additional verification requirements. See [Google's audience documentation](https://support.google.com/cloud/answer/15549945?hl=en). Local demo/evaluation remains available without Gmail. Current import consent is specifically for synthetic messages sent to Groq; using private mail requires a separate explicit data-sharing decision.

## Draft, review, edit and send

For new live imports, a valid send/draft proposal first creates a Gmail draft. The card shows From, To, Subject, Body and the version number. Missing draft recipients default to the original sender; invalid values require human review. No message is sent while the draft is being saved.

Once the draft is verified, edit its recipient, subject or body inside Wajo and click **Save new revision**. Wait for Gmail verification. Unsaved edits disable the approval button. A changed subject starts a new conversation when it no longer matches the original subject. Only plain text and one recipient are supported; Cc, Bcc, attachments and sending aliases are not supported.

**Approve and send via Gmail** approves exactly the displayed saved version. Approval is persisted with a hash of the outgoing MIME payload. The worker rechecks the account, exact source-message binding and current scope, policy, approval and draft contents. It supplies the approved MIME payload in drafts.send, so a concurrently modified draft cannot substitute recipients or content. Changes detected before update/send stop the operation rather than overwriting external edits. A rejected send keeps the unsent draft in Gmail. Manually sending it in Gmail is outside Wajo's controls.

A successful send is verified in Sent before the app reports success. A lost response, crash or unconfirmed readback produces **unknown**, with no automatic resend. **Check Gmail status (read only)** uses the returned message ID when available; otherwise it searches by Message-ID and checks at most 50 recent Sent messages for the exact per-version X-Wajo-Reply-Key and matching sender, recipient, subject and body. Gmail can rewrite Message-ID. An absent or ambiguous match never proves that sending failed and never authorizes a retry. Draft creation uncertainty similarly uses a bounded draft scan. If matching is inconclusive or the user removed the marker/content, inspect Gmail manually.

This prevents automatic duplicate attempts within one database, but is not an exactly-once delivery guarantee across Gmail, independent applications or multiple databases. Do not manually send the same draft while Wajo is sending or investigating an unknown result. Keep one server per database; do not run CLI edits concurrently with the live worker.

## Optional Superpowers auto-send

Superpowers is disabled by default. Enabling its global toggle requires a currently verified Gmail account and an explicit review of the displayed qualified rules. A Draft Skill revision qualifies separately for that account only after Wajo has completed and verified two manual Gmail sends made with the same Skill without changing recipient, subject or body. Ordinary Skills can match across saved accounts, but this permission cannot: switching accounts does not transfer the toggle or confirmations to the new account, while changing the Skill revision, pausing/deleting/improving the Skill, rejecting or editing an applied draft, disabling the Skill permission, or turning off the toggle removes or invalidates automatic authority.

For every candidate, Wajo first saves and verifies the Gmail draft. It can auto-send only to the exact incoming sender, with the exact authorized MIME bytes, no attachment, no changed recipient/content, and no suspicious, sensitive or human-judgment condition. A second check immediately before `drafts.send` verifies the toggle, Gmail account, Skill revision, confirmation threshold and payload again. Money, legal and security-sensitive terms force review.

Every attempt appears in **Autosent** with exact recipient, subject, body, Skill revision and delivery status. An uncertain outcome is marked unknown and is never automatically queued again; use read-only reconciliation and inspect Gmail when necessary. This new path is covered only by fixtures, synthetic SQLite databases and mocked Gmail transport. No live automatic send or private-mail pilot has been performed, so keep Superpowers off for real mail.
