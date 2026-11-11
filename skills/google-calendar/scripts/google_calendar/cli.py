"""Command line of `scripts/gcal`. Output is JSON lines on stdout; errors are JSON on stderr."""

import argparse
import json
import re
import sys
from pathlib import Path

from . import auth, setup, store
from .account import Account
from .api import Api
from .calendars import ROLES, Calendars
from .errors import GcalError
from .events import SCOPES, Events
from .shape import COLOR_IDS, RESPONSE_VALUES
from .tasks import Tasks
from .untrusted import render

NOTIFY = ("all", "none")


def build_parser():
    parser = argparse.ArgumentParser(prog="gcal", description="Google Calendar and Google Tasks for agents. See SKILL.md.")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("setup", help="interactive setup for the user, in a terminal")
    login = commands.add_parser("auth", help="log in: the user clicks Allow in the browser")
    login.add_argument("--no-browser", action="store_true", help="print the URL only")
    login.add_argument("--client", metavar="FILE", help="import a downloaded client_secret_*.json first, then delete it")
    commands.add_parser("logout", help="revoke the access and remove the tokens")
    commands.add_parser("whoami", help="the account and the time zone")

    commands.add_parser("calendars", help="list the calendars")
    calendar = commands.add_parser("calendar", help="create, edit, delete, subscribe, unsubscribe").add_subparsers(dest="action", required=True)
    create = calendar.add_parser("create")
    create.add_argument("name")
    create.add_argument("--description")
    create.add_argument("--time-zone")
    edit = calendar.add_parser("edit")
    edit.add_argument("calendar")
    edit.add_argument("--name")
    edit.add_argument("--description")
    edit.add_argument("--time-zone")
    edit.add_argument("--color", help="hex, for example #3f51b5")
    edit.add_argument("--remind", help="default reminders of new events: 10m,email:1d or none")
    shown = edit.add_mutually_exclusive_group()
    shown.add_argument("--show", dest="shown", action="store_const", const=True)
    shown.add_argument("--hide", dest="shown", action="store_const", const=False)
    delete = calendar.add_parser("delete")
    delete.add_argument("calendar")
    delete.add_argument("--confirm", help="the exact name of the calendar")
    subscribe = calendar.add_parser("subscribe")
    subscribe.add_argument("calendar_id")
    unsubscribe = calendar.add_parser("unsubscribe")
    unsubscribe.add_argument("calendar")
    unsubscribe.add_argument("--yes", action="store_true")

    share = commands.add_parser("share", help="who can see a calendar").add_subparsers(dest="action", required=True)
    shares = share.add_parser("list")
    shares.add_argument("calendar")
    grant = share.add_parser("add", help="give access, or change the role")
    grant.add_argument("calendar")
    grant.add_argument("email")
    grant.add_argument("--role", required=True, choices=ROLES)
    grant.add_argument("--group", action="store_true", help="the email is a Google group")
    grant.add_argument("--yes", action="store_true")
    revoke = share.add_parser("remove")
    revoke.add_argument("calendar")
    revoke.add_argument("email")
    revoke.add_argument("--yes", action="store_true")

    agenda = commands.add_parser("agenda", help="events in a time range")
    _range(agenda, "today", "--from + 7 days")
    agenda.add_argument("--calendar", action="append")
    agenda.add_argument("--limit", type=int)
    agenda.add_argument("--body", action="store_true", help="add descriptions")
    search = commands.add_parser("search", help="events that contain words")
    search.add_argument("query")
    _range(search, "-30d", "+365d")
    search.add_argument("--calendar", action="append")
    search.add_argument("--limit", type=int)
    search.add_argument("--body", action="store_true")
    get = commands.add_parser("get", help="one event")
    get.add_argument("ref")
    get.add_argument("--body", action="store_true")
    free = commands.add_parser("free", help="busy times, or free slots with --slots")
    _range(free, "now", "--from + 7 days")
    free.add_argument("--who", action="append", help="email of a person who shares free/busy with you")
    free.add_argument("--calendar", action="append")
    free.add_argument("--slots", help="minimum length of a free slot, for example 30m")
    free.add_argument("--hours", default="09:00-17:00")
    free.add_argument("--weekends", action="store_true")
    commands.add_parser("colors", help="event color names")

    new = commands.add_parser("create", help="new event")
    new.add_argument("title")
    new.add_argument("--start", required=True)
    new.add_argument("--calendar")
    new.add_argument("--guest", action="append", dest="guests")
    _event_fields(new)
    change = commands.add_parser("edit", help="change an event")
    change.add_argument("ref")
    change.add_argument("--scope", choices=SCOPES)
    change.add_argument("--title")
    change.add_argument("--start")
    change.add_argument("--add-guest", action="append", dest="add_guests")
    change.add_argument("--remove-guest", action="append", dest="remove_guests")
    _event_fields(change)
    move = commands.add_parser("move", help="move an event to another calendar")
    move.add_argument("ref")
    move.add_argument("--to", required=True, dest="destination")
    move.add_argument("--scope", choices=SCOPES)
    move.add_argument("--notify", choices=NOTIFY)
    remove = commands.add_parser("delete", help="delete an event")
    remove.add_argument("ref")
    remove.add_argument("--scope", choices=SCOPES)
    remove.add_argument("--yes", action="store_true")
    remove.add_argument("--notify", choices=NOTIFY)
    rsvp = commands.add_parser("rsvp", help="answer an invitation")
    rsvp.add_argument("ref")
    rsvp.add_argument("answer", choices=RESPONSE_VALUES)
    rsvp.add_argument("--scope", choices=SCOPES)
    rsvp.add_argument("--notify", choices=NOTIFY)

    task = commands.add_parser("task", help="Google Tasks, see references/tasks.md").add_subparsers(dest="action", required=True)
    task.add_parser("lists")
    new_list = task.add_parser("new-list")
    new_list.add_argument("name")
    rename = task.add_parser("rename-list")
    rename.add_argument("list")
    rename.add_argument("name")
    drop = task.add_parser("delete-list")
    drop.add_argument("list")
    drop.add_argument("--yes", action="store_true")
    show = task.add_parser("show", help="tasks of a list")
    show.add_argument("--list")
    show.add_argument("--done", action="store_true", help="add completed tasks")
    show.add_argument("--due-before")
    show.add_argument("--body", action="store_true")
    one = task.add_parser("get")
    one.add_argument("ref")
    one.add_argument("--body", action="store_true")
    add = task.add_parser("add")
    add.add_argument("title")
    add.add_argument("--list")
    add.add_argument("--notes")
    add.add_argument("--due")
    add.add_argument("--parent", help="ref of the parent task: makes a subtask")
    add.add_argument("--after", help="ref of the task before it")
    change_task = task.add_parser("edit")
    change_task.add_argument("ref")
    change_task.add_argument("--title")
    change_task.add_argument("--notes")
    change_task.add_argument("--due", help="a date, or none")
    for name in ("done", "reopen"):
        task.add_parser(name).add_argument("ref")
    move_task = task.add_parser("move")
    move_task.add_argument("ref")
    move_task.add_argument("--parent")
    move_task.add_argument("--after")
    move_task.add_argument("--top", action="store_true", help="first top-level task")
    move_task.add_argument("--list", dest="to_list")
    remove_task = task.add_parser("delete")
    remove_task.add_argument("ref")
    remove_task.add_argument("--yes", action="store_true")
    return parser


