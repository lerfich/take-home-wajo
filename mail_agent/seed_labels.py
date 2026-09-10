"""Explicitly insert the bundled 30 synthetic samples into the connected test Gmail.

Run with the web server stopped. Uses messages.insert, never send. Every sample
gets Wajo-Test and human ready. A durable private manifest prevents blind replay
of uncertain insertions. Starts no model calls; queued label-only jobs run in Wajo.
"""
import argparse
import base64
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime
import json
from pathlib import Path

from .gmail import private_write, service, message_fields
from .web import Application


def seed(api, app, dataset, manifest_path):
    account = api.users().getProfile(userId="me").execute()["emailAddress"].casefold()
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest["account"] != account or manifest["batch_id"] != dataset["batch_id"]:
            raise ValueError("Existing batch belongs to a different account or dataset")
    else:
        manifest = {"batch_id":dataset["batch_id"],"account":account,"messages":{},"label_creation":None}
    def save():
        private_write(manifest_path,json.dumps(manifest,indent=2))
    labels=api.users().labels().list(userId="me").execute().get("labels",[])
    ids={x["name"]:x["id"] for x in labels}
    if "Wajo-Test" not in ids:
        raise ValueError("The Wajo-Test label must already exist for this synthetic batch")
    if "human ready" not in ids:
        if manifest.get("label_creation") == "unknown":
            raise ValueError("Previous label creation was uncertain. Check Gmail manually before continuing")
        manifest["label_creation"]="unknown";save()
        ids["human ready"]=api.users().labels().create(userId="me",body={"name":"human ready",
                "labelListVisibility":"labelShow","messageListVisibility":"show"}).execute()["id"]
        manifest["label_creation"]="done";save()
    for item in dataset["emails"]:
        if not item["sender"].endswith(".example.test"):
            raise ValueError("Only bundled fictional sender domains are permitted")
        key=item["id"]
        record=manifest["messages"].get(key)
        if record and record["status"] == "unknown":
            raise ValueError("An earlier insertion has an unknown outcome. Do not repeat it; inspect the private manifest.")
        if not record:
            msg=EmailMessage(policy=SMTP)
            msg["From"]=item["sender"];msg["To"]=account
            msg["Subject"]=item["subject"]
            msg["Date"]=format_datetime(datetime.now(timezone.utc))
            msg["Message-ID"]="<"+dataset["batch_id"]+"-"+key+"@wajo.example.test>"
            msg["X-Wajo-Synthetic-Batch"]=dataset["batch_id"]
            msg["X-Wajo-Synthetic-Item"]=key
            msg.set_content(item["body"]+"\n\n[Synthetic Wajo sample "+key+"/30. No real request or transaction.]")
            record={"status":"unknown","subject":item["subject"]}
            manifest["messages"][key]=record;save()
            inserted=api.users().messages().insert(userId="me",body={
                "raw":base64.urlsafe_b64encode(msg.as_bytes()).decode(),
                "labelIds":["INBOX",ids["Wajo-Test"],ids["human ready"]]}).execute()
            record.update(message_id=inserted["id"],status="inserted");save()
        message=api.users().messages().get(userId="me",id=record["message_id"],format="full").execute()
        headers={h["name"].lower():h["value"] for h in message["payload"].get("headers",[])}
        fields=message_fields(message)
        if (headers.get("x-wajo-synthetic-batch") != dataset["batch_id"] or headers.get("x-wajo-synthetic-item") != key
                or fields["sender"] != item["sender"] or fields["subject"] != item["subject"]
                or fields["body"].replace('\r\n','\n') != (item["body"]+"\n\n[Synthetic Wajo sample "+key+"/30. No real request or transaction.]")
                or not {"INBOX",ids["Wajo-Test"],ids["human ready"]}.issubset(message.get("labelIds",[]))):
            raise ValueError("Inserted sample does not match the expected content and labels")
        event_id="gmail:"+account+":"+record["message_id"]
        app.enqueue(fields,event_id,dict(account=account,message_id=record["message_id"],label_id=ids["Wajo-Test"],
                    label_name="Wajo-Test",initial_inbox=1),processing_mode="label_review")
        record.update(status="queued",event_id=event_id);save()
        print("Sample "+key+"/30 verified and queued",flush=True)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,default=Path("data/web-groq.sqlite3"))
    parser.add_argument("--token",type=Path,default=Path("data/gmail-token.json"))
    parser.add_argument("--manifest",type=Path,default=Path("data/label-review-30-manifest.json"))
    parser.add_argument("--allow-groq",action="store_true",required=True)
    parser.add_argument("--insert-synthetic",action="store_true",required=True)
    args=parser.parse_args()
    dataset=json.loads((Path(__file__).resolve().parents[1]/"examples"/"label-review-30.json").read_text())
    app=Application(args.db,recover_jobs=False)
    try:
        seed(service(args.token),app,dataset,args.manifest)
    except Exception:
        parser.exit(2,"Synthetic batch stopped. Check the private manifest and Gmail; do not repeat uncertain insertions.\n")


if __name__ == "__main__":
    main()
