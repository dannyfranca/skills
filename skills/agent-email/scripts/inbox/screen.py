"""Decide what the agent may see of each message."""

from . import envelope, guard, sanitize
from .base import address_of

HELD_LABELS = ("spam", "unauthenticated", "agent-armor-held")


def pending(message):
    return message.armor is not None and message.armor.status == "pending"


def hold(message):
    """The reason to withhold a message before the guard runs, or None."""
    for label in HELD_LABELS:
        if label in message.labels:
            return f"label {label}"
    if len(message.html or "") > sanitize.MAX_HTML:
        return "html too large to screen"
    armor = message.armor
    if armor is None or (armor.status == "complete" and armor.verdict == "clear"):
        return None
    if armor.status == "complete":
        return f"armor {armor.verdict}" + (f": {armor.reason}" if armor.reason else "")
    return f"armor {armor.status}"


def listing(message):
    reason = hold(message)
    return envelope.block(message, f"withheld: {reason}" if reason else "listed")


def inspect(message, with_body=False):
    reason = hold(message)
    if reason:
        return envelope.block(message, f"withheld: {reason}")
    details = sanitize.content(message, address_of(message.sender).rpartition("@")[2])
    verdict = guard.check(envelope.block(message, "screening", details, with_body=True))
    if verdict == "unavailable":
        return envelope.block(message, "withheld: guard unavailable")
    if verdict == "flagged":
        return envelope.block(message, "withheld: guard")
    armor = "clear" if message.armor else "off"
    return envelope.block(message, f"shown (armor: {armor}, guard: {verdict or 'off'})", details, with_body)
