"""Background, single-flight Gmail connection and resumable Gmail sync for the UI.

Public state contains no OAuth payloads. All Gmail I/O shares the executor lock;
clients stay thread-local, and automatic polling is enabled only after consent.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading


BUNDLED_CREDENTIALS_PATH = Path(__file__).with_name("gmail-credentials.json")


def default_credentials_path():
    """Use an optional local client override, otherwise the shipped Mailward client."""
    local = Path("data/gmail-credentials.json")
    return local if local.is_file() else BUNDLED_CREDENTIALS_PATH


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
                     "last_sync": None, "last_poll": None, "labels": [], "auth_url": None,
                     "previous_account": None, "switch_counts": None}

    def snapshot(self):
        with self.lock:
            return {**deepcopy(self.info), "client_ready": self.credentials_path.is_file(),
                    "token_present": self.token_path.is_file(),
                    "live": bool(self.app.gmail_token), "legacy_label": "Wajo-Test"}

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
                    raise ValueError("Google client setup is missing. Reinstall Mailward or check your custom client path.")
            elif operation == "sync":
                legacy = set(data) == {"allow_groq", "account", "live", "limit"}
                modern = set(data) == {"allow_groq", "account", "live", "history_mode", "label_ids"}
                if not (legacy or modern):
                    raise ValueError("Review the Gmail account, history scope and data-sharing consent.")
                if data["allow_groq"] is not True:
                    raise ValueError("Allow selected email text to be sent to the active model provider before syncing.")
                if self.info["status"] != "connected" or (legacy and not self.info["label_ready"]):
                    raise ValueError("Check the Gmail connection and selected label before syncing.")
                if data["account"] != self.info["account"]:
                    raise ValueError("The displayed Gmail account changed. Review it again.")
                if type(data["live"]) is not bool or data["live"] != bool(self.app.gmail_token):
                    raise ValueError("The Gmail execution mode changed. Review it again.")
                if legacy and (type(data["limit"]) is not int or not 1 <= data["limit"] <= 50):
                    raise ValueError("Choose between 1 and 50 messages per sync.")
                if modern:
                    from .gmail_sync import HISTORY_MODES, MAX_HISTORY_LABELS
                    if data["history_mode"] not in HISTORY_MODES:
                        raise ValueError("Choose how much existing mail to import.")
                    if (type(data["label_ids"]) is not list
                            or any(type(value) is not str for value in data["label_ids"])):
                        raise ValueError("Choose Gmail labels from the displayed list.")
                    if len(data["label_ids"]) > MAX_HISTORY_LABELS:
                        raise ValueError(f"Choose no more than {MAX_HISTORY_LABELS} Gmail labels.")
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
        from .gmail_sync import active_account, local_data_counts, set_active_account
        profile = api.users().getProfile(userId="me").execute()
        account = profile["emailAddress"].casefold()
        labels = api.users().labels().list(userId="me").execute().get("labels", [])
        # Expose only the access profile, never credentials or raw Google errors.
        scopes = json.loads(self.token_path.read_text()).get("scopes", [])
        with self.app.connect() as db:
            previous = active_account(db)
            if previous is None:
                set_active_account(db, account)
            counts = local_data_counts(db) if previous and previous != account else None
        status = "account_choice" if counts is not None else "connected"
        with self.lock:
            self.info.update(status=status, account=account,
                             access="manage" if MANAGE_SCOPE in scopes else "readonly",
                             label_ready=any(label.get("name") == "Wajo-Test" for label in labels),
                             labels=[{"id": x.get("id", ""), "name": x.get("name", ""),
                                      "type": x.get("type", "system")} for x in labels
                                     if x.get("id") and x.get("name")],
                             checked_at=datetime.now(timezone.utc).isoformat(),
                             previous_account=previous if counts is not None else None,
                             switch_counts=counts)

    def resolve_account_switch(self, data):
        if set(data) != {"account", "previous_account", "choice"}:
            raise ValueError("Choose how to handle the previous account data.")
        if data["choice"] not in {"keep", "fresh"}:
            raise ValueError("Choose Keep previous data or Start fresh.")
        with self.lock:
            if self.info["operation"]:
                raise ValueError("A Gmail connection or sync is already running. Wait for it to finish.")
            if (self.info["status"] != "account_choice" or data["account"] != self.info["account"]
                    or data["previous_account"] != self.info["previous_account"]):
                raise ValueError("The connected account changed. Review the choice again.")
            account = self.info["account"]
            expected_counts = self.info["switch_counts"]
            self.info["operation"] = "account_switch"
        from .gmail_sync import clear_local_data, local_data_counts, set_active_account
        try:
            with self.app.analysis_condition:
                self.app.account_switching = True
                while self.app.analysis_inflight:
                    self.app.analysis_condition.wait(1)
            with self.app.lock:
                with self.app.connect() as db:
                    before = local_data_counts(db)
                    if before != expected_counts:
                        with self.lock:
                            self.info["switch_counts"] = before
                        raise ValueError("Local data changed. Review the updated deletion counts and confirm again.")
                    previous = data["previous_account"]
                    if data["choice"] == "fresh":
                        clear_local_data(db)
                    else:
                        db.execute("""UPDATE incoming_jobs SET status='paused_account' WHERE id IN
                            (SELECT j.id FROM incoming_jobs j JOIN gmail_bindings b ON b.email_id=j.id
                             WHERE b.account=? AND j.status='queued')""", (previous,))
                        db.execute("""UPDATE gmail_operations SET status='account_paused' WHERE status='queued'
                            AND action_id IN (SELECT a.id FROM actions a JOIN gmail_bindings b ON b.email_id=a.email_id
                                              WHERE b.account=?)""", (previous,))
                        db.execute("""UPDATE incoming_jobs SET status='queued' WHERE id IN
                            (SELECT j.id FROM incoming_jobs j JOIN gmail_bindings b ON b.email_id=j.id
                             WHERE b.account=? AND j.status='paused_account')""", (account,))
                        db.execute("""UPDATE gmail_operations SET status='queued' WHERE status='account_paused'
                            AND action_id IN (SELECT a.id FROM actions a JOIN gmail_bindings b ON b.email_id=a.email_id
                                              WHERE b.account=?)""", (account,))
                    set_active_account(db, account)
            with self.lock:
                self.info.update(status="connected", previous_account=None, switch_counts=None,
                                 last_sync=None, last_poll=None, error=None)
            return {"choice": data["choice"], "removed": before if data["choice"] == "fresh" else None}
        finally:
            with self.app.analysis_condition:
                self.app.account_switching = False
                self.app.analysis_condition.notify_all()
            with self.lock:
                self.info["operation"] = None
            self.app.wakeup.set()

    def _run(self, operation, data):
        from .gmail import authorize, service
        try:
            if operation == "connect":
                # OAuth replacement and token refresh cannot race live Gmail writes.
                with self.app.lock:
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
                if "limit" in data:
                    from .gmail import import_label
                    with self.app.lock:
                        result = import_label(api, self.app, "Wajo-Test", data["limit"],
                                              live=bool(self.app.gmail_token), expected_account=account)
                    with self.lock:
                        self.info["last_sync"] = {**result, "completed_at": datetime.now(timezone.utc).isoformat()}
                else:
                    from .gmail_sync import configure, history_step, public
                    configure(api, self.app, data)
                    while True:
                        with self.app.lock:
                            more = history_step(api, self.app, account)
                        if not more:
                            break
                    with self.lock:
                        self.info["last_sync"] = {**public(self.app, account)["settings"],
                                                  "completed_at": datetime.now(timezone.utc).isoformat()}
        except Exception as exc:
            if isinstance(exc, ImportError):
                message = "Install requirements-gmail.txt in the app's Python environment, then try again."
            elif operation == "connect":
                message = ("Gmail connection did not complete. Google access may have been declined or timed out. "
                           "Make sure your Google account is on Mailward's test-user allowlist, then retry or check the existing connection.")
            elif operation == "sync":
                message = ("Sync did not complete. Some messages may already be queued for analysis. "
                           "Check the connection and inbox before retrying; existing message IDs are not imported twice.")
            else:
                message = "Gmail could not be verified. Check your network or reconnect with Google."
            with self.lock:
                # A resumable sync failure does not invalidate the connection that
                # was just verified. Legacy import keeps its historical behavior.
                if operation == "sync" and "history_mode" in data and self.info["account"]:
                    self.info["error"] = message
                else:
                    self.info.update(status="unverified", error=message, account=None, access=None,
                                     label_ready=False)
        finally:
            with self.lock:
                self.info["operation"] = None
                self.info["auth_url"] = None

    def resume_history(self, account):
        with self.lock:
            if self.info["operation"]:
                raise ValueError("A Gmail connection or sync is already running. Wait for it to finish.")
            if self.info["status"] != "connected" or account != self.info["account"]:
                raise ValueError("Check the Gmail connection before resuming.")
            self.info.update(operation="sync", error=None)
            self.thread = threading.Thread(target=self._resume_history, args=(account,), daemon=True)
            self.thread.start()

    def _resume_history(self, account):
        try:
            from .gmail import service
            from .gmail_sync import history_step, public
            api = service(self.token_path)
            connected = api.users().getProfile(userId="me").execute()["emailAddress"].casefold()
            if connected != account:
                raise ValueError("Connected Gmail account changed")
            while True:
                with self.app.lock:
                    more = history_step(api, self.app, account)
                if not more:
                    break
            with self.lock:
                self.info["last_sync"] = {**public(self.app, account)["settings"],
                                          "completed_at": datetime.now(timezone.utc).isoformat()}
        except Exception:
            with self.lock:
                self.info["error"] = ("History import stopped. Progress was saved; verify the Gmail account "
                                      "and try Resume again.")
        finally:
            with self.lock:
                self.info["operation"] = None

    def poll_if_due(self):
        with self.lock:
            if self.info["status"] != "connected" or self.info["operation"] or not self.info["account"]:
                return False
            account = self.info["account"]
        from .gmail_sync import public
        state = public(self.app, account)["settings"]
        if not state:
            return False
        due = not state["next_poll_at"] or state["next_poll_at"] <= datetime.now(timezone.utc).isoformat()
        if not due:
            return False
        with self.lock:
            if self.info["operation"]:
                return False
            self.info["operation"] = "poll"
            self.thread = threading.Thread(target=self._poll, args=(account,), daemon=True)
            self.thread.start()
        return True

    def _poll(self, account):
        try:
            from .gmail import service
            from .gmail_sync import poll_new
            api = service(self.token_path)
            connected = api.users().getProfile(userId="me").execute()["emailAddress"].casefold()
            if connected != account:
                raise ValueError("Connected Gmail account changed")
            with self.app.lock:
                result = poll_new(api, self.app, account)
            with self.lock:
                self.info["last_poll"] = {**result, "completed_at": datetime.now(timezone.utc).isoformat()}
                self.info["error"] = None
            self.app.wakeup.set()
        except Exception:
            from .gmail_sync import defer_poll
            defer_poll(self.app, account)
            with self.lock:
                self.info["error"] = "Automatic Gmail sync failed. Mailward will try again; no Gmail write was repeated."
        finally:
            with self.lock:
                self.info["operation"] = None
