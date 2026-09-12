import sqlite3
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from mail_agent.notifications import MacOSNotifier, NotificationScheduler, initialize


class Clock:
    def __init__(self, value=1_800_000_000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class Recorder:
    def __init__(self, result=True):
        self.calls = []
        self.result = result

    def notify(self, title, body):
        self.calls.append((title, body))
        return self.result


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "state.sqlite3")
        self.clock = Clock()
        self.recorder = Recorder()
        self.scheduler = NotificationScheduler(
            self.path, notifier=self.recorder, clock=self.clock, launch_id="launch-a"
        )

    def tearDown(self):
        self.scheduler.close()
        self.temp.cleanup()

    def test_startup_mode_is_once_per_launch_after_five_seconds(self):
        first = self.scheduler.schedule_startup_mode("Bundled free Groq")
        self.assertEqual(first, self.scheduler.schedule_startup_mode("Bundled free Groq"))
        self.assertEqual(self.scheduler.run_due().sent, 0)
        self.clock.advance(5)
        self.assertEqual(self.scheduler.run_due().sent, 1)
        self.assertEqual(self.scheduler.run_due().sent, 0)
        self.assertEqual(self.recorder.calls, [("Wajo is running", "Active model: Bundled free Groq")])

    def test_new_launch_gets_its_own_startup_notification(self):
        self.scheduler.schedule_startup_mode("User Groq")
        other = NotificationScheduler(
            self.path, notifier=self.recorder, clock=self.clock, launch_id="launch-b"
        )
        try:
            other.schedule_startup_mode("User Groq")
            self.clock.advance(5)
            self.assertEqual(other.run_due().sent, 2)
        finally:
            other.close()

    def test_event_reminder_is_thirty_minutes_before_local_zoned_time(self):
        starts = datetime.fromtimestamp(self.clock.value + 3600, timezone.utc)
        self.scheduler.schedule_event_reminder(
            "e1", title="Project call", starts_at=starts, source_verified=True
        )
        self.clock.advance(1799)
        self.assertEqual(self.scheduler.run_due().sent, 0)
        self.clock.advance(1)
        self.assertEqual(self.scheduler.run_due().sent, 1)

    def test_all_day_event_has_no_reminder_and_naive_time_is_rejected(self):
        naive = datetime.fromtimestamp(self.clock.value + 3600)
        self.assertIsNone(self.scheduler.schedule_event_reminder(
            "all-day", title="Holiday", starts_at=naive, all_day=True
        ))
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.scheduler.schedule_event_reminder("bad", title="Call", starts_at=naive)

    def test_reschedule_cancels_old_pending_occurrence(self):
        first = datetime.fromtimestamp(self.clock.value + 3600, timezone.utc)
        second = datetime.fromtimestamp(self.clock.value + 7200, timezone.utc)
        self.scheduler.schedule_event_reminder("e1", title="Call", starts_at=first, source_verified=True)
        self.scheduler.schedule_event_reminder("e1", title="Moved call", starts_at=second, source_verified=True)
        self.assertEqual([j.status for j in self.scheduler.jobs()], ["cancelled", "pending"])

    def test_source_resolution_cancels_pending_notification(self):
        self.scheduler.schedule_urgent_deadline(
            "mail", title="Reply", body="Due", source_ref="thread:7", source_verified=True
        )
        self.assertEqual(self.scheduler.cancel_source("thread:7"), 1)
        self.assertEqual(self.scheduler.run_due().sent, 0)

    def test_urgent_deadline_is_immediate_and_deduplicated_after_restart(self):
        first = self.scheduler.schedule_urgent_deadline(
            "mail-1", title="Reply needed", body="Reply is due soon", source_verified=True
        )
        again = self.scheduler.schedule_urgent_deadline(
            "mail-1", title="Changed text", body="Changed", source_verified=True
        )
        self.assertEqual(first, again)
        self.assertEqual(self.scheduler.run_due().sent, 1)
        reopened = NotificationScheduler(self.path, notifier=self.recorder, clock=self.clock)
        try:
            self.assertEqual(reopened.run_due().sent, 0)
        finally:
            reopened.close()

    def test_source_and_policy_are_rechecked_at_delivery(self):
        seen = []
        scheduler = NotificationScheduler(
            self.path, notifier=self.recorder, clock=self.clock,
            source_verifier=lambda job: seen.append(job.source_ref) or False,
            policy_check=lambda job: True,
        )
        try:
            scheduler.schedule_urgent_deadline(
                "untrusted", title="Reply", body="Due", source_ref="gmail:m1",
                source_verified=True,
            )
            result = scheduler.run_due()
            self.assertEqual(result.suppressed, 1)
            self.assertEqual(seen, ["gmail:m1"])
            self.assertEqual(self.recorder.calls, [])
        finally:
            scheduler.close()

    def test_unverified_source_is_suppressed_by_default(self):
        self.scheduler.schedule_urgent_deadline("x", title="Reply", body="Due")
        self.assertEqual(self.scheduler.run_due().suppressed, 1)
        self.assertEqual(self.recorder.calls, [])

    def test_stale_restart_catchup_expires_without_banner_burst(self):
        for number in range(6):
            self.scheduler.schedule_urgent_deadline(
                str(number), title="Old", body="Old deadline", source_verified=True
            )
        self.clock.advance(301)
        result = self.scheduler.run_due()
        self.assertEqual(result.expired, 6)
        self.assertEqual(self.recorder.calls, [])

    def test_frequency_guard_defers_excess_jobs(self):
        for number in range(5):
            self.scheduler.schedule_urgent_deadline(
                str(number), title="Reply", body=str(number), source_verified=True
            )
        result = self.scheduler.run_due()
        self.assertEqual((result.sent, result.deferred), (3, 2))
        self.clock.advance(61)
        self.assertEqual(self.scheduler.run_due().sent, 2)

    def test_notification_failure_is_nonfatal_and_terminal(self):
        self.scheduler.notifier = Recorder(result=False)
        self.scheduler.schedule_urgent_deadline(
            "failure", title="Reply", body="Due", source_verified=True
        )
        self.assertEqual(self.scheduler.run_due().failed, 1)
        self.assertEqual(self.scheduler.jobs()[0].status, "failed")
        self.assertEqual(self.scheduler.run_due().failed, 0)

    def test_background_loop_runs_without_a_browser(self):
        self.scheduler.schedule_urgent_deadline(
            "background", title="Reply", body="Due", source_verified=True
        )
        self.scheduler.start(poll_interval=0.01)
        deadline = time.monotonic() + 1
        while not self.recorder.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.recorder.calls, [("Reply", "Due")])

    def test_initialize_can_share_the_application_database(self):
        db = sqlite3.connect(self.path)
        try:
            initialize(db)
            initialize(db)
            self.assertTrue(db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='notification_jobs'"
            ).fetchone())
        finally:
            db.close()


