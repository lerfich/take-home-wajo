"""Optional read-only Gmail connection. Imported messages use LOCAL execution.

No Gmail write/send endpoints are implemented. OAuth consent is granted by the user.
Only the explicitly selected test label is imported and passed to Groq.
"""
import argparse
import base64
from email.utils import parseaddr
import json
import os
from pathlib import Path

from .web import Application

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def private_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
    except Exception:
        # fdopen owns the descriptor once constructed.
        raise


def authorize(credentials_path, token_path):
    from google_auth_oauthlib.flow import InstalledAppFlow
    config = json.loads(credentials_path.read_text())
    installed = config.get("installed", {})
    if (installed.get("auth_uri") != "https://accounts.google.com/o/oauth2/auth"
            or installed.get("token_uri") != "https://oauth2.googleapis.com/token"):
        raise ValueError("Use an official Google Desktop app OAuth client JSON")
    flow = InstalledAppFlow.from_client_config(config, SCOPES, autogenerate_code_verifier=True)
    credentials = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True,
        timeout_seconds=180, authorization_prompt_message="Authorize read-only access in your browser.",
        success_message="Wajo: read-only Gmail access connected. You may close this window.")
    private_write(token_path, credentials.to_json())


def service(token_path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        private_write(token_path, credentials.to_json())
    if not credentials.valid:
        raise ValueError("Gmail authorization expired; run the auth command again")
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def plain_body(payload):
    """Only inline text/plain; no attachments, remote loads or HTML execution."""
    pieces = []
    def visit(part, depth=0):
        if depth > 20:
            raise ValueError("MIME nesting too deep")
        if part.get("filename"):
            return
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                pieces.append(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace"))
        for child in part.get("parts", []):
            visit(child, depth + 1)
    visit(payload)
    return "\n".join(pieces).strip()


def message_fields(message):
    payload = message.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    sender = parseaddr(headers.get("from", ""))[1]
    body = plain_body(payload)
    if not body or "@" not in sender:
        raise ValueError("Missing plain-text body or sender; message requires manual review")
    return {"sender": sender, "subject": headers.get("subject") or "(No subject)", "body": body}


def import_label(api, app, label, limit):
    """One bounded polling cycle. Repeated IDs are deduplicated in the local queue."""
    profile = api.users().getProfile(userId="me").execute()
    account = profile["emailAddress"].casefold()
    labels = api.users().labels().list(userId="me").execute().get("labels", [])
    label_id = next((item["id"] for item in labels if item["name"] == label), None)
    if label_id is None:
        raise ValueError("Create the exact selected test label in Gmail and label synthetic emails first")
    messages = api.users().messages().list(userId="me", labelIds=[label_id], maxResults=limit).execute()
    result = {"queued": 0, "already_imported": 0, "manual_review": 0,
              "more_available": bool(messages.get("nextPageToken"))}
    for item in messages.get("messages", []):
        event_id = f"gmail:{account}:{item['id']}"
        with app.connect() as db:
            if db.execute("SELECT 1 FROM incoming_jobs WHERE id=?", (event_id,)).fetchone():
                result["already_imported"] += 1
                continue
        message = api.users().messages().get(userId="me", id=item["id"], format="full").execute()
        try:
            fields = message_fields(message)
            app.enqueue(fields, event_id=event_id)
            result["queued"] += 1
        except ValueError:
            result["manual_review"] += 1
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", type=Path, default=Path("data/gmail-token.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    auth = sub.add_parser("auth", help="User authorizes read-only Gmail access in browser")
    auth.add_argument("--credentials", type=Path, default=Path("data/gmail-credentials.json"))
    imp = sub.add_parser("import", help="Queue selected test-label emails for local Groq analysis")
    imp.add_argument("--db", type=Path, default=Path("data/web-groq.sqlite3"))
    imp.add_argument("--label", default="Wajo-Test")
    imp.add_argument("--limit", type=int, default=10)
    imp.add_argument("--allow-groq", action="store_true", required=True,
                     help="Explicitly allow selected synthetic email text to be sent to Groq")
    args = parser.parse_args()
    try:
        if args.command == "auth":
            authorize(args.credentials, args.token)
            print("Read-only Gmail OAuth token saved locally.")
        else:
            if not 1 <= args.limit <= 50:
                parser.error("limit must be between 1 and 50")
            args.db.parent.mkdir(parents=True, exist_ok=True)
            app = Application(args.db, recover_jobs=False)
            print(json.dumps(import_label(service(args.token), app, args.label, args.limit), indent=2))
            print("Run mail_agent.web with this database to process queued messages. All actions stay LOCAL.")
    except ImportError:
        parser.exit(2, "Install optional requirements-gmail.txt in your virtual environment.\n")
    except (ValueError, FileNotFoundError) as exc:
        parser.exit(2, f"{exc}\n")
    except Exception:
        # OAuth tokens and message payloads must not be included in Google error text.
        parser.exit(2, "Gmail connection failed; verify OAuth access and the selected test label. No Gmail writes were attempted.\n")


if __name__ == "__main__":
    main()
