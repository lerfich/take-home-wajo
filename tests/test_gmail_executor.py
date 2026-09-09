import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from mail_agent.core import Agent, Email, Proposal
from mail_agent.web import Application
from mail_agent.gmail_executor import GmailExecutor, ScopeError, run_one, recover

class Fixed:
    def __init__(self, p): self.p = p
    def propose(self, email): return self.p

class GmailExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "db.sqlite3"
        self.app = Application(self.path)
        self.binding = dict(account="owner@example.test",message_id="m1",label_id="test",label_name="Wajo-Test",initial_inbox=1)
        self.email = Email("gmail:owner@example.test:m1","sender@example.test","Synthetic","Receipt only")
        self.app.enqueue(dict(sender=self.email.sender,subject=self.email.subject,body=self.email.body),self.email.id,self.binding)
        self.agent = Agent(self.path,Fixed(Proposal("archive","Receipt",pattern="acknowledgement_only",
            requires_action=False,has_deadline=False,significant_change=False,sensitive=False,pattern_evidence="Receipt only")))
        self.api = MagicMock()
        users = self.api.users.return_value
        users.getProfile.return_value.execute.return_value = {"emailAddress":"owner@example.test"}
        self.labels = [{"id":"test","name":"Wajo-Test","type":"user"},{"id":"ai","name":"AI: Work","type":"user"}]
        self.ids = {"test","INBOX"}
        users.labels.return_value.list.return_value.execute.side_effect = lambda: {"labels":self.labels}
        users.messages.return_value.get.return_value.execute.side_effect = lambda: {"labelIds":list(self.ids)}
        def modify(**kwargs):
            def execute():
                self.ids.update(kwargs["body"].get("addLabelIds",[]))
                self.ids.difference_update(kwargs["body"].get("removeLabelIds",[]))
                return {"labelIds":list(self.ids)}
            request=MagicMock()
            request.execute.side_effect=execute
            return request
        users.messages.return_value.modify.side_effect=modify
        self.executor=GmailExecutor(self.api)

    def tearDown(self):
        self.agent.close()
        self.temp.cleanup()

    def test_archive_approval_verified_feedback_and_restore(self):
        row=self.agent.ingest(self.email)
        self.assertEqual(row["transport"],"gmail")
        self.assertFalse(run_one(self.path,self.executor))
        self.agent.approve(row["id"],1)
        self.assertEqual(self.agent.snapshot()["preference_feedback"],[])
        self.assertEqual(self.agent.get(row["id"])["status"],"executing")
        run_one(self.path,self.executor)
        self.assertNotIn("INBOX",self.ids)
        self.assertEqual(len(self.agent.snapshot()["preference_feedback"]),1)
        self.agent.correct_archive(row["id"])
        self.assertEqual(self.agent.get(row["id"])["status"],"restoring")
        run_one(self.path,self.executor)
        self.assertIn("INBOX",self.ids)
        self.assertEqual(self.agent.get(row["id"])["status"],"corrected")
        self.assertEqual(len(self.agent.snapshot()["preference_feedback"]),2)
        self.assertFalse(run_one(self.path,self.executor))
        self.api.users.return_value.messages.return_value.send.assert_not_called()

    def test_timeout_after_write_is_unknown_and_check_does_not_repeat(self):
        row=self.agent.ingest(self.email)
        self.agent.approve(row["id"],1)
        def uncertain(*args,**kwargs):
            self.ids.discard("INBOX")
            raise TimeoutError("private data must not be logged")
        request=self.api.users.return_value.messages.return_value
        request.modify.side_effect=uncertain
        run_one(self.path,self.executor)
        self.assertEqual(self.agent.get(row["id"])["status"],"unknown")
        self.assertEqual(self.agent.snapshot()["preference_feedback"],[])
        self.assertNotIn("private data",str(self.agent.snapshot()["audit"]))
        self.assertFalse(run_one(self.path,self.executor))
        op=self.agent.snapshot()["gmail_operations"][0]
        run_one(self.path,self.executor,op["id"],True)
        self.assertEqual(request.modify.call_count,1)
        self.assertEqual(self.agent.get(row["id"])["status"],"executed")

    def test_scope_and_rule_rechecked_before_write(self):
        row=self.agent.ingest(self.email)
        self.agent.approve(row["id"],1)
        self.agent.set_archive_rule()
        run_one(self.path,self.executor)
        self.assertEqual(self.agent.get(row["id"])["status"],"error")
        self.api.users.return_value.messages.return_value.modify.assert_not_called()
        for change in ["account","label","trash"]:
            with self.subTest(change=change):
                binding=dict(self.binding)
                if change=="account": binding["account"]="other@example.test"
                if change=="label": binding["label_id"]="missing"
                if change=="trash": self.ids.add("TRASH")
                with self.assertRaises(ScopeError):
                    self.executor.apply(binding,"archive")
        self.api.users.return_value.messages.return_value.modify.assert_not_called()

    def test_legacy_import_stays_local_and_live_send_escalates(self):
        email=Email("legacy","x@example.test","Test","Test")
        self.agent.proposer=Fixed(Proposal("label","Work",label="AI: Work"))
        row=self.agent.ingest(email)
        self.assertEqual(row["transport"],"local_simulation")
        self.agent.proposer=Fixed(Proposal("send","Reply",text="Thanks",recipient="x@example.test"))
        row=self.agent.ingest(self.email)
        self.assertEqual(row["status"],"escalated")
        self.assertEqual(self.agent.snapshot()["gmail_operations"],[])

    def test_label_and_restart_recovery(self):
        self.agent.proposer=Fixed(Proposal("label","Work",label="AI: Work"))
        row=self.agent.ingest(self.email)
        run_one(self.path,self.executor)
        self.assertIn("ai",self.ids)
        self.assertEqual(self.agent.get(row["id"])["status"],"executed")
        with self.agent.db:
            self.agent.db.execute("UPDATE gmail_operations SET status='processing'")
        recover(self.path)
        self.assertEqual(self.agent.get(row["id"])["status"],"unknown")
        self.assertFalse(run_one(self.path,self.executor))

    def test_unverified_readback_and_invalid_replay(self):
        row=self.agent.ingest(self.email)
        self.agent.approve(row["id"],1)
        self.api.users.return_value.messages.return_value.modify.side_effect=None
        run_one(self.path,self.executor)
        self.assertEqual(self.agent.get(row["id"])["status"],"unknown")
        op=self.agent.snapshot()["gmail_operations"][0]
        run_one(self.path,self.executor,op["id"],True)
        self.assertEqual(self.agent.get(row["id"])["status"],"error")
        with self.assertRaises(ValueError):
            run_one(self.path,self.executor,op["id"])
        self.assertEqual(self.agent.get(row["id"])["status"],"error")

    def test_learned_archive_rechecks_revocation(self):
        for i in range(3):
            email=Email("training-"+str(i),"other@example.test","Receipt","Receipt only")
            row=self.agent.ingest(email)
            self.agent.approve(row["id"],1)
        row=self.agent.ingest(self.email)
        self.assertEqual(row["status"],"executing")
        self.assertEqual(row["autonomy"],"notify")
        with self.agent.db:
            self.agent.db.execute("DELETE FROM preference_feedback")
        run_one(self.path,self.executor)
        self.assertEqual(self.agent.get(row["id"])["status"],"error")
        self.api.users.return_value.messages.return_value.modify.assert_not_called()

    def test_live_binding_cannot_upgrade_existing_queue_item(self):
        email=Email("old","x@example.test","Test","Test")
        payload=dict(sender=email.sender,subject=email.subject,body=email.body)
        self.app.enqueue(payload,email.id)
        self.app.enqueue(payload,email.id,self.binding)
        self.agent.proposer=Fixed(Proposal("label","Work",label="AI: Work"))
        self.assertEqual(self.agent.ingest(email)["transport"],"local_simulation")

    def test_web_worker_executes_only_when_explicitly_enabled(self):
        import threading
        import time
        from unittest.mock import patch
        row=self.agent.ingest(self.email)
        self.agent.approve(row["id"],1)
        app=Application(self.path,gmail_token=Path("synthetic-token"))
        # Imported job is already processed in this test; prevent model calls.
        with app.connect() as db:
            db.execute("UPDATE incoming_jobs SET status='done'")
        with patch("mail_agent.gmail.service",return_value=self.api):
            app.worker.start()
            deadline=time.monotonic()+3
            while self.agent.get(row["id"])["status"]=="executing" and time.monotonic()<deadline:
                time.sleep(.02)
            app.stop.set()
            app.wakeup.set()
            app.worker.join(3)
        self.assertEqual(self.agent.get(row["id"])["status"],"executed")
        self.assertFalse(app.worker.is_alive())
