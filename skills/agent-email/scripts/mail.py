#!/usr/bin/env python3
"""Agent inbox: list, read, wait for, send, and reply to email through one provider."""

import argparse
import json
import os
import time

from inbox import envelope, screen
from inbox.agentmail import AgentMailProvider
from inbox.base import fail, parse_time, sender_matches
from inbox.smtp import SmtpProvider

PROVIDERS = {"agentmail": AgentMailProvider, "smtp": SmtpProvider}
POLL_SECONDS = 10
SCAN_LIMIT = 50


def provider():
    name = os.environ.get("AGENT_EMAIL_PROVIDER", "agentmail")
    if name not in PROVIDERS:
        fail(f"AGENT_EMAIL_PROVIDER must be one of {', '.join(PROVIDERS)}, not {name}")
    return PROVIDERS[name]()


def matches(message, domain, subject):
    return (not domain or sender_matches(message.sender, domain)) and (
        not subject or subject.lower() in message.subject.lower()
    )


def find(inbox, args):
    after = parse_time(args.after) if args.after else None
    filtered = args.from_domain or args.subject
    found = inbox.list(after=after, limit=max(SCAN_LIMIT, args.limit) if filtered else args.limit)
    return [m for m in found if matches(m, args.from_domain, args.subject)][: args.limit]


def run_list(inbox, args):
    print(envelope.wrap([screen.listing(m) for m in find(inbox, args)]))


def run_read(inbox, args):
    message = inbox.read(envelope.decode_id(args.id))
    print(envelope.wrap([screen.inspect(message, args.body)]))


def run_wait(inbox, args):
    deadline = time.monotonic() + args.timeout
    while True:
        found = find(inbox, args)
        if found:
            # The newest match wins: after a resend, only the latest code still works.
            message = inbox.read(found[0].id)
            # Agent Armor asks clients to act only on a final verdict, so a pending scan waits until the timeout.
            if not screen.pending(message) or time.monotonic() >= deadline:
                print(envelope.wrap([screen.inspect(message, args.body)]))
                return
        if time.monotonic() >= deadline:
            fail(f"no mail from {args.from_domain} within {args.timeout} seconds")
        time.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))


def run_send(inbox, args):
    sent = inbox.send(args.to, args.subject, args.text)
    print(json.dumps({"sent": True, "id": envelope.encode_id(sent)}))


def run_reply(inbox, args):
    sent = inbox.reply(envelope.decode_id(args.id), args.text)
    print(json.dumps({"sent": True, "id": envelope.encode_id(sent)}))


def parser():
    root = argparse.ArgumentParser(prog="mail.py", description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="list received mail")
    listing.add_argument("--after", help="only mail received after this ISO 8601 time")
    listing.add_argument("--from", dest="from_domain", help="only mail from this domain or its subdomains")
    listing.add_argument("--subject", help="only mail whose subject contains this text")
    listing.add_argument("--limit", type=int, default=10)
    listing.set_defaults(run=run_list)

    read = commands.add_parser("read", help="show one message with codes and links")
    read.add_argument("id")
    read.add_argument("--body", action="store_true", help="also show the cleaned body")
    read.set_defaults(run=run_read)

    wait = commands.add_parser("wait", help="wait for mail from a domain, then show it")
    wait.add_argument("--from", dest="from_domain", required=True)
    wait.add_argument("--after", required=True, help="ISO 8601 time taken before the mail was triggered")
    wait.add_argument("--subject")
    wait.add_argument("--timeout", type=int, default=180, help="seconds")
    wait.add_argument("--body", action="store_true")
    wait.set_defaults(run=run_wait, limit=SCAN_LIMIT)

    send = commands.add_parser("send", help="send new mail")
    send.add_argument("--to", required=True)
    send.add_argument("--subject", required=True)
    send.add_argument("--text", required=True)
    send.set_defaults(run=run_send)

    reply = commands.add_parser("reply", help="reply to the sender of a message")
    reply.add_argument("id")
    reply.add_argument("--text", required=True)
    reply.set_defaults(run=run_reply)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    args.run(provider(), args)


if __name__ == "__main__":
    main()
