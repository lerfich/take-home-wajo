"""Durable, policy-gated macOS notifications.

The scheduler is deliberately independent from the web UI.  A server can start it
once and notifications continue to be checked while no browser tab is open.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import contextmanager
import platform
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Protocol


STARTUP_DELAY_SECONDS = 5
EVENT_REMINDER_MINUTES = 30


class Notifier(Protocol):
    def notify(self, title: str, body: str) -> bool: ...


@dataclass(frozen=True)
class NotificationJob:
    id: int
    kind: str
    dedupe_key: str
    title: str
    body: str
    source_ref: str
    source_verified: bool
    due_at: float
    expires_at: float
    status: str


@dataclass(frozen=True)
class DispatchResult:
    sent: int = 0
    suppressed: int = 0
    expired: int = 0
    failed: int = 0
    deferred: int = 0


class MacOSNotifier:
    """Show a macOS banner without placing user content in AppleScript source."""

    _SCRIPT = """on run argv
set notificationTitle to item 1 of argv
set notificationBody to item 2 of argv
display notification notificationBody with title notificationTitle
end run"""

    def __init__(self, *, system: str | None = None, timeout: float = 3.0):
        self.system = system if system is not None else platform.system()
        self.timeout = timeout

    def notify(self, title: str, body: str) -> bool:
        if self.system != "Darwin":
            return False
        try:
            # Title and body follow the script and become its argv; they are
            # never interpolated into executable AppleScript.
            completed = subprocess.run(
                ["/usr/bin/osascript", "-e", self._SCRIPT, title, body],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            return completed.returncode == 0
        except (OSError, subprocess.SubprocessError):
            # Notification permissions, a missing binary, or a timeout must not
            # stop mail processing.
            return False


def initialize(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS notification_jobs (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            dedupe_key TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            source_ref TEXT NOT NULL DEFAULT '',
            source_verified INTEGER NOT NULL DEFAULT 0,
            due_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL,
            completed_at REAL,
            error TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS notification_jobs_due
            ON notification_jobs(status, due_at);
    """)
    # Delivery may have reached Notification Center before a prior process
    # crashed. Never repeat that unknown external outcome automatically.
    db.execute("""UPDATE notification_jobs SET status='unknown',completed_at=?,
                  error='Delivery outcome unknown after restart'
                  WHERE status='dispatching'""", (time.time(),))
    db.commit()


def _utc_timestamp(value: datetime) -> float:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Notification datetimes must include a timezone")
    return value.astimezone(timezone.utc).timestamp()