class MacOSNotifierTests(unittest.TestCase):
    @patch("mail_agent.notifications.subprocess.run")
    def test_user_text_is_passed_as_argv_not_script_source(self, run):
        run.return_value.returncode = 0
        dangerous = '\" & do shell script "touch /tmp/pwned" & \"'
        notifier = MacOSNotifier(system="Darwin")
        self.assertTrue(notifier.notify("Wajo", dangerous))
        argv = run.call_args.args[0]
        self.assertEqual(argv[:3], ["/usr/bin/osascript", "-e", MacOSNotifier._SCRIPT])
        self.assertEqual(argv[-2:], ["Wajo", dangerous])
        self.assertNotIn(dangerous, MacOSNotifier._SCRIPT)
        self.assertFalse(run.call_args.kwargs["check"])

    @patch("mail_agent.notifications.subprocess.run")
    def test_non_macos_is_a_noop(self, run):
        self.assertFalse(MacOSNotifier(system="Linux").notify("Wajo", "Ready"))
        run.assert_not_called()

    @patch("mail_agent.notifications.subprocess.run", side_effect=subprocess.TimeoutExpired("osascript", 3))
    def test_os_failure_does_not_escape(self, _run):
        self.assertFalse(MacOSNotifier(system="Darwin").notify("Wajo", "Ready"))


if __name__ == "__main__":
    unittest.main()
