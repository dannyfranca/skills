"""Provider contract and the message shape that every provider returns."""

import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr


def fail(message):
    sys.exit(f"mail: {message}")


def env(name, fallback=None):
    value = os.environ.get(name) or fallback
    if value is None:
        fail(f"{name} is not set")
    return value


def parse_time(value):
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def address_of(header):
    return parseaddr(header or "")[1].lower()


def sender_matches(header, domain):
    sender_domain = address_of(header).rpartition("@")[2]
    domain = domain.lower().lstrip("@")
    return bool(sender_domain) and (sender_domain == domain or sender_domain.endswith(f".{domain}"))


@dataclass
class Armor:
    status: str
    verdict: str | None = None
    reason: str | None = None


@dataclass
class Message:
    id: str
    sender: str
    subject: str
    received: datetime | None
    labels: list[str] = field(default_factory=list)
    armor: Armor | None = None
    text: str | None = None
    html: str | None = None
    attachments: list[str] = field(default_factory=list)


class Provider(ABC):
    @abstractmethod
    def list(self, after=None, limit=10):
        """Received messages, newest first, without bodies."""

    @abstractmethod
    def read(self, message_id):
        """One message with its bodies."""

    @abstractmethod
    def send(self, to, subject, text):
        """Send new mail and return the provider message id."""

    @abstractmethod
    def reply(self, message_id, text):
        """Reply to the sender of a message and return the new provider message id."""
