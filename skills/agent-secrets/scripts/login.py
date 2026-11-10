#!/usr/bin/env python3
"""Store and look up the logins of accounts that the agent creates, for the agent-secrets skill."""

import argparse
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone

SETUP = "$HOME/.agents/skills/agent-secrets/references/setup.md"


def fail(message):
    sys.exit(f"login: {message}")


def project_id():
    for name in ("BWS_ACCESS_TOKEN", "AGENT_SECRETS_PROJECT_ID"):
        if not os.environ.get(name):
            fail(f"{name} is not set. Follow {SETUP}")
    return os.environ["AGENT_SECRETS_PROJECT_ID"]


def bws(*args):
    try:
        result = subprocess.run(["bws", "--output", "json", *args], capture_output=True, text=True)
    except FileNotFoundError:
        fail(f"bws is not installed. Follow {SETUP}")
    if result.returncode:
        fail(result.stderr.strip() or f"bws exited with {result.returncode}")
    return result.stdout


def meta(secret):
    try:
        note = json.loads(secret.get("note") or "{}")
    except ValueError:
        return {}
    # The user can edit the note in the Bitwarden web app, so any JSON value can come back.
    return note if isinstance(note, dict) else {}


def logins(domain=None):
    entries = json.loads(bws("secret", "list", project_id()))
    if domain is None:
        return entries
    return [entry for entry in entries if entry["key"].startswith(f"{domain}/")]


def find(domain, username):
    key = f"{domain}/{username}"
    for entry in logins(domain):
        if entry["key"] == key:
            return entry
    return None


def require(domain, username):
    entry = find(domain, username)
    if entry is None:
        fail(f"no login {domain}/{username}")
    return entry


# An active login is a working account. A wrong edit locks the agent out, so only the user changes it.
def require_pending(domain, username):
    entry = require(domain, username)
    if meta(entry).get("status") != "pending":
        fail(f"{domain}/{username} is active. Only the user can change it, in the Bitwarden web app")
    return entry


def summary(entry):
    note = meta(entry)
    return {
        "login": entry["key"],
        "status": note.get("status"),
        "url": note.get("url"),
        "email": note.get("email"),
    }


def emit(value):
    print(json.dumps(value, indent=2))


def cmd_get(args):
    entry = require(args.domain, args.username)
    emit({**summary(entry), "password": entry["value"]})


def cmd_list(args):
    emit([summary(entry) for entry in logins(args.domain)])


def cmd_new(args):
    if find(args.domain, args.username):
        fail(f"{args.domain}/{args.username} exists. Use get")
    note = {
        "url": args.url,
        "username": args.username,
        "email": args.email or args.username,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "pending",
    }
    key = f"{args.domain}/{args.username}"
    # "--" stops bws from reading a password that starts with "-" as an option.
    bws("secret", "create", "--note", json.dumps(note), "--", key, secrets.token_urlsafe(24), project_id())
    emit(summary(require(args.domain, args.username)))


def cmd_set_password(args):
    # Only the newline that echo adds goes: spaces can be part of the password the site accepted.
    password = sys.stdin.read().removesuffix("\n").removesuffix("\r")
    if not password:
        fail("pipe the new password to stdin")
    # The pending check runs after stdin is read, so a login activated while stdin waits stays unchanged.
    entry = require_pending(args.domain, args.username)
    bws("secret", "edit", f"--value={password}", entry["id"])
    emit(summary(entry))


def cmd_activate(args):
    entry = require_pending(args.domain, args.username)
    note = {**meta(entry), "status": "active"}
    bws("secret", "edit", f"--note={json.dumps(note)}", entry["id"])
    emit(summary(require(args.domain, args.username)))


def cmd_delete(args):
    entry = require_pending(args.domain, args.username)
    bws("secret", "delete", entry["id"])
    emit({"login": entry["key"], "deleted": True})


def parser():
    root = argparse.ArgumentParser(prog="login.py", description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    def command(name, handler, help_text, *positionals):
        sub = commands.add_parser(name, help=help_text)
        for positional in positionals:
            sub.add_argument(positional)
        sub.set_defaults(handler=handler)
        return sub

    command("get", cmd_get, "Show one login, with its password", "domain", "username")
    command("list", cmd_list, "Show logins without passwords").add_argument("domain", nargs="?")
    command("new", cmd_new, "Store a random password as pending", "domain", "username", "url").add_argument(
        "email", nargs="?", help="Defaults to the username"
    )
    command("set-password", cmd_set_password, "Replace the password of a pending login with the one on stdin", "domain", "username")
    command("activate", cmd_activate, "Mark a pending login as active", "domain", "username")
    command("delete", cmd_delete, "Delete a pending login", "domain", "username")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    args.handler(args)


if __name__ == "__main__":
    main()