def _clean_text(value: str, *, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError("Notification text must be a string")
    # Notification Center can render newlines, but compact single-line banners
    # reveal less source content and avoid visually misleading formatting.
    return " ".join(value.split())[:limit]


class NotificationScheduler:
    """SQLite-backed notification scheduler with restart-safe delivery guards.

    ``source_verifier`` and ``policy_check`` run immediately before delivery.
    They receive an immutable NotificationJob and should return false when the
    source event/email is no longer current or notification policy changed.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        notifier: Notifier | None = None,
        clock: Callable[[], float] = time.time,
        source_verifier: Callable[[NotificationJob], bool] | None = None,
        policy_check: Callable[[NotificationJob], bool] | None = None,
        launch_id: str | None = None,
        max_catchup_seconds: float = 300,
        max_per_window: int = 3,
        frequency_window_seconds: float = 60,
    ):
        self.db_path = str(db_path)
        self.notifier = notifier or MacOSNotifier()
        self.clock = clock
        self.source_verifier = source_verifier or (lambda job: job.source_verified)
        self.policy_check = policy_check or (lambda job: True)
        self.launch_id = launch_id or uuid.uuid4().hex
        self.max_catchup_seconds = max_catchup_seconds
        self.max_per_window = max_per_window
        self.frequency_window_seconds = frequency_window_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        with self._connect() as db:
            initialize(db)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _insert(self, *, kind: str, dedupe_key: str, title: str, body: str,
                source_ref: str, source_verified: bool, due_at: float,
                expires_at: float) -> int:
        if expires_at <= due_at:
            raise ValueError("Notification expiry must be after its due time")
        now = self.clock()
        with self._connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO notification_jobs
                   (kind,dedupe_key,title,body,source_ref,source_verified,due_at,
                    expires_at,status,created_at)
                   VALUES(?,?,?,?,?,?,?,?, 'pending', ?)""",
                (kind, dedupe_key, _clean_text(title, limit=100),
                 _clean_text(body, limit=300), source_ref, int(source_verified),
                 due_at, expires_at, now),
            )
            row = db.execute(
                "SELECT id FROM notification_jobs WHERE dedupe_key=?", (dedupe_key,)
            ).fetchone()
            return int(row["id"])

    def schedule_startup_mode(self, mode: str) -> int:
        """Schedule exactly one active-model banner for this process launch."""
        now = self.clock()
        due = now + STARTUP_DELAY_SECONDS
        body = ("Active model: Groq · bundled free plan"
                if mode == "Bundled free Groq" else f"Active model: {_clean_text(mode, limit=80)}")
        with self._connect() as db:
            db.execute("""UPDATE notification_jobs SET status='cancelled',completed_at=?
                          WHERE kind='startup_mode' AND status='pending'
                            AND dedupe_key<>?""",
                       (now, f"startup-mode:{self.launch_id}"))
        return self._insert(
            kind="startup_mode",
            dedupe_key=f"startup-mode:{self.launch_id}",
            title="Mailward is running",
            body=body,
            source_ref="server-launch",
            source_verified=True,
            due_at=due,
            expires_at=due + 30,
        )

    def schedule_event_reminder(
        self,
        event_id: str,
        *,
        title: str,
        starts_at: datetime,
        body: str = "Starts in 30 minutes",
        all_day: bool = False,
        source_ref: str = "",
        source_verified: bool = False,
    ) -> int | None:
        """Schedule a reminder for a timed event; all-day events have none."""
        if all_day:
            self.cancel_event(event_id)
            return None
        starts = _utc_timestamp(starts_at)
        due = starts - EVENT_REMINDER_MINUTES * 60
        occurrence = str(int(starts))
        with self._connect() as db:
            db.execute(
                """UPDATE notification_jobs SET status='cancelled', completed_at=?
                   WHERE kind='event_reminder' AND source_ref=? AND status='pending'
                         AND dedupe_key<>?""",
                (self.clock(), source_ref or f"event:{event_id}",
                 f"event:{event_id}:{occurrence}"),
            )
        return self._insert(
            kind="event_reminder",
            dedupe_key=f"event:{event_id}:{occurrence}",
            title=title,
            body=body,
            source_ref=source_ref or f"event:{event_id}",
            source_verified=source_verified,
            due_at=due,
            expires_at=starts,
        )

    def cancel_event(self, event_id: str) -> int:
        return self.cancel_source(f"event:{event_id}", kind="event_reminder")

    def cancel_source(self, source_ref: str, *, kind: str | None = None) -> int:
        """Cancel pending notifications when their source is removed or resolved."""
        with self._connect() as db:
            where = "source_ref=? AND status='pending'"
            values: list[object] = [self.clock(), source_ref]
            if kind is not None:
                where += " AND kind=?"
                values.append(kind)
            cursor = db.execute(
                f"UPDATE notification_jobs SET status='cancelled', completed_at=? WHERE {where}",
                values,
            )
            return cursor.rowcount

    def schedule_urgent_deadline(
        self,
        item_id: str,
        *,
        title: str,
        body: str,
        source_ref: str = "",
        source_verified: bool = False,
    ) -> int:
        """Queue one immediate banner for an urgent reply deadline."""
        now = self.clock()
        return self._insert(
            kind="urgent_deadline",
            dedupe_key=f"urgent:{item_id}",
            title=title,
            body=body,
            source_ref=source_ref or f"email:{item_id}",
            source_verified=source_verified,
            due_at=now,
            expires_at=now + self.max_catchup_seconds,
        )

    @staticmethod
    def _job(row: sqlite3.Row) -> NotificationJob:
        return NotificationJob(
            id=row["id"], kind=row["kind"], dedupe_key=row["dedupe_key"],
            title=row["title"], body=row["body"], source_ref=row["source_ref"],
            source_verified=bool(row["source_verified"]), due_at=row["due_at"],
            expires_at=row["expires_at"], status=row["status"],
        )

    def run_due(self) -> DispatchResult:
        """Deliver due jobs once.  All notification failures are contained."""
        now = self.clock()
        sent = suppressed = expired = failed = deferred = 0
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM notification_jobs
                   WHERE status='pending' AND due_at<=? ORDER BY due_at,id""", (now,)
            ).fetchall()
            recent = db.execute(
                """SELECT COUNT(*) FROM notification_jobs
                   WHERE status='sent' AND completed_at>?""",
                (now - self.frequency_window_seconds,),
            ).fetchone()[0]
            allowance = max(0, self.max_per_window - recent)
            for row in rows:
                job = self._job(row)
                stale_at = min(job.expires_at, job.due_at + self.max_catchup_seconds)
                if now > stale_at:
                    db.execute(
                        "UPDATE notification_jobs SET status='expired',completed_at=? WHERE id=? AND status='pending'",
                        (now, job.id),
                    )
                    expired += 1
                    continue
                if allowance <= 0:
                    deferred += 1
                    continue
                try:
                    allowed = bool(self.source_verifier(job)) and bool(self.policy_check(job))
                except Exception:
                    allowed = False
                if not allowed:
                    db.execute(
                        "UPDATE notification_jobs SET status='suppressed',completed_at=? WHERE id=? AND status='pending'",
                        (now, job.id),
                    )
                    suppressed += 1
                    continue
                claimed = db.execute(
                    "UPDATE notification_jobs SET status='dispatching' WHERE id=? AND status='pending'",
                    (job.id,),
                ).rowcount
                if not claimed:
                    continue
                db.commit()
                try:
                    delivered = bool(self.notifier.notify(job.title, job.body))
                except Exception:
                    delivered = False
                status = "sent" if delivered else "failed"
                db.execute(
                    """UPDATE notification_jobs SET status=?,completed_at=?,error=?
                       WHERE id=? AND status='dispatching'""",
                    (status, now, "" if delivered else "Notification unavailable", job.id),
                )
                allowance -= 1
                if delivered:
                    sent += 1
                else:
                    failed += 1
        return DispatchResult(sent, suppressed, expired, failed, deferred)

    def jobs(self) -> list[NotificationJob]:
        with self._connect() as db:
            return [self._job(row) for row in db.execute(
                "SELECT * FROM notification_jobs ORDER BY id"
            )]

    def start(self, *, poll_interval: float = 1.0) -> None:
        """Start the server-owned loop; safe to call more than once."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.wait(poll_interval):
                try:
                    self.run_due()
                except Exception:
                    # A locked/unavailable notification store must not terminate
                    # the mail server's other background services.
                    continue

        self._thread = threading.Thread(target=loop, name="mailward-notifications", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 4.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None

    def close(self) -> None:
        self.stop()
