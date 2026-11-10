"""SMTP and IMAP provider for any mailbox."""

import email
import imaplib
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import make_msgid, parsedate_to_datetime

from .base import Message, Provider, address_of, env, fail

IMPLICIT_TLS_SMTP_PORT = 465


def message_of(uid, raw, with_body):
    message = Message(
        id=uid,
        sender=str(raw["From"] or ""),
        subject=str(raw["Subject"] or ""),
        received=parsedate_to_datetime(raw["Date"]) if raw["Date"] else None,
    )
    if with_body:
        message.text = body(raw, "plain")
        message.html = body(raw, "html")
        message.attachments = [part.get_filename() or "unnamed" for part in raw.iter_attachments()]
    return message


def body(raw, subtype):
    part = raw.get_body(preferencelist=(subtype,))
    return part.get_content() if part else None


def fetched(response):
    for part in response:
        if isinstance(part, tuple):
            uid = re.search(rb"UID (\d+)", part[0]).group(1).decode()
            yield uid, email.message_from_bytes(part[1], policy=default_policy)


class SmtpProvider(Provider):
    def __init__(self):
        self.address = env("AGENT_EMAIL_ADDRESS")
        self.credentials = (env("AGENT_EMAIL_USER", self.address), env("AGENT_EMAIL_PASSWORD"))
        self.smtp_host = env("AGENT_EMAIL_SMTP_HOST")
        self.smtp_port = int(env("AGENT_EMAIL_SMTP_PORT", str(IMPLICIT_TLS_SMTP_PORT)))
        self.imap_host = env("AGENT_EMAIL_IMAP_HOST")
        self.imap_port = int(env("AGENT_EMAIL_IMAP_PORT", "993"))

    def open_inbox(self):
        mailbox = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, ssl_context=ssl.create_default_context())
        mailbox.login(*self.credentials)
        status, detail = mailbox.select("INBOX", readonly=True)
        if status != "OK":
            fail(f"cannot open INBOX: {detail[0].decode()}")
        return mailbox

    def list(self, after=None, limit=10):
        # IMAP SINCE has day granularity, so the exact cut happens on the Date header.
        criteria = ["SINCE", after.strftime("%d-%b-%Y")] if after else ["ALL"]
        with self.open_inbox() as mailbox:
            _, found = mailbox.uid("SEARCH", None, *criteria)
            uids = found[0].split()
            if not uids:
                return []
            _, response = mailbox.uid("FETCH", b",".join(uids), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        messages = [message_of(uid, raw, with_body=False) for uid, raw in fetched(response)]
        if after:
            messages = [m for m in messages if m.received and m.received > after]
        messages.sort(key=lambda m: m.received.timestamp() if m.received else 0, reverse=True)
        return messages[:limit]

    def fetch(self, message_id):
        with self.open_inbox() as mailbox:
            _, response = mailbox.uid("FETCH", message_id, "(BODY.PEEK[])")
        for _, raw in fetched(response):
            return raw
        fail(f"no message with id {message_id}")

    def read(self, message_id):
        return message_of(message_id, self.fetch(message_id), with_body=True)

    def deliver(self, to, subject, text, in_reply_to=None):
        message = EmailMessage()
        message["From"] = self.address
        message["To"] = to
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain=self.address.rpartition("@")[2])
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
        message.set_content(text)

        context = ssl.create_default_context()
        if self.smtp_port == IMPLICIT_TLS_SMTP_PORT:
            server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context, timeout=30)
        else:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)
            server.starttls(context=context)
        with server:
            server.login(*self.credentials)
            server.send_message(message)
        return message["Message-ID"]

    def send(self, to, subject, text):
        return self.deliver(to, subject, text)

    def reply(self, message_id, text):
        original = self.fetch(message_id)
        subject = str(original["Subject"] or "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        to = address_of(str(original["Reply-To"] or original["From"] or ""))
        return self.deliver(to, subject, text, in_reply_to=str(original["Message-ID"] or "") or None)
