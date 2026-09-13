# Run Mailward from a fresh copy

Mailward runs locally on your computer. The reviewer flow is the same with either installation method: start Mailward, connect Gmail through Google, confirm the account and synchronization scope in Mailward, then use the inbox. The checked-in bundled Groq evaluation key is ready without `.env`, a key file, or a Groq account. Internet access is required for Groq inference; the free account can hit its quota. Gmail access requires a Google Desktop OAuth client and separate consent.

## Local Python

Install Python 3.11 or newer. From the directory containing `run.sh` and `compose.yaml` (the submission repository root, or `task/` in the development repository):

```sh
./run.sh
```

On first launch, this creates `.venv`, installs the pinned Gmail dependencies, and creates `data/web-groq.sqlite3`. Open <http://127.0.0.1:8765/>. Stop with Ctrl+C. Later launches reuse the same local database. Do not start Docker against the same `data/` directory at the same time.

## Docker Desktop or Docker Engine

Start Docker Desktop (macOS/Windows) or the Docker daemon (Linux). Docker Compose v2 is required. From the directory containing `run.sh` and `compose.yaml` (the submission repository root, or `task/` in the development repository):

```sh
docker compose up --build
```

Open <http://127.0.0.1:8765/>. Compose publishes only on the host's loopback interface. `data/` is mounted at `/app/data`; the database and optional OAuth files persist across container rebuilds and stay outside the image. Stop with Ctrl+C, then `docker compose down` to remove the stopped container and network. Do not use `docker compose down -v` if you later switch to a named volume setup.

## Fresh-copy check

Use a new clone or extracted handoff archive, not the working directory that contains an existing `data/` or `.venv`. In the new copy, run either launch command above. Verify that the Inbox loads and Models shows bundled Groq, then follow the Gmail connection below. To check the bundled model's live access before connecting Gmail, run `./.venv/bin/python -m mail_agent models` locally after `./run.sh` has installed dependencies, or `docker compose run --rm mailward python -m mail_agent models` in Docker. This makes a provider catalog request; it does not analyze or import mail. A working UI alone does not prove Groq inference or Gmail OAuth.

## Connect a Gmail account

Gmail OAuth is a separate per-reviewer credential. Create a Google Desktop OAuth client as described in [GMAIL_SETUP.md](GMAIL_SETUP.md), add the reviewer as a test user if the OAuth app is in Testing, and place its downloaded JSON at `data/gmail-credentials.json` in the fresh copy. Do **not** copy an existing `gmail-token.json` or SQLite database from another user. In Mailward, click **Connect Gmail** then **Connect with Google**. Local Python opens the browser automatically. In Docker, use **Continue with Google** in the panel; Google returns to `http://127.0.0.1:8766/`, which Compose forwards to the container for the three-minute authorization window. Keep ports 8765 and 8766 free. After connection, review the displayed account, history scope, live-action mode, and permission to share selected mail text with the active model before starting synchronization. New mail is subsequently checked every 10 seconds.

Docker runs Linux, so Mailward's macOS Notification Center banners are unavailable there. The inbox, model, Gmail sync, local calendar, and in-app action views remain available. For macOS background banners, use the local Python launch while the app is running. Neither launch is intended as a public network service.
