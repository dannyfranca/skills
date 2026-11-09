#!/usr/bin/env python3
"""Send mail over SMTP and read the IMAP INBOX for the agent-email skill."""

import argparse
import email
import imaplib
import json
import os
import re
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import make_msgid, parsedate_to_datetime

IMPLICIT_TLS_SMTP_PORT = 465


def env(name, fallback=None):
    value = os.environ.get(f"AGENT_EMAIL_{name}") or fallback
    if value is None:
        sys.exit(f"smtp_mail: AGENT_EMAIL_{name} is not set")
    return value


def credentials():
    return env("USER", os.environ.get("AGENT_EMAIL_ADDRESS")), env("PASSWORD")


def parse_time(value):
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def imap_quote(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def send(args):
    message = EmailMessage()
    message["From"] = env("ADDRESS")
    message["To"] = args.to
    message["Subject"] = args.subject
    message["Message-ID"] = make_msgid(domain=env("ADDRESS").rpartition("@")[2])
    if args.in_reply_to:
        message["In-Reply-To"] = args.in_reply_to
        message["References"] = args.in_reply_to
    message.set_content(args.text)
    if args.html:
        message.add_alternative(args.html, subtype="html")

    host, port = env("SMTP_HOST"), int(env("SMTP_PORT", str(IMPLICIT_TLS_SMTP_PORT)))
    context = ssl.create_default_context()
    if port == IMPLICIT_TLS_SMTP_PORT:
        server = smtplib.SMTP_SSL(host, port, context=context, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.starttls(context=context)
    with server:
        server.login(*credentials())
        server.send_message(message)
    return {"message_id": message["Message-ID"]}


def open_inbox():
    mailbox = imaplib.IMAP4_SSL(
        env("IMAP_HOST"), int(env("IMAP_PORT", "993")), ssl_context=ssl.create_default_context()
    )
    mailbox.login(*credentials())
    status, detail = mailbox.select("INBOX", readonly=True)
    if status != "OK":
        sys.exit(f"smtp_mail: cannot open INBOX: {detail[0].decode()}")
    return mailbox


def fetched_messages(response):
    for part in response:
        if isinstance(part, tuple):
            uid = re.search(rb"UID (\d+)", part[0]).group(1).decode()
            yield uid, email.message_from_bytes(part[1], policy=default_policy)


def summary(uid, message):
    return {
        "id": uid,
        "from": str(message["From"] or ""),
        "subject": str(message["Subject"] or ""),
        "date": parsedate_to_datetime(message["Date"]).isoformat() if message["Date"] else None,
    }


def list_messages(args):
    # IMAP SINCE has day granularity, so the exact --after cut happens on the Date header.
    criteria = []
    after = parse_time(args.after) if args.after else None
    if after:
        criteria += ["SINCE", after.strftime("%d-%b-%Y")]
    if args.sender:
        criteria += ["FROM", imap_quote(args.sender)]
    if args.subject:
        criteria += ["SUBJECT", imap_quote(args.subject)]

    with open_inbox() as mailbox:
        _, found = mailbox.uid("SEARCH", None, *(criteria or ["ALL"]))
        uids = found[0].split()
        if not uids:
            return []
        _, response = mailbox.uid(
            "FETCH", b",".join(uids), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
        )

    messages = [summary(uid, message) for uid, message in fetched_messages(response)]
    if after:
        messages = [m for m in messages if m["date"] and parse_time(m["date"]) > after]
    messages.sort(key=lambda m: m["date"] or "", reverse=True)
    return messages[: args.limit]


def body(message, subtype):
    part = message.get_body(preferencelist=(subtype,))
    return part.get_content() if part else None


def read(args):
    with open_inbox() as mailbox:
        _, response = mailbox.uid("FETCH", args.id, "(BODY.PEEK[])")
    for uid, message in fetched_messages(response):
        return {
            **summary(uid, message),
            "to": str(message["To"] or ""),
            "message_id": str(message["Message-ID"] or ""),
            "text": body(message, "plain"),
            "html": body(message, "html"),
        }
    sys.exit(f"smtp_mail: no message with id {args.id}")


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    send_cmd = commands.add_parser("send", help="send one message")
    send_cmd.add_argument("--to", required=True)
    send_cmd.add_argument("--subject", required=True)
    send_cmd.add_argument("--text", required=True)
    send_cmd.add_argument("--html")
    send_cmd.add_argument("--in-reply-to", help="Message-ID of the message to reply to")
    send_cmd.set_defaults(run=send)

    list_cmd = commands.add_parser("list", help="list INBOX messages, newest first")
    list_cmd.add_argument("--after", help="ISO 8601 time; only mail received after it")
    list_cmd.add_argument("--from", dest="sender")
    list_cmd.add_argument("--subject")
    list_cmd.add_argument("--limit", type=int, default=10)
    list_cmd.set_defaults(run=list_messages)

    read_cmd = commands.add_parser("read", help="read one message by id")
    read_cmd.add_argument("id")
    read_cmd.set_defaults(run=read)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    print(json.dumps(args.run(args), indent=2))


if __name__ == "__main__":
    main()
