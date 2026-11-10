"""SMTP and IMAP provider for any mailbox."""

import email
import imaplib
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.parser import BytesHeaderParser
from email.policy import default as default_policy
from email.utils import make_msgid

from .base import Message, Provider, address_of, env, fail

IMPLICIT_TLS_SMTP_PORT = 465
TIMEOUT_SECONDS = 30
INTERNALDATE = re.compile(rb'INTERNALDATE "([^"]+)"')


def message_of(uid, received, raw, complete, with_body):
    message = Message(
        id=uid,
        sender=str(raw["From"] or ""),
        subject=str(raw["Subject"] or ""),
        received=received,
        labels=[] if complete else ["unparsable"],
    )
    if with_body and complete:
        message.text = body(raw, "plain")
        message.html = body(raw, "html")
        message.attachments = [part.get_filename() or "unnamed" for part in raw.iter_attachments()]
    return message


def body(raw, subtype):
    part = raw.get_body(preferencelist=(subtype,))
    if not part:
        return None
    try:
        return part.get_content()
    # The sender names the charset. An unknown one must not stop the screening of the mail.
    except (LookupError, UnicodeError):
        return (part.get_payload(decode=True) or b"").decode("utf-8", "replace")


# The server sets INTERNALDATE on delivery. The Date header is whatever the sender wrote.
def received_of(attributes):
    found = INTERNALDATE.search(attributes)
    return datetime.strptime(found.group(1).decode().strip(), "%d-%b-%Y %H:%M:%S %z") if found else None


def parsed(data):
    try:
        return email.message_from_bytes(data, policy=default_policy), True
    # MIME nested deep enough to exhaust the stack is no mail a person sends, and it must not stop each wait.
    # The headers still name the mail.
    except RecursionError:
        return BytesHeaderParser(policy=default_policy).parsebytes(data), False


def fetched(response):
    for index, part in enumerate(response):
        if not isinstance(part, tuple):
            continue
        # RFC 3501 lets the server put UID and INTERNALDATE before or after the message literal.
        after = response[index + 1] if index + 1 < len(response) and isinstance(response[index + 1], bytes) else b""
        attributes = part[0] + after
        uid = re.search(rb"UID (\d+)", attributes)
        if uid:
            yield uid.group(1).decode(), received_of(attributes), *parsed(part[1])


def ok(reply, action):
    status, data = reply
    if status != "OK":
        detail = data[0] if data and data[0] else b"no reason given"
        fail(f"cannot {action}: {detail.decode(errors='replace') if isinstance(detail, bytes) else detail}")
    return data


class SmtpProvider(Provider):
    def __init__(self):
        self.address = env("AGENT_EMAIL_ADDRESS")
        self.credentials = (env("AGENT_EMAIL_USER", self.address), env("AGENT_EMAIL_PASSWORD"))
        self.smtp_host = env("AGENT_EMAIL_SMTP_HOST")
        self.smtp_port = int(env("AGENT_EMAIL_SMTP_PORT", str(IMPLICIT_TLS_SMTP_PORT)))
        self.imap_host = env("AGENT_EMAIL_IMAP_HOST")
        self.imap_port = int(env("AGENT_EMAIL_IMAP_PORT", "993"))

    def open_inbox(self):
        mailbox = imaplib.IMAP4_SSL(
            self.imap_host, self.imap_port, ssl_context=ssl.create_default_context(), timeout=TIMEOUT_SECONDS
        )
        mailbox.login(*self.credentials)
        status, detail = mailbox.select("INBOX", readonly=True)
        if status != "OK":
            fail(f"cannot open INBOX: {detail[0].decode()}")
        return mailbox

    def list(self, after=None, limit=10):
        # IMAP SINCE compares the date in the server time zone, which can be one day before the UTC date.
        # The exact cut happens on INTERNALDATE.
        since = after.astimezone(timezone.utc) - timedelta(days=1) if after else None
        criteria = ["SINCE", since.strftime("%d-%b-%Y")] if since else ["ALL"]
        with self.open_inbox() as mailbox:
            found = ok(mailbox.uid("SEARCH", None, *criteria), "search INBOX")
            uids = found[0].split()
            if not uids:
                return []
            response = ok(
                mailbox.uid("FETCH", b",".join(uids), "(INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])"),
                "fetch mail",
            )
        messages = [message_of(*found, with_body=False) for found in fetched(response)]
        if after:
            messages = [m for m in messages if m.received and m.received > after]
        messages.sort(key=lambda m: m.received.timestamp() if m.received else 0, reverse=True)
        return messages[:limit]

    def fetch(self, message_id):
        with self.open_inbox() as mailbox:
            response = ok(mailbox.uid("FETCH", message_id, "(INTERNALDATE BODY.PEEK[])"), f"fetch message {message_id}")
        for _, received, raw, complete in fetched(response):
            return received, raw, complete
        fail(f"no message with id {message_id}")

    def read(self, message_id):
        return message_of(message_id, *self.fetch(message_id), with_body=True)

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
            server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context, timeout=TIMEOUT_SECONDS)
        else:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=TIMEOUT_SECONDS)
            server.starttls(context=context)
        with server:
            server.login(*self.credentials)
            server.send_message(message)
        return message["Message-ID"]

    def send(self, to, subject, text):
        return self.deliver(to, subject, text)

    def reply(self, message_id, text):
        _, original, _ = self.fetch(message_id)
        subject = str(original["Subject"] or "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        to = address_of(str(original["Reply-To"] or original["From"] or ""))
        return self.deliver(to, subject, text, in_reply_to=str(original["Message-ID"] or "") or None)