def _range(parser, start, end):
    parser.add_argument("--from", dest="start", help=f"default: {start}")
    parser.add_argument("--to", dest="end", help=f"default: {end}; the end is not included")


def _event_fields(parser):
    parser.add_argument("--end", help="time; for an all-day event, the last day")
    parser.add_argument("--duration", help="for example 30m or 1h30m (default 1h)")
    parser.add_argument("--description")
    parser.add_argument("--location")
    parser.add_argument("--meet", action="store_true", help="add a Google Meet link")
    parser.add_argument("--repeat", help="daily, weekdays, weekly, biweekly, monthly, yearly, none, or RRULE:...")
    parser.add_argument("--until", help="last day of the repeat")
    parser.add_argument("--count", type=int, help="number of repeats")
    parser.add_argument("--remind", help="10m,1h or email:1d or default or none")
    parser.add_argument("--color", choices=[*COLOR_IDS, "default"])
    parser.add_argument("--show-as", choices=("busy", "free"))
    parser.add_argument("--notify", choices=NOTIFY, help="email the guests (all) or not (none)")


EVENT_FIELDS = ("description", "location", "meet", "repeat", "until", "count", "remind", "color", "show_as")


def dispatch(args, api=None):
    if args.command == "setup":
        return setup.run()
    if args.command == "logout":
        auth.logout()
        return {"logged_out": True}
    api = api or Api()
    account = Account(api)
    if args.command == "auth":
        if args.client:
            auth.import_client(args.client)
            # The secret is now in the credentials file (mode 600); a second copy in Downloads is a leak risk.
            Path(args.client).expanduser().unlink(missing_ok=True)
        auth.login(open_url=not args.no_browser)
        facts = account.refresh()
        return {"logged_in": facts["account"], "time_zone": facts["time_zone"]}
    if args.command == "whoami":
        facts = account.refresh()
        return {"account": facts["account"], "time_zone": facts["time_zone"], "credentials": str(store.credentials_file())}
    calendars = Calendars(api, account)
    if args.command == "calendars":
        return calendars.list(), "calendar"
    if args.command == "calendar":
        return _calendar(args, calendars), "calendar"
    if args.command == "share":
        calendar_id = calendars.resolve(args.calendar)
        if args.action == "list":
            return calendars.shares(calendar_id)
        if args.action == "add":
            return calendars.share(calendar_id, args.email, args.role, args.yes, args.group)
        return calendars.unshare(calendar_id, args.email, args.yes)
    if args.command == "task":
        return _task(args, Tasks(api, account)), "task"
    return _event(args, Events(api, account, calendars)), "event"


