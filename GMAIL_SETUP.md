# Connect Gmail

Mailward includes the Google Desktop OAuth client configuration for Nikita's Mailward application in `mail_agent/gmail-credentials.json`, including in the Docker image. You do not need to create a Google Cloud project or download a JSON file. Each user signs in to their own Gmail account; no Gmail token or mailbox data is included with the application.

1. Send Nikita the Google email address you want to use. He adds it to **Test users** in the existing Mailward Google project. No password is needed.
2. Start Mailward with `./run.sh` or `docker compose up --build`, then open <http://127.0.0.1:8765/>.
3. Click **Connect Gmail → Connect with Google**. In Docker, use **Continue with Google** in the panel; Google returns to `http://127.0.0.1:8766/`. Choose your allowlisted account and approve access. Google may show a warning because the application is in **Testing**. Complete the sign-in within three minutes.
4. Back in Mailward, verify the account, choose the initial history, and explicitly allow the selected email text to be sent to the active model before starting synchronization.

Mailward requests `gmail.modify` for reading, labels, drafts, and approved sends. It does not request full mailbox or settings access. New incoming mail is checked every 10 seconds while the configured connection is active. Sending requires approval of the exact saved recipient, subject, and text, except for an explicitly enabled and qualified Special powers rule. A Gmail operation with an uncertain outcome is never retried blindly.

Your Gmail token, SQLite database, and any API keys you add are stored in local `data/` and excluded from Git and Docker images. Do not share another user's token or database. If Google reports a different account on reconnect, Mailward asks whether to keep previous local data or start fresh; that choice does not change Gmail. Docker requires ports 8765 and 8766 to be free on your computer.

## Optional: use a different Google client

The bundled client is used automatically. An existing `data/gmail-credentials.json` takes precedence, so a development installation with its own configuration continues to use it. You can also launch with `./run.sh --gmail-credentials PATH` to select a client explicitly; that file must exist. Keep custom client files in local `data/`, outside Git and Docker images. This override is not needed to connect an account allowlisted in Mailward's Google project.
