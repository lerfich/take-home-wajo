# Connect Gmail

Mailward uses a Google Desktop OAuth client. Each user signs in to their own Gmail account; no Gmail token or mailbox data is included with the application.

1. In [Google Cloud Console](https://console.cloud.google.com/), create or select a project and enable the Gmail API.
2. Configure the OAuth consent screen. If the app is in **Testing**, add the Gmail account under **Test users**.
3. Create an OAuth client of type **Desktop app** and download its JSON file.
4. Save that file as `data/gmail-credentials.json` inside this directory. Keep it out of Git and handoff archives.
5. Start Mailward with `./run.sh` or `docker compose up --build`, then open <http://127.0.0.1:8765/>.
6. Click **Connect Gmail → Connect with Google**. In Docker, use the **Continue with Google** link in the panel; the callback uses local port 8766. Complete Google's consent screen within three minutes.
7. Back in Mailward, verify the account, choose the initial history, and explicitly allow the selected email text to be sent to the active model before starting synchronization.

Mailward requests `gmail.modify` for reading, labels, drafts, and approved sends. It does not request full mailbox or settings access. New incoming mail is checked every 10 seconds while the configured connection is active. Sending requires approval of the exact saved recipient, subject, and text, except for an explicitly enabled and qualified Special powers rule. A Gmail operation with an uncertain outcome is never retried blindly.

The token and SQLite database are stored in `data/` and excluded from Git and Docker images. Do not share another user's token or database. If Google reports a different account on reconnect, Mailward asks whether to keep previous local data or start fresh; that choice does not change Gmail. Docker requires ports 8765 and 8766 to be free on your computer.