def _calendar(args, calendars):
    if args.action == "create":
        return calendars.create(args.name, args.description, args.time_zone)
    if args.action == "subscribe":
        return calendars.subscribe(args.calendar_id)
    calendar_id = calendars.resolve(args.calendar)
    if args.action == "edit":
        return calendars.edit(calendar_id, args.name, args.description, args.time_zone, args.color, args.remind, args.shown)
    if args.action == "delete":
        return calendars.delete(calendar_id, args.confirm)
    return calendars.unsubscribe(calendar_id, args.yes)


def _event(args, events):
    fields = {name: getattr(args, name) for name in EVENT_FIELDS if hasattr(args, name)}
    command = args.command
    if command == "agenda":
        return events.agenda(args.start, args.end, args.calendar, None, args.limit, args.body)
    if command == "search":
        return events.search(args.query, args.start, args.end, args.calendar, args.limit, args.body)
    if command == "get":
        return events.get(args.ref, args.body)
    if command == "free":
        return events.freebusy(args.start, args.end, args.who, args.calendar, args.slots, args.hours, args.weekends)
    if command == "colors":
        return events.colors()
    if command == "create":
        return events.create(args.title, args.start, args.end, args.duration, args.calendar, args.guests, args.notify, **fields)
    if command == "edit":
        return events.edit(
            args.ref, args.scope, args.notify, args.start, args.end, args.duration,
            title=args.title, add_guests=args.add_guests, remove_guests=args.remove_guests, **fields,
        )
    if command == "move":
        return events.move(args.ref, args.destination, args.notify, args.scope)
    if command == "delete":
        return events.delete(args.ref, args.scope, args.yes, args.notify)
    return events.rsvp(args.ref, args.answer, args.notify, args.scope)


def _task(args, tasks):
    action = args.action
    if action == "lists":
        return tasks.show_lists()
    if action == "new-list":
        return tasks.create_list(args.name)
    if action == "rename-list":
        return tasks.rename_list(args.list, args.name)
    if action == "delete-list":
        return tasks.delete_list(args.list, args.yes)
    if action == "show":
        return tasks.tasks(args.list, args.done, args.due_before, args.body)
    if action == "get":
        return tasks.get(args.ref, args.body)
    if action == "add":
        return tasks.add(args.title, args.list, args.notes, args.due, args.parent, args.after)
    if action == "edit":
        return tasks.edit(args.ref, args.title, args.notes, args.due)
    if action == "done":
        return tasks.complete(args.ref)
    if action == "reopen":
        return tasks.reopen(args.ref)
    if action == "move":
        return tasks.move(args.ref, args.parent, args.after, args.to_list, args.top)
    return tasks.delete(args.ref, args.yes)


def _join_offsets(argv):
    # Python 3.11 argparse reads "--from -3d" as two options. "--from=-3d" works on every version.
    joined = []
    for token in argv:
        if joined and re.fullmatch(r"-\d+[a-z]*", token) and re.fullmatch(r"--[a-z-]+", joined[-1]):
            joined[-1] = f"{joined[-1]}={token}"
        else:
            joined.append(token)
    return joined


def main(argv=None, api=None):
    args = build_parser().parse_args(_join_offsets(sys.argv[1:] if argv is None else argv))
    try:
        result = dispatch(args, api)
    except GcalError as error:
        print(json.dumps(error.payload(), ensure_ascii=False), file=sys.stderr)
        return error.code
    if isinstance(result, int):
        return result
    kind = "event"
    if isinstance(result, tuple):
        result, kind = result
    output = render(result if isinstance(result, list) else [result], kind)
    if output:
        print(output)
    return 0
