import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import mail  # noqa: E402
from inbox import envelope  # noqa: E402
from inbox.base import Armor, Message, Provider  # noqa: E402


def message(id, sender, subject="Your code", minute=5, armor=None):
    received = datetime(2026, 11, 9, 10, minute, tzinfo=timezone.utc)
    return Message(id, sender, subject, received, labels=["received"], armor=armor, text=f"Code {id}")


class FakeProvider(Provider):
    def __init__(self, polls=None):
        self.polls, self.sent, self.reads = list(polls or [[]]), [], []

    def list(self, after=None, limit=10):
        found = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
        return [m for m in found if not after or m.received > after][:limit]

    def read(self, message_id):
        self.reads.append(message_id)
        for poll in self.polls:
            for found in poll:
                if found.id == message_id:
                    return found
        raise AssertionError(message_id)

    def send(self, to, subject, text):
        self.sent.append((to, subject, text))
        return "<new@a>"

    def reply(self, message_id, text):
        self.sent.append((message_id, text))
        return "<re@a>"


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@patch.dict(os.environ, {}, clear=True)
class MailTests(unittest.TestCase):
    def run_mail(self, inbox, *argv):
        out, clock = io.StringIO(), Clock()
        with patch.object(mail, "provider", lambda: inbox), patch.object(mail, "time", clock), redirect_stdout(out):
            mail.main(list(argv))
        self.elapsed = clock.now
        return out.getvalue()

    def test_list_filters_by_sender_domain_not_display_name(self):
        inbox = FakeProvider([[
            message("1", "Site <noreply@mail.site.com>"),
            message("2", "noreply@site.com.evil.net"),
            message("3", "site.com <x@evil.net>"),
        ]])
        out = self.run_mail(inbox, "list", "--from", "site.com")
        self.assertIn(envelope.encode_id("1"), out)
        for other in ("2", "3"):
            self.assertNotIn(f'id="{envelope.encode_id(other)}"', out)

    def test_filtered_list_honours_a_limit_above_the_scan_window(self):
        inbox = FakeProvider([[message(str(n), "noreply@site.com") for n in range(80)]])
        out = self.run_mail(inbox, "list", "--from", "site.com", "--limit", "70")
        self.assertEqual(out.count("<message "), 70)

    def test_read_decodes_the_token(self):
        inbox = FakeProvider([[message("<a@b>", "noreply@site.com")]])
        out = self.run_mail(inbox, "read", envelope.encode_id("<a@b>"), "--body")
        self.assertEqual(inbox.reads, ["<a@b>"])
        self.assertIn("<codes>", out)
        self.assertIn("<body>", out)

    def test_wait_polls_until_the_mail_arrives(self):
        inbox = FakeProvider([[], [], [message("7", "noreply@site.com", minute=6), message("6", "noreply@site.com")]])
        out = self.run_mail(inbox, "wait", "--from", "site.com", "--after", "2026-11-09T10:00:00Z")
        self.assertEqual(self.elapsed, 20)
        self.assertIn(envelope.encode_id("7"), out)
        self.assertNotIn(f'id="{envelope.encode_id("6")}"', out)

    def test_wait_ignores_mail_before_the_start(self):
        inbox = FakeProvider([[message("1", "noreply@site.com", minute=1)]])
        with self.assertRaises(SystemExit) as error:
            self.run_mail(inbox, "wait", "--from", "site.com", "--after", "2026-11-09T10:02:00Z", "--timeout", "30")
        self.assertIn("no mail from site.com within 30 seconds", str(error.exception))

    def test_wait_holds_off_while_armor_is_pending(self):
        pending = message("1", "noreply@site.com", armor=Armor("pending"))
        clear = message("1", "noreply@site.com", armor=Armor("complete", "clear"))
        inbox = FakeProvider([[pending], [clear]])
        inbox.read = lambda _id: inbox.reads.append(_id) or (pending if len(inbox.reads) == 1 else clear)
        out = self.run_mail(inbox, "wait", "--from", "site.com", "--after", "2026-11-09T10:00:00Z")
        self.assertIn("shown (armor: clear", out)

    def test_wait_reports_pending_armor_at_timeout(self):
        inbox = FakeProvider([[message("1", "noreply@site.com", armor=Armor("pending"))]])
        out = self.run_mail(inbox, "wait", "--from", "site.com", "--after", "2026-11-09T10:00:00Z", "--timeout", "15")
        self.assertIn("withheld: armor pending", out)

    def test_send_and_reply_print_tokens(self):
        inbox = FakeProvider()
        out = self.run_mail(inbox, "send", "--to", "a@b.com", "--subject", "Hi", "--text", "Body")
        self.assertEqual(json.loads(out), {"sent": True, "id": envelope.encode_id("<new@a>")})
        self.run_mail(inbox, "reply", envelope.encode_id("<a@b>"), "--text", "Thanks")
        self.assertEqual(inbox.sent[-1], ("<a@b>", "Thanks"))

    def test_unknown_provider_fails(self):
        with patch.dict(os.environ, {"AGENT_EMAIL_PROVIDER": "gmail"}), self.assertRaises(SystemExit) as error:
            mail.provider()
        self.assertIn("agentmail, smtp", str(error.exception))


if __name__ == "__main__":
    unittest.main()
