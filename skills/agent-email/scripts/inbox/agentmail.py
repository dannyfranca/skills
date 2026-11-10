"""AgentMail provider over its REST API."""

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta, timezone

from .base import Armor, Message, Provider, env, fail, parse_time

DEFAULT_BASE_URL = "https://api.agentmail.to"
# The API filters on "timestamp", the send time that the sender's clock sets. The wider window keeps mail
# from a sender whose clock runs late, and the exact cutoff applies to the server receive time.
CLOCK_SKEW = timedelta(days=1)


def armor_of(raw):
    if not raw:
        return None
    return Armor(raw.get("status"), raw.get("verdict"), raw.get("reason") or raw.get("unavailable_reason"))


def message_of(raw):
    return Message(
        id=raw["message_id"],
        sender=raw.get("from") or "",
        subject=raw.get("subject") or "",
        received=parse_time(received) if (received := raw.get("created_at") or raw.get("timestamp")) else None,
        labels=raw.get("labels") or [],
        armor=armor_of(raw.get("agent_armor")),
        # extracted_* drops quoted replies, so an old thread cannot smuggle text into the new message.
        text=raw.get("extracted_text") or raw.get("text"),
        html=raw.get("extracted_html") or raw.get("html"),
        attachments=[a.get("filename") or "unnamed" for a in raw.get("attachments") or []],
    )


class AgentMailProvider(Provider):
    def __init__(self):
        self.key = env("AGENTMAIL_API_KEY")
        self.inbox = env("AGENT_EMAIL_ADDRESS")
        self.base_url = env("AGENTMAIL_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

    def call(self, method, path, query=None, body=None):
        url = f"{self.base_url}/v0/inboxes/{urllib.parse.quote(self.inbox, safe='')}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                detail = error.read().decode(errors="replace")[:300]
            fail(f"AgentMail {error.code}: {detail}")
        except urllib.error.URLError as error:
            fail(f"AgentMail is unreachable: {error.reason}")

    def message_path(self, message_id):
        return f"/messages/{urllib.parse.quote(message_id, safe='')}"

    def list(self, after=None, limit=10):
        # Spam and unauthenticated mail stay visible so the agent can report it as withheld instead of timing out.
        query = {"limit": limit, "include_spam": "true", "include_unauthenticated": "true"}
        if after:
            # The API accepts only UTC times with a "Z" suffix, not "+00:00".
            window = (after - CLOCK_SKEW).astimezone(timezone.utc)
            query["after"] = window.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        found = self.call("GET", "/messages", query)["messages"]
        # Mail sent to the own address carries both "sent" and "received", so only "received" means inbound.
        inbound = [message_of(raw) for raw in found if "received" in (raw.get("labels") or [])]
        inbound = [m for m in inbound if not after or (m.received and m.received > after)]
        return sorted(inbound, key=lambda m: m.received.timestamp() if m.received else 0, reverse=True)

    def read(self, message_id):
        return message_of(self.call("GET", self.message_path(message_id)))

    def send(self, to, subject, text):
        return self.call("POST", "/messages/send", body={"to": to, "subject": subject, "text": text})["message_id"]

    def reply(self, message_id, text):
        return self.call("POST", self.message_path(message_id) + "/reply", body={"text": text})["message_id"]
