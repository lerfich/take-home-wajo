# Mailward

Mailward is a local email agent. It connects to Gmail, sorts incoming mail, suggests replies and calendar events, and learns from the choices you confirm. It can act silently, act and notify you, ask first, or escalate. Sending normally requires approval of the exact recipient, subject, and text.

## Start

You need internet access. To connect Gmail, place your own Google Desktop OAuth client JSON at `data/gmail-credentials.json` (see [Google setup](GMAIL_SETUP.md)). The bundled free Groq model is ready to use without an API key or `.env` file.

Choose one launch method from this directory:

```sh
./run.sh                    # Python 3.11+; installs dependencies on first run
```

```sh
docker compose up --build   # Docker Desktop or Docker Engine with Compose v2
```

Open <http://127.0.0.1:8765/>. Click **Connect Gmail**, sign in with Google, review the account and initial history, allow the selected mail text to be sent to the active model, and start synchronization. Stop with Ctrl+C; for Docker, also run `docker compose down`. The local SQLite database and OAuth token remain in `data/`, which is excluded from Git and Docker images. Do not run two servers against the same `data/` directory.

## Use the app

- **Inbox:** Review conversations, pending decisions, attention items, escalations, archives, and reply drafts. Approve, edit, or reject proposed actions.
- **Calendar:** Review dates found in email and confirm or correct local events.
- **Preferences:** Review suggested Skills and manage what Mailward has learned from feedback.
- **Models:** Choose bundled Groq (about 65 emails per day, depending on quota and message length), your own Groq key, or your own OpenAI key.
- **Review labels:** Confirm or change suggested Gmail labels.
- **Special powers / Autosent:** Optional, initially off. Qualified Draft Skills can send matching replies automatically when you explicitly enable this; Autosent records those replies.

For architecture and safety decisions, read [DESIGN.md](DESIGN.md). The [evaluation report](reports/evaluation/REPORT.md) gives measured results and limitations; [example transcripts](examples/transcripts.md) show the four action levels. [PACKAGING.md](PACKAGING.md) has a clean-copy check and Docker details.
