"""Optional Gmail connection with explicit OAuth access profiles and legacy CLI import.

The executor supports labels/archive/restore, saved drafts and exact-version approved sends. OAuth consent is granted by the user.
The standalone CLI imports one selected test label; the web synchronizer is implemented in gmail_sync.py.
"""
import argparse
import base64
from email.utils import parseaddr
import json
import os
from pathlib import Path
from html.parser import HTMLParser

from .web import Application

READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
MANAGE_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
ACCESS_SCOPES = {"readonly": [READONLY_SCOPE], "manage": [MANAGE_SCOPE]}
GMAIL_TIMEOUT_SECONDS = 25


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


def authorize(credentials_path, token_path, access="readonly", on_url=None):
    from google_auth_oauthlib.flow import InstalledAppFlow
    config = json.loads(credentials_path.read_text())
    installed = config.get("installed", {})
    if (installed.get("auth_uri") != "https://accounts.google.com/o/oauth2/auth"
            or installed.get("token_uri") != "https://oauth2.googleapis.com/token"):
        raise ValueError("Use an official Google Desktop app OAuth client JSON")
    scopes = ACCESS_SCOPES[access]
    flow = InstalledAppFlow.from_client_config(config, scopes, autogenerate_code_verifier=True)
    if on_url:
        original_url = flow.authorization_url
        def authorization_url(**kwargs):
            result = original_url(**kwargs)
            on_url(result[0])
            return result
        flow.authorization_url = authorization_url
    credentials = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True,
        timeout_seconds=180, prompt="consent",
        authorization_prompt_message=f"Authorize Gmail {access} access in your browser.",
        success_message="Wajo: Gmail authorization received. You may close this window.")
    granted = credentials.granted_scopes
    if not set(scopes).issubset(set(granted if granted is not None else credentials.scopes or [])):
        raise ValueError("Required Gmail access was not granted. The existing token was not replaced.")
    private_write(token_path, credentials.to_json())


def service(token_path):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp, Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    credentials = Credentials.from_authorized_user_file(str(token_path))
    if not {READONLY_SCOPE, MANAGE_SCOPE}.intersection(credentials.scopes or []):
        raise ValueError("Gmail read access is missing; run the auth command again")
    # Bound both token refresh and Gmail requests; never replay a mutation on 401.
    http = httplib2.Http(timeout=GMAIL_TIMEOUT_SECONDS)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request(http))
        private_write(token_path, credentials.to_json())
    if not credentials.valid:
        raise ValueError("Gmail authorization expired; run the auth command again")
    authorized_http = AuthorizedHttp(credentials, http=http, max_refresh_attempts=0)
    return build("gmail", "v1", http=authorized_http, cache_discovery=False, num_retries=0)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head"}:
            self.hidden.append(tag)

    def handle_endtag(self, tag):
        if tag in self.hidden:
            self.hidden = self.hidden[:self.hidden.index(tag)]

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def plain_body(payload):
    """Prefer inline text/plain and safely reduce inline HTML to text; skip attachments."""
    plain, html = [], []
    def visit(part, depth=0):
        if depth > 20:
            raise ValueError("MIME nesting too deep")
        if part.get("filename"):
            return
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                plain.append(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace"))
        elif part.get("mimeType") == "text/html":
            data = part.get("body", {}).get("data", "")
            if data:
                parser = _TextExtractor()
                parser.feed(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace"))
                html.append("\n".join(parser.parts))
        for child in part.get("parts", []):
            visit(child, depth + 1)
    visit(payload)
    return "\n".join(plain or html).strip()


def message_fields(message):
    payload = message.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    sender = parseaddr(headers.get("from", ""))[1]
    body = plain_body(payload)
    if not body or "@" not in sender:
        raise ValueError("Missing plain-text body or sender; message requires manual review")
    return {"sender": sender, "subject": headers.get("subject") or "(No subject)", "body": body}


def has_attachments(payload):
    """Detect attached MIME parts without downloading their bodies."""
    if payload.get("filename") or payload.get("body", {}).get("attachmentId"):
        return True
    return any(has_attachments(part) for part in payload.get("parts", []))


def import_label(api, app, label, limit, live=False, expected_account=None):
    """One bounded polling cycle. Repeated IDs are deduplicated in the local queue."""
    if live and label != "Wajo-Test":
        raise ValueError("Live writes are restricted to the Wajo-Test label")
    profile = api.users().getProfile(userId="me").execute()
    account = profile["emailAddress"].casefold()
    if expected_account is not None and account != expected_account:
        raise ValueError("The Gmail account changed. Check the connection before syncing.")
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
            binding = None
            if live:
                ids = set(message.get("labelIds", []))
                if label_id not in ids or ids.intersection({"TRASH", "SPAM", "DRAFT"}):
                    raise ValueError("Message is outside the live test scope")
                binding = {"account": account, "message_id": item["id"], "label_id": label_id,
                           "label_name": label, "initial_inbox": int("INBOX" in ids),
                           "has_attachments": int(has_attachments(message.get("payload", {})))}
            app.enqueue(fields, event_id=event_id, gmail_binding=binding)
            result["queued"] += 1
        except ValueError:
            result["manual_review"] += 1
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", type=Path, default=Path("data/gmail-token.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    auth = sub.add_parser("auth", help="User authorizes the selected Gmail access profile in browser")
    auth.add_argument("--access", choices=sorted(ACCESS_SCOPES), default="readonly",
                      help="readonly (default), or manage: read, labels, archive, drafts and send; no permanent deletion")
    auth.add_argument("--credentials", type=Path, default=Path("data/gmail-credentials.json"))
    imp = sub.add_parser("import", help="Queue selected test-label emails for local Groq analysis")
    imp.add_argument("--db", type=Path, default=Path("data/web-groq.sqlite3"))
    imp.add_argument("--gmail-live", action="store_true", help="Bind NEW imports to real Gmail operations, including approved replies; existing imports stay unchanged")
    imp.add_argument("--label", default="Wajo-Test")
    imp.add_argument("--limit", type=int, default=10)
    imp.add_argument("--allow-groq", action="store_true", required=True,
                     help="Explicitly allow selected synthetic email text to be sent to Groq")
    args = parser.parse_args()
    try:
        if args.command == "auth":
            authorize(args.credentials, args.token, args.access)
            print(f"Gmail {args.access} OAuth token saved locally. Live execution still requires explicit import and server flags.")
        else:
            if not 1 <= args.limit <= 50:
                parser.error("limit must be between 1 and 50")
            args.db.parent.mkdir(parents=True, exist_ok=True)
            app = Application(args.db, recover_jobs=False)
            print(json.dumps(import_label(service(args.token), app, args.label, args.limit, args.gmail_live), indent=2))
            print("Run mail_agent.web with this database" + (" and --gmail-live. New live imports can change Gmail; reply sending requires exact approval." if args.gmail_live else ". Newly queued messages use LOCAL simulation."))
    except ImportError:
        parser.exit(2, "Install optional requirements-gmail.txt in your virtual environment.\n")
    except (ValueError, FileNotFoundError) as exc:
        parser.exit(2, f"{exc}\n")
    except Exception:
        # OAuth tokens and message payloads must not be included in Google error text.
        parser.exit(2, "Gmail connection failed; verify OAuth access and the selected test label. No Gmail writes were attempted.\n")


if __name__ == "__main__":
    main()
