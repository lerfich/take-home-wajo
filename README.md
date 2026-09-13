# Mailward

Mailward is a local email agent. It connects to Gmail, sorts incoming mail, suggests replies and calendar events, and learns from the choices you confirm. It can act silently, act and notify you, ask first, or escalate. Sending normally requires approval of the exact recipient, subject, and text.

## Start

You need internet access and a Google account that Nikita has added to Mailward's test-user allowlist. Mailward includes its Google sign-in configuration and a free Groq model: no Google Cloud project, JSON download, API key, or `.env` file is needed.

The bundled Groq key is temporary. If it becomes unavailable, request a replacement evaluation key from Nikita privately. In **Models → Your Groq**, paste it into **API key**, wait for the green validation check, then click **Apply**. No `.env` file is needed; keep the replacement key out of GitHub.

Choose one launch method from this directory:

```sh
./run.sh                    # Python 3.11+; installs dependencies on first run
```

```sh
docker compose up --build   # Docker Desktop or Docker Engine with Compose v2
```

Open <http://127.0.0.1:8765/>. Click **Connect Gmail → Connect with Google** and sign in to your account. In Docker, open **Continue with Google** from the connection panel. Google may show a Testing warning and ask you to approve access. Back in Mailward, review the account and initial history, allow the selected mail text to be sent to the active model, and start synchronization. See [Gmail connection details](GMAIL_SETUP.md) if needed.

Stop with Ctrl+C; for Docker, also run `docker compose down`. Your Gmail token, SQLite database, and any API keys you add remain in local `data/`, excluded from Git and Docker images. Do not run two servers against the same `data/` directory.

## Use the app

- **Inbox:** Review conversations, pending decisions, attention items, escalations, archives, and reply drafts. Approve, edit, or reject proposed actions.
- **Calendar:** Review dates found in email and confirm or correct local events.
- **Preferences:** Review suggested Skills and manage what Mailward has learned from feedback.
- **Models:** Choose bundled Groq (about 65 emails per day, depending on quota and message length), your own Groq key, or your own OpenAI key.
- **Review labels:** Confirm or change suggested Gmail labels.
- **Special powers / Autosent:** Optional, initially off. Qualified Draft Skills can send matching replies automatically when you explicitly enable this; Autosent records those replies.

For architecture and safety decisions, read [DESIGN.md](DESIGN.md). The [evaluation report](reports/evaluation/REPORT.md) gives measured results and limitations; [example transcripts](examples/transcripts.md) show the four action levels. [PACKAGING.md](PACKAGING.md) has a clean-copy check and Docker details.
