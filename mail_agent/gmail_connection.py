"""Background, single-flight Gmail connection and bounded manual sync for the UI.

Only explicit POST requests initiate network activity. Public state contains no
OAuth payloads. All Gmail I/O shares the executor lock; clients stay thread-local.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading


class GmailConnection:
    def __init__(self, app, token_path, credentials_path):
        self.app = app
        self.token_path = Path(token_path)
        self.credentials_path = Path(credentials_path)
        self.lock = threading.Lock()
        self.thread = None
        self.info = {"status": "unchecked" if self.token_path.is_file() else "disconnected",
                     "account": None, "access": None, "label_ready": False,
                     "checked_at": None, "operation": None, "error": None,
                     "last_sync": None, "auth_url": None}

    def snapshot(self):
        with self.lock:
            return {**deepcopy(self.info), "client_ready": self.credentials_path.is_file(),
                    "token_present": self.token_path.is_file(),
                    "live": bool(self.app.gmail_token), "label": "Wajo-Test"}

    def start(self, operation, data):
        if self.app.demo:
            raise ValueError("Gmail is unavailable in sample mode. Start the app in Groq mode.")
        with self.lock:
            if self.info["operation"]:
                raise ValueError("A Gmail connection or sync is already running. Wait for it to finish.")
            if operation == "connect":
                if data:
                    raise ValueError("Gmail access is determined by the server mode.")
                if not self.credentials_path.is_file():
                    raise ValueError("Google client setup is required. Follow the setup instructions in the Gmail panel.")
            elif operation == "sync":
                if set(data) != {"allow_groq", "account", "live", "limit"}:
                    raise ValueError("Review the Gmail account, sync mode and data-sharing consent.")
                if data["allow_groq"] is not True:
                    raise ValueError("Allow the selected synthetic email text to be sent to Groq before syncing.")
                if self.info["status"] != "connected" or not self.info["label_ready"]:
                    raise ValueError("Check the Gmail connection and Wajo-Test label before syncing.")
                if data["account"] != self.info["account"]:
                    raise ValueError("The displayed Gmail account changed. Review it again.")
                if type(data["live"]) is not bool or data["live"] != bool(self.app.gmail_token):
                    raise ValueError("The Gmail execution mode changed. Review it again.")
                if type(data["limit"]) is not int or not 1 <= data["limit"] <= 50:
                    raise ValueError("Choose between 1 and 50 messages per sync.")
                if self.app.gmail_token and self.info["access"] != "manage":
                    raise ValueError("Reconnect Gmail to grant access for live mail actions.")
            elif operation != "check" or data:
                raise ValueError("Unknown Gmail connection operation.")
            self.info.update(operation=operation, error=None)
            self.thread = threading.Thread(target=self._run, args=(operation, dict(data)), daemon=True)
            self.thread.start()
        return {"started": operation}

    def _inspect(self, api):
        from .gmail import MANAGE_SCOPE
        profile = api.users().getProfile(userId="me").execute()
        account = profile["emailAddress"].casefold()
        labels = api.users().labels().list(userId="me").execute().get("labels", [])
        # Expose only the access profile, never credentials or raw Google errors.
        scopes = json.loads(self.token_path.read_text()).get("scopes", [])
        with self.lock:
            self.info.update(status="connected", account=account,
                             access="manage" if MANAGE_SCOPE in scopes else "readonly",
                             label_ready=any(label.get("name") == "Wajo-Test" for label in labels),
                             checked_at=datetime.now(timezone.utc).isoformat())

    def _run(self, operation, data):
        from .gmail import authorize, import_label, service
        try:
            # OAuth replacement and token refresh cannot race live Gmail writes.
            with self.app.lock:
                if operation == "connect":
                    def show_url(url):
                        from urllib.parse import urlparse
                        if urlparse(url).scheme != "https" or urlparse(url).hostname != "accounts.google.com":
                            raise ValueError("Unexpected Google authorization destination")
                        with self.lock:
                            self.info["auth_url"] = url
                    authorize(self.credentials_path, self.token_path,
                              "manage" if self.app.gmail_token else "readonly", on_url=show_url)
                api = service(self.token_path)
                self._inspect(api)
                if operation == "sync":
                    with self.lock:
                        account = self.info["account"]
                        access = self.info["access"]
                    if account != data["account"]:
                        raise ValueError("Account changed")
                    if self.app.gmail_token and access != "manage":
                        raise ValueError("Manage access missing")
                    result = import_label(api, self.app, "Wajo-Test", data["limit"],
                                          live=bool(self.app.gmail_token), expected_account=data["account"])
                    with self.lock:
                        self.info["last_sync"] = {**result, "completed_at": datetime.now(timezone.utc).isoformat()}
        except Exception as exc:
            if isinstance(exc, ImportError):
                message = "Install requirements-gmail.txt in the app's Python environment, then try again."
            elif operation == "connect":
                message = ("Gmail connection did not complete. Google access may have been declined or timed out. "
                           "Check your Desktop client and Google test-user setup, then retry or check the existing connection.")
            elif operation == "sync":
                message = ("Sync did not complete. Some messages may already be queued for analysis. "
                           "Check the connection and inbox before retrying; existing message IDs are not imported twice.")
            else:
                message = "Gmail could not be verified. Check your network or reconnect with Google."
            with self.lock:
                self.info.update(status="unverified", error=message, account=None, access=None, label_ready=False)
        finally:
            with self.lock:
                self.info["operation"] = None
                self.info["auth_url"] = None
