import importlib.util
import os
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "smtp_mail.py"
SPEC = importlib.util.spec_from_file_location("smtp_mail", MODULE_PATH)
smtp_mail = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smtp_mail)

ENV = {
    "AGENT_EMAIL_ADDRESS": "agent@example.com",
    "AGENT_EMAIL_PASSWORD": "secret",
    "AGENT_EMAIL_SMTP_HOST": "smtp.example.com",
    "AGENT_EMAIL_IMAP_HOST": "imap.example.com",
}


def raw(sender, subject, date, text="hi", html=None):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "agent@example.com"
    message["Subject"] = subject
    message["Date"] = date
    message["Message-ID"] = f"<{subject}@example.com>"
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
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
        self.searches = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def login(self, user, password):
        self.login_as = (user, password)

    def select(self, mailbox, readonly):
        assert mailbox == "INBOX" and readonly
        return FakeImap.select_reply

    def uid(self, command, *args):
        if command == "SEARCH":
            FakeImap.last_search = args[1:]
            return "OK", [b" ".join(FakeImap.store)]
        uids = args[0] if isinstance(args[0], bytes) else args[0].encode()
        return "OK", [
            (b"1 (UID " + uid + b" BODY[] {1}", FakeImap.store[uid])
            for uid in uids.split(b",")
            if uid in FakeImap.store
        ]


@patch.dict(os.environ, ENV, clear=True)
class SendTests(unittest.TestCase):
    def setUp(self):
        FakeSmtp.instances = []

    def run_send(self, *extra):
        with patch.object(smtp_mail.smtplib, "SMTP_SSL", FakeSmtp), patch.object(
            smtp_mail.smtplib, "SMTP", FakeSmtp
        ):
            args = smtp_mail.parser().parse_args(
                ["send", "--to", "a@b.com", "--subject", "Hi", "--text", "Body", *extra]
            )
            return args.run(args)

    def test_sends_over_implicit_tls_by_default(self):
        result = self.run_send()
        server = FakeSmtp.instances[0]
        self.assertEqual((server.port, server.tls), (465, False))
        self.assertEqual(server.login_as, ("agent@example.com", "secret"))
        self.assertEqual(server.sent[0]["To"], "a@b.com")
        self.assertEqual(result["message_id"], server.sent[0]["Message-ID"])

    def test_uses_starttls_on_other_ports(self):
        with patch.dict(os.environ, {"AGENT_EMAIL_SMTP_PORT": "587"}):
            self.run_send()
        self.assertTrue(FakeSmtp.instances[0].tls)

    def test_reply_sets_threading_headers(self):
        self.run_send("--in-reply-to", "<x@y>")
        sent = FakeSmtp.instances[0].sent[0]
        self.assertEqual((sent["In-Reply-To"], sent["References"]), ("<x@y>", "<x@y>"))


@patch.dict(os.environ, ENV, clear=True)
class ReadTests(unittest.TestCase):
    def setUp(self):
        FakeImap.store = {
            b"1": raw("old@site.com", "Old", "Mon, 09 Nov 2026 09:00:00 +0000"),
            b"2": raw("noreply@site.com", "Code", "Mon, 09 Nov 2026 10:05:00 +0000",
                      text="Code 123456", html="<a href='https://site.com/v'>v</a>"),
        }
        FakeImap.select_reply = ("OK", [b"2"])

    def call(self, *argv):
        with patch.object(smtp_mail.imaplib, "IMAP4_SSL", FakeImap):
            args = smtp_mail.parser().parse_args(list(argv))
            return args.run(args)

    def test_list_keeps_only_mail_after_the_exact_time(self):
        result = self.call("list", "--after", "2026-11-09T10:00:00Z", "--from", "noreply@site.com")
        self.assertEqual([m["id"] for m in result], ["2"])
        self.assertEqual(FakeImap.last_search, ("SINCE", "09-Nov-2026", "FROM", '"noreply@site.com"'))

    def test_list_without_filters_is_newest_first(self):
        self.assertEqual([m["id"] for m in self.call("list")], ["2", "1"])

    def test_read_returns_text_and_html(self):
        result = self.call("read", "2")
        self.assertIn("123456", result["text"])
        self.assertIn("https://site.com/v", result["html"])
        self.assertEqual(result["message_id"], "<Code@example.com>")

    def test_unreadable_inbox_reports_the_server_reason(self):
        FakeImap.select_reply = ("NO", [b"missing permission"])
        with self.assertRaises(SystemExit) as error:
            self.call("list")
        self.assertIn("missing permission", str(error.exception))

    def test_missing_setting_names_the_variable(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit) as error:
            self.call("list")
        self.assertIn("AGENT_EMAIL_IMAP_HOST", str(error.exception))


if __name__ == "__main__":
    unittest.main()
