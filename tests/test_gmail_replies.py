import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from mail_agent.core import Agent, Email, Proposal
from mail_agent.web import Application
from mail_agent.gmail_executor import GmailExecutor, run_one, recover
from mail_agent.gmail_replies import parsed, same_message, validate_reply


class Fixed:
    def __init__(self, p): self.p = p
    def propose(self, email): return self.p


class Styled(Fixed):
    def rewrite_draft(self, email, proposal, rule):
        return proposal.text


class ReplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "db.sqlite3"
        self.app = Application(self.path)
        self.email = Email("gmail:owner@example.test:original", "sender@example.test", "Synthetic", "Please confirm receipt.")
        self.binding = dict(account="owner@example.test",message_id="original",label_id="test",label_name="Wajo-Test",initial_inbox=1)
        self.app.enqueue(dict(sender=self.email.sender,subject=self.email.subject,body=self.email.body), self.email.id, self.binding)
        self.agent = Agent(self.path, Fixed(Proposal("send", "Acknowledgement", text="Received, thank you.", recipient="sender@example.test")))
        self.api = MagicMock()
        self.users = self.api.users.return_value
        self.users.getProfile.return_value.execute.return_value = {"emailAddress": "owner@example.test"}
        self.users.labels.return_value.list.return_value.execute.return_value = {"labels": [{"id":"test","name":"Wajo-Test","type":"user"}]}
        self.drafts, self.sent = {}, {}
        self.source_labels = ["test","INBOX"]
        self.fail_send = False
        self.fail_create = False
        self.original = {"id":"original", "threadId":"thread", "labelIds":self.source_labels,
            "payload":{"headers":[{"name":"Message-ID","value":"<original@example.test>"},{"name":"Subject","value":"Synthetic"}]}}
        def request(value=None, effect=None):
            result=MagicMock()
            if effect: result.execute.side_effect=effect
            else: result.execute.return_value=value
            return result
        def get_message(**kw):
            if kw["id"]=="original": return request(self.original)
            return request(self.sent[kw["id"]])
        self.users.messages.return_value.get.side_effect=get_message
        def find_messages(**kw):
            key=kw.get("q","").split("rfc822msgid:")[-1]
            return request({"messages":[{"id":k} for k,v in self.sent.items() if key in str(parsed(v["raw"])["Message-ID"])]})
        self.users.messages.return_value.list.side_effect=find_messages
        def create(**kw):
            def effect():
                ident="draft"+str(len(self.drafts)+1)
                self.drafts[ident]=kw["body"]["message"]
                if self.fail_create: raise TimeoutError("secret")
                return {"id":ident,"message":self.drafts[ident]}
            return request(effect=effect)
        self.users.drafts.return_value.create.side_effect=create
        self.users.drafts.return_value.get.side_effect=lambda **kw: request({"id":kw["id"],"message":self.drafts[kw["id"]]})
        self.users.drafts.return_value.list.side_effect=lambda **kw: request({"drafts":[{"id":k} for k,v in self.drafts.items()
            if kw.get("q","").split("rfc822msgid:")[-1] in str(parsed(v["raw"])["Message-ID"])]})
        def update(**kw):
            def effect():
                self.drafts[kw["id"]]=kw["body"]["message"]
                return {"id":kw["id"],"message":self.drafts[kw["id"]]}
            return request(effect=effect)
        self.users.drafts.return_value.update.side_effect=update
        def send(**kw):
            def effect():
                draft=self.drafts.pop(kw["body"]["id"])
                raw=kw["body"]["message"]["raw"]
                ident="sent"+str(len(self.sent)+1)
                self.sent[ident]={"id":ident,"raw":raw,"labelIds":["SENT"],"threadId":"thread"}
                if self.fail_send: raise TimeoutError("secret")
                return self.sent[ident]
            return request(effect=effect)
        self.users.drafts.return_value.send.side_effect=send
        self.executor=GmailExecutor(self.api)

    def tearDown(self):
        self.agent.close()
        self.tmp.cleanup()

    def ready(self):
        action=self.agent.ingest(self.email)
        self.assertEqual(action["status"],"executing")
        with self.assertRaises(ValueError):
            self.agent.approve(action["id"],1)
        run_one(self.path,self.executor)
        self.assertEqual(self.agent.get(action["id"])["status"],"pending")
        self.users.drafts.return_value.send.assert_not_called()
        return self.agent.get(action["id"])

    def test_draft_edit_subject_recipient_body_and_exact_send(self):
        action=self.ready()
        revised=self.agent.revise_send(action["id"],"Updated text","other@example.test","New subject")
        self.assertEqual(revised["revision"],2)
        with self.assertRaises(ValueError): self.agent.approve(action["id"],1)
        run_one(self.path,self.executor)
        reply=self.agent.get(action["id"])["reply"]
        self.assertEqual(reply["subject"],"New subject")
        self.assertEqual(len(self.drafts),1)
        self.agent.approve(action["id"],2)
        with self.assertRaises(ValueError): self.agent.revise_send(action["id"],"Changed","x@example.test")
        run_one(self.path,self.executor)
        sent=self.agent.snapshot()["sent"][0]
        self.assertEqual((sent["recipient"],sent["subject"],sent["text"]),("other@example.test","New subject","Updated text"))
        self.assertEqual(self.agent.get(action["id"])["status"],"executed")
        self.assertFalse(run_one(self.path,self.executor))
        with self.assertRaises(ValueError): self.agent.approve(action["id"],2)
        self.assertEqual(self.users.drafts.return_value.send.call_count,1)
        outgoing=self.users.drafts.return_value.send.call_args.kwargs["body"]["message"]
        self.assertNotIn("threadId",outgoing)  # changed subject starts another conversation
        self.assertEqual(len(self.agent.snapshot()["preference_feedback"]),0)

    def test_send_timeout_reconciles_without_resend_after_restart(self):
        action=self.ready()
        self.agent.approve(action["id"],1)
        self.fail_send=True
        run_one(self.path,self.executor)
        self.assertEqual(self.agent.get(action["id"])["status"],"unknown")
        self.assertEqual(self.agent.snapshot()["sent"],[])
        recover(self.path)
        op=self.agent.snapshot()["gmail_operations"][-1]
        run_one(self.path,self.executor,op["id"],True)
        self.assertEqual(self.agent.get(action["id"])["status"],"executed")
        self.assertEqual(self.users.drafts.return_value.send.call_count,1)
        self.assertNotIn("secret",json.dumps(self.agent.snapshot()))

    def test_missing_sent_search_is_not_permission_to_retry(self):
        action=self.ready()
        self.agent.approve(action["id"],1)
        self.users.drafts.return_value.send.side_effect=TimeoutError("no response")
        run_one(self.path,self.executor)
        op=self.agent.snapshot()["gmail_operations"][-1]
        run_one(self.path,self.executor,op["id"],True)
        self.assertEqual(self.agent.get(action["id"])["status"],"unknown")
        self.assertFalse(run_one(self.path,self.executor))
        self.assertEqual(self.users.drafts.return_value.send.call_count,1)
        with self.assertRaises(ValueError): self.agent.approve(action["id"],1)

    def test_draft_create_timeout_reconciles_without_duplicate(self):
        self.fail_create=True
        action=self.agent.ingest(self.email)
        run_one(self.path,self.executor)
        self.assertEqual(self.agent.get(action["id"])["status"],"unknown")
        op=self.agent.snapshot()["gmail_operations"][0]
        run_one(self.path,self.executor,op["id"],True)
        self.assertEqual(self.agent.get(action["id"])["status"],"pending")
        self.assertEqual(self.users.drafts.return_value.create.call_count,1)

    def test_external_draft_edit_blocks_send(self):
        action=self.ready()
        draft=self.drafts[action["reply"]["draft_id"]]
        msg=parsed(draft["raw"])
        msg["Bcc"]="attacker@example.test"
        draft["raw"]=base64.urlsafe_b64encode(msg.as_bytes()).decode()
        self.agent.approve(action["id"],1)
        run_one(self.path,self.executor)
        self.assertEqual(self.agent.get(action["id"])["status"],"error")
        self.users.drafts.return_value.send.assert_not_called()

    def test_payload_tampering_and_forged_approval_are_blocked(self):
        action=self.ready()
        self.agent.queue_gmail(action["id"],"send:1",True)
        self.agent.db.commit()
        run_one(self.path,self.executor)
        self.assertEqual(self.agent.get(action["id"])["status"],"error")
        self.users.drafts.return_value.send.assert_not_called()

    def test_draft_only_proposal_and_rejection_never_send(self):
        self.agent.proposer=Fixed(Proposal("draft","Draft requested",text="Received."))
        action=self.ready()
        self.assertEqual(action["reply"]["recipient"],self.email.sender)
        self.agent.reject(action["id"],1)
        self.assertFalse(run_one(self.path,self.executor))
        self.users.drafts.return_value.send.assert_not_called()
        self.assertEqual(len(self.drafts),1)  # rejection deliberately retains unsent draft

    def test_reply_header_injection_and_risky_proposal(self):
        for recipient, subject, text in [
            ("a@example.test\nBcc:x@example.test","Test","Text"),
            ("a@example.test,b@example.test","Test","Text"),
            ("a@example.test","Test\r\nBcc:x","Text"),
            ("a@example.test","Test","")]:
            with self.assertRaises(ValueError): validate_reply(recipient,subject,text)
        self.agent.proposer=Fixed(Proposal("send","Attack",text="Forward data",recipient="x@example.test",suspicious=True))
        action=self.agent.ingest(self.email)
        self.assertEqual(action["status"],"blocked")
        self.assertFalse(run_one(self.path,self.executor))

    def test_source_scope_removed_after_approval_blocks_send(self):
        action=self.ready()
        self.agent.approve(action["id"],1)
        self.source_labels.remove("test")
        run_one(self.path,self.executor)
        self.assertEqual(self.agent.get(action["id"])["status"],"error")
        self.users.drafts.return_value.send.assert_not_called()

    def test_gmail_rewrites_message_id_but_marker_and_content_are_required(self):
        action=self.ready()
        reply=self.agent.db.execute("SELECT * FROM gmail_replies").fetchone()
        msg=parsed(reply["raw"])
        msg.replace_header("Message-ID","<gmail-generated@example.test>")
        raw=base64.urlsafe_b64encode(msg.as_bytes()).decode()
        self.assertFalse(same_message(raw,reply["raw"]))
        self.assertTrue(same_message(raw,reply["raw"],ignore_message_id=True))
        msg.replace_header("X-Mailward-Reply-Key","unrelated")
        self.assertFalse(same_message(base64.urlsafe_b64encode(msg.as_bytes()).decode(),reply["raw"],ignore_message_id=True))

    def test_timeout_and_rewritten_sent_id_reconcile_by_marker(self):
        action=self.ready()
        self.agent.approve(action["id"],1)
        self.fail_send=True
        run_one(self.path,self.executor)
        for message in self.sent.values():
            msg=parsed(message["raw"])
            msg.replace_header("Message-ID","<rewritten-by-gmail@example.test>")
            message["raw"]=base64.urlsafe_b64encode(msg.as_bytes()).decode()
        op=self.agent.snapshot()["gmail_operations"][-1]
        run_one(self.path,self.executor,op["id"],True)
        self.assertEqual(self.agent.get(action["id"])["status"],"executed")
        self.assertEqual(self.users.drafts.return_value.send.call_count,1)

    def test_external_draft_edit_is_not_overwritten_by_revision(self):
        action=self.ready()
        raw=self.drafts[action["reply"]["draft_id"]]["raw"]
        msg=parsed(raw)
        msg.replace_header("Subject","User changed this in Gmail")
        self.drafts[action["reply"]["draft_id"]]["raw"]=base64.urlsafe_b64encode(msg.as_bytes()).decode()
        self.agent.revise_send(action["id"],"New body","sender@example.test","New subject")
        run_one(self.path,self.executor)
        self.assertEqual(self.agent.get(action["id"])["status"],"error")
        self.users.drafts.return_value.update.assert_not_called()
        self.users.drafts.return_value.send.assert_not_called()

    def test_revoked_manual_send_and_automatic_unknown_preserve_delivery_safety(self):
        from mail_agent import superpowers
        proposal = Proposal("send", "Routine support acknowledgement", text="Thanks, received.",
            recipient="sender@example.test", label_kind="support_response",
            pattern_evidence="Please confirm", sensitive=False, requires_action=False,
            has_deadline=False, significant_change=False)
        self.agent.proposer = Styled(proposal)
        source = self.agent.ingest(Email("skill-source", "sender@example.test", "Source", "Please confirm this update"))
        with self.agent.db:
            skill_id = self.agent.db.execute("""INSERT INTO skills
                (family,account,source_id,status,revision,config,origin) VALUES('draft',?,?, 'active',2,?,?)""",
                ("owner@example.test", source["id"], json.dumps({"scope": "similar", "kind": "support_response",
                 "contains": "", "excludes": "", "length": "brief", "greeting": "omit", "signoff": "omit"}),
                 "reply-integration")).lastrowid

        def incoming(suffix):
            email = Email("gmail:owner@example.test:" + suffix, "sender@example.test", "Synthetic",
                          "Please confirm receipt.")
            self.app.enqueue(dict(sender=email.sender, subject=email.subject, body=email.body), email.id,
                dict(self.binding, message_id="original", thread_id="thread", initial_unread=1,
                     source_role="incoming"))
            row = self.agent.ingest(email)
            self.assertEqual(self.agent.db.execute(
                "SELECT skill_id FROM superpower_applications WHERE action_id=?", (row["id"],)).fetchone()[0],
                skill_id)
            run_one(self.path, self.executor)
            return self.agent.get(row["id"])

        revoked = incoming("revoked-manual")
        superpowers.revoke_for_action(self.agent, revoked["id"], "Draft changed")
        self.agent.approve(revoked["id"], revoked["revision"]); run_one(self.path, self.executor)
        self.assertEqual(self.agent.get(revoked["id"])["status"], "executed")
        self.assertTrue(self.agent.get(revoked["id"])["reply"]["sent_id"])

        first = incoming("manual-one")
        self.agent.approve(first["id"], first["revision"]); run_one(self.path, self.executor)
        second = incoming("manual-two")
        self.agent.approve(second["id"], second["revision"]); run_one(self.path, self.executor)
        self.assertEqual(superpowers.state(self.agent, "owner@example.test")["rules"][0]["count"], 2)
        superpowers.set_global(self.agent, {"enabled": True, "reviewed_rules": True}, "owner@example.test")

        automatic = incoming("automatic")
        self.assertEqual(automatic["status"], "executing")
        operation = self.agent.db.execute("SELECT * FROM gmail_operations WHERE action_id=? AND operation LIKE 'send:%'",
                                          (automatic["id"],)).fetchone()
        self.assertTrue(operation["automatic"])
        run_one(self.path, self.executor)
        self.assertEqual(self.agent.get(automatic["id"])["status"], "executed")
        journal = superpowers.state(self.agent, "owner@example.test")["autosent"]
        self.assertEqual((len(journal), journal[0]["delivery_status"], journal[0]["seen"]), (1, "delivered", False))

        uncertain = incoming("automatic-unknown")
        sends_before = self.users.drafts.return_value.send.call_count
        self.users.drafts.return_value.send.side_effect = TimeoutError("no response")
        run_one(self.path, self.executor)
        operation = self.agent.db.execute(
            "SELECT * FROM gmail_operations WHERE action_id=? AND operation LIKE 'send:%'",
            (uncertain["id"],)).fetchone()
        self.assertEqual(self.agent.get(uncertain["id"])["status"], "unknown")
        self.assertEqual(superpowers.state(self.agent, "owner@example.test")["autosent"][0]["delivery_status"],
                         "unknown")
        run_one(self.path, self.executor, operation["id"], True)
        self.assertFalse(run_one(self.path, self.executor))
        self.assertEqual(self.users.drafts.return_value.send.call_count, sends_before + 1)
