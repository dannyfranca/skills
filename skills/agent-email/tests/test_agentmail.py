import io
import json
import os
import sys
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from inbox import agentmail  # noqa: E402

ENV = {"AGENTMAIL_API_KEY": "key", "AGENT_EMAIL_ADDRESS": "agent@agentmail.to"}

RECEIVED = {
    "message_id": "<abc@site.com>",
    "from": "Site <noreply@site.com>",
    "subject": "Your code",
    "timestamp": "2026-11-09T10:04:00Z",
    "created_at": "2026-11-09T10:05:00Z",
    "labels": ["received"],
    "text": "Hi\n> quoted",
    "extracted_text": "Hi",
    "html": "<p>Hi</p>",
    "attachments": [{"filename": "a.pdf"}, {}],
    "agent_armor": {"status": "complete", "verdict": "review", "reason": "asks agent to act"},
}


class FakeUrlopen:
    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, request, timeout):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(json.dumps(reply).encode())


@patch.dict(os.environ, ENV, clear=True)
class AgentMailProviderTests(unittest.TestCase):
    def call(self, fake, method, *args):
        with patch.object(agentmail.urllib.request, "urlopen", fake):
            return getattr(agentmail.AgentMailProvider(), method)(*args)

    def test_list_asks_for_held_mail_and_keeps_received_mail(self):
        sent = dict(RECEIVED, message_id="<out@agentmail.to>", labels=["sent"])
        to_self = dict(RECEIVED, message_id="<self@agentmail.to>", labels=["sent", "received"])
        fake = FakeUrlopen({"messages": [RECEIVED, sent, to_self]})
        found = self.call(fake, "list", datetime(2026, 11, 9, 10, tzinfo=timezone.utc), 5)
        self.assertEqual([m.id for m in found], ["<abc@site.com>", "<self@agentmail.to>"])
        url = fake.requests[0].full_url
        self.assertTrue(url.startswith("https://api.agentmail.to/v0/inboxes/agent%40agentmail.to/messages?"))
        for part in ("limit=5", "include_spam=true", "include_unauthenticated=true", "after=2026-11-08T10%3A00%3A00.000Z"):
            self.assertIn(part, url)
        self.assertEqual(fake.requests[0].get_header("Authorization"), "Bearer key")

    def test_list_filters_and_orders_by_server_receive_time(self):
        skewed = dict(RECEIVED, message_id="<skewed@site.com>", timestamp="2026-11-09T09:50:00Z", created_at="2026-11-09T10:06:00Z")
        old = dict(RECEIVED, message_id="<old@site.com>", timestamp="2026-11-09T09:58:00Z", created_at="2026-11-09T09:58:01Z")
        bare = dict(RECEIVED, message_id="<bare@site.com>", timestamp="2026-11-09T10:01:00Z")
        del bare["created_at"]
        fake = FakeUrlopen({"messages": [RECEIVED, old, bare, skewed]})
        found = self.call(fake, "list", datetime(2026, 11, 9, 10, tzinfo=timezone.utc), 10)
        self.assertEqual([m.id for m in found], ["<skewed@site.com>", "<abc@site.com>", "<bare@site.com>"])

    def test_read_maps_fields_and_prefers_extracted_text(self):
        fake = FakeUrlopen(RECEIVED)
        message = self.call(fake, "read", "<abc@site.com>")
        self.assertTrue(fake.requests[0].full_url.endswith("/messages/%3Cabc%40site.com%3E"))
        self.assertEqual(message.text, "Hi")
        self.assertEqual(message.attachments, ["a.pdf", "unnamed"])
        self.assertEqual((message.armor.status, message.armor.verdict), ("complete", "review"))
        self.assertEqual(message.received, datetime(2026, 11, 9, 10, 5, tzinfo=timezone.utc))

    def test_unavailable_armor_keeps_its_reason(self):
        raw = dict(RECEIVED, agent_armor={"status": "unavailable", "unavailable_reason": "quota"})
        message = self.call(FakeUrlopen(raw), "read", "x")
        self.assertEqual((message.armor.status, message.armor.reason), ("unavailable", "quota"))

    def test_send_and_reply_post_json(self):
        fake = FakeUrlopen({"message_id": "<new@a>"}, {"message_id": "<re@a>"})
        with patch.object(agentmail.urllib.request, "urlopen", fake):
            inbox = agentmail.AgentMailProvider()
            self.assertEqual(inbox.send("a@b.com", "Hi", "Body"), "<new@a>")
            self.assertEqual(inbox.reply("<abc@site.com>", "Thanks"), "<re@a>")
        send, reply = fake.requests
        self.assertEqual((send.method, json.loads(send.data)), ("POST", {"to": "a@b.com", "subject": "Hi", "text": "Body"}))
        self.assertTrue(reply.full_url.endswith("/messages/%3Cabc%40site.com%3E/reply"))

    def test_http_error_names_the_status(self):
        error = urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b"bad key"))
        with self.assertRaises(SystemExit) as raised:
            self.call(FakeUrlopen(error), "list")
        self.assertIn("AgentMail 403: bad key", str(raised.exception))

    def test_base_url_override_for_eu(self):
        fake = FakeUrlopen({"messages": []})
        with patch.dict(os.environ, {"AGENTMAIL_BASE_URL": "https://api.agentmail.eu/"}):
            self.call(fake, "list")
        self.assertTrue(fake.requests[0].full_url.startswith("https://api.agentmail.eu/v0/"))


if __name__ == "__main__":
    unittest.main()
