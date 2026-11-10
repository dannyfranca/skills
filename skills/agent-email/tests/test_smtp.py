import os
import sys
import unittest
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from inbox import smtp  # noqa: E402

ENV = {
    "AGENT_EMAIL_ADDRESS": "agent@example.com",
    "AGENT_EMAIL_PASSWORD": "secret",
    "AGENT_EMAIL_SMTP_HOST": "smtp.example.com",
    "AGENT_EMAIL_IMAP_HOST": "imap.example.com",
}


def raw(sender, subject, date, text="hi", html=None, attachment=None, reply_to=None):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "agent@example.com"
    message["Subject"] = subject
    message["Date"] = date
    message["Message-ID"] = f"<{subject}@example.com>"
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    if attachment:
        message.add_attachment(b"data", maintype="application", subtype="pdf", filename=attachment)
    return bytes(message)


class FakeSmtp:
    instances = []

    def __init__(self, host, port, **_):
        self.host, self.port, self.tls, self.sent = host, port, False, []
        FakeSmtp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def starttls(self, **_):
        self.tls = True

    def login(self, user, password):
        self.login_as = (user, password)

    def send_message(self, message):
        self.sent.append(message)


class FakeImap:
    store = {}
    select_reply = ("OK", [b"2"])

    def __init__(self, host, port, **_):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def login(self, user, password):
        pass

    def select(self, mailbox, readonly):
        assert mailbox == "INBOX" and readonly
        return FakeImap.select_reply

    def uid(self, command, *args):
        if command == "SEARCH":
            FakeImap.last_search = args[1:]
            return "OK", [b" ".join(FakeImap.store)]
        uids = args[0] if isinstance(args[0], bytes) else args[0].encode()
        return "OK", [
            (b"1 (UID " + uid + b" BODY[] {1}", FakeImap.store[uid]) for uid in uids.split(b",") if uid in FakeImap.store
        ]


@patch.dict(os.environ, ENV, clear=True)
class SmtpProviderTests(unittest.TestCase):
    def setUp(self):
        FakeSmtp.instances = []
        FakeImap.select_reply = ("OK", [b"2"])
        FakeImap.store = {
            b"1": raw("old@site.com", "Old", "Mon, 09 Nov 2026 09:00:00 +0000"),
            b"2": raw(
                "Site <noreply@site.com>",
                "Code",
                "Mon, 09 Nov 2026 10:05:00 +0000",
                text="Code 123456",
                html="<a href='https://site.com/v'>v</a>",
                attachment="terms.pdf",
            ),
        }
        patches = [
            patch.object(smtp.imaplib, "IMAP4_SSL", FakeImap),
            patch.object(smtp.smtplib, "SMTP_SSL", FakeSmtp),
            patch.object(smtp.smtplib, "SMTP", FakeSmtp),
        ]
        for active in patches:
            active.start()
            self.addCleanup(active.stop)

    def test_list_keeps_only_mail_after_the_exact_time(self):
        after = datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc)
        found = smtp.SmtpProvider().list(after=after)
        self.assertEqual([m.id for m in found], ["2"])
        self.assertEqual(FakeImap.last_search, ("SINCE", "09-Nov-2026"))

    def test_list_is_newest_first_without_bodies(self):
        found = smtp.SmtpProvider().list()
        self.assertEqual([m.id for m in found], ["2", "1"])
        self.assertIsNone(found[0].text)

    def test_read_returns_bodies_and_attachment_names(self):
        message = smtp.SmtpProvider().read("2")
        self.assertIn("123456", message.text)
        self.assertIn("https://site.com/v", message.html)
        self.assertEqual(message.attachments, ["terms.pdf"])
        self.assertIsNone(message.armor)

    def test_read_of_unknown_id_fails(self):
        with self.assertRaises(SystemExit) as error:
            smtp.SmtpProvider().read("9")
        self.assertIn("no message with id 9", str(error.exception))

    def test_send_uses_implicit_tls_by_default(self):
        sent_id = smtp.SmtpProvider().send("a@b.com", "Hi", "Body")
        server = FakeSmtp.instances[0]
        self.assertEqual((server.port, server.tls), (465, False))
        self.assertEqual(server.login_as, ("agent@example.com", "secret"))
        self.assertEqual(server.sent[0]["To"], "a@b.com")
        self.assertEqual(sent_id, server.sent[0]["Message-ID"])

    def test_send_uses_starttls_on_other_ports(self):
        with patch.dict(os.environ, {"AGENT_EMAIL_SMTP_PORT": "587"}):
            smtp.SmtpProvider().send("a@b.com", "Hi", "Body")
        self.assertTrue(FakeSmtp.instances[0].tls)

    def test_reply_goes_to_reply_to_and_threads(self):
        FakeImap.store[b"3"] = raw(
            "noreply@site.com", "Welcome", "Mon, 09 Nov 2026 11:00:00 +0000", reply_to="Help <help@site.com>"
        )
        smtp.SmtpProvider().reply("3", "Thanks")
        sent = FakeSmtp.instances[0].sent[0]
        self.assertEqual(sent["To"], "help@site.com")
        self.assertEqual(sent["Subject"], "Re: Welcome")
        self.assertEqual(sent["In-Reply-To"], "<Welcome@example.com>")

    def test_unreadable_inbox_reports_the_server_reason(self):
        FakeImap.select_reply = ("NO", [b"missing permission"])
        with self.assertRaises(SystemExit) as error:
            smtp.SmtpProvider().list()
        self.assertIn("missing permission", str(error.exception))

    def test_missing_setting_names_the_variable(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit) as error:
            smtp.SmtpProvider()
        self.assertIn("AGENT_EMAIL_ADDRESS", str(error.exception))


if __name__ == "__main__":
    unittest.main()
