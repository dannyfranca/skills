"""Events: read, find free time, create, change, move, delete, and answer invitations."""

import uuid
from datetime import datetime, time, timedelta

from . import recurrence, shape
from .api import CALENDAR, quote
from .errors import NOT_FOUND, GcalError, approval, bad_input
from .times import elapsed, from_api, later, parse_duration, parse_moment, parse_reminders, pin, shift, show, start_of, to_api, zone

SCOPES = ("instance", "following", "all")
# Changes to these fields stay in the copy of the user, so they email nobody.
PERSONAL_FIELDS = {"reminders", "colorId"}
COPIED_FIELDS = (
    "summary",
    "description",
    "location",
    "reminders",
    "colorId",
    "transparency",
    "visibility",
    "conferenceData",
    "guestsCanModify",
    "guestsCanInviteOthers",
    "guestsCanSeeOtherGuests",
)
DEFAULT_DURATION = "1h"
# An instance with its own value in one of these has changes that a split would lose.
# Each instance has its own value of these keys, also when nobody changed it. Attendees differ in their answers only.
PER_INSTANCE = frozenset(
    ("id", "etag", "htmlLink", "kind", "start", "end", "originalStartTime", "recurringEventId", "recurrence",
     "sequence", "created", "updated", "status", "attendees")
)
# The guest fields that belong to the series; the answer and comment of a guest differ per instance.
GUEST_FIELDS = ("email", "optional", "resource", "additionalGuests")
NOTIFY_FLAG = "--notify all (or --notify none for no email)"


class Events:
    def __init__(self, api, account, calendars):
        self.api, self.account, self.calendars = api, account, calendars

    # Reading

    def agenda(self, start=None, end=None, calendars=None, query=None, limit=None, body=False):
        tz = self.account.tz
        begin = start_of(parse_moment(start or "today", tz), tz)
        finish = start_of(parse_moment(end, tz), tz) if end else pin(begin.astimezone(tz) + timedelta(days=7), tz)
        if finish <= begin:
            raise bad_input("--to must be after --from")
        ids = [self.calendars.resolve(name) for name in calendars] if calendars else self.calendars.shown()
        items = []
        for calendar_id in ids:
            found = self.api.pages(
                self._url(calendar_id),
                limit=limit,
                singleEvents="true",
                orderBy="startTime",
                timeMin=show(begin),
                timeMax=show(finish),
                timeZone=self.account.tz_name,
                q=query,
                maxResults=250,
            )
            items += [self._shape(item, calendar_id, body) for item in found if item.get("status") != "cancelled"]
        items.sort(key=lambda item: start_of(parse_moment(item["start"], tz), tz))
        return items[:limit] if limit else items

    def search(self, query, start=None, end=None, calendars=None, limit=None, body=False):
        return self.agenda(start or "-30d", end or "+365d", calendars, query, limit, body)

    def get(self, ref, body=False):
        calendar_id, item = self._load(ref)
        master = self._master(calendar_id, item)
        return self._shape(item, calendar_id, body, master.get("recurrence") if master else None)

    def colors(self):
        palette = self.api.get(f"{CALENDAR}/colors").get("event", {})
        return [{"color": shape.EVENT_COLORS.get(key, key), "hex": value.get("background")} for key, value in palette.items()]

    def freebusy(self, start=None, end=None, who=None, calendars=None, slots=None, hours="09:00-17:00", weekends=False):
        tz = self.account.tz
        begin = start_of(parse_moment(start or "now", tz), tz)
        finish = start_of(parse_moment(end, tz), tz) if end else pin(begin.astimezone(tz) + timedelta(days=7), tz)
        chosen = [self.calendars.resolve(name) for name in calendars or []] or self.calendars.owned()
        # Time with another person must also be free for the user, so --who adds to the calendars.
        ids = list(dict.fromkeys([*chosen, *(who or [])]))
        answer = self.api.request(
            "POST",
            f"{CALENDAR}/freeBusy",
            body={"timeMin": show(begin), "timeMax": show(finish), "timeZone": self.account.tz_name, "items": [{"id": i} for i in ids]},
        )
        results, busy, unknown = [], [], False
        for calendar_id, info in answer.get("calendars", {}).items():
            errors = [error.get("reason") for error in info.get("errors", [])]
            periods = [(from_api({"dateTime": p["start"]}, tz), from_api({"dateTime": p["end"]}, tz)) for p in info.get("busy", [])]
            busy += periods
            line = {"calendar": calendar_id}
            if errors:
                unknown = True
                line["error"] = ", ".join(errors) + " (the owner does not share free/busy with you)"
            if not slots:
                line["busy"] = [{"start": show(a), "end": show(b)} for a, b in periods]
                results.append(line)
            elif errors:
                results.append(line)
        if slots and unknown:
            results.append({"slots": "withheld: the busy time of a calendar above is unknown", "hint": "ask without that calendar or person"})
        elif slots:
            results += free_slots(begin, finish, busy, parse_duration(slots), hours, weekends, tz)
        return results

    # Writing

    def create(self, title, start, end=None, duration=None, calendar=None, guests=None, notify=None, **fields):
        calendar_id = self.calendars.resolve(calendar) if calendar else "primary"
        tz = self.account.tz
        begin = parse_moment(start, tz)
        body = {"summary": title, **self._times(begin, end, duration)}
        body.update(self._fields({}, begin, add_guests=guests, **fields))
        if guests and notify is None:
            raise approval(f"this emails an invitation to {', '.join(guests)}", NOTIFY_FLAG)
        created = self.api.request("POST", self._url(calendar_id), self._params(notify, body), body)
        return self._shape(created, calendar_id)

    def edit(self, ref, scope=None, notify=None, start=None, end=None, duration=None, **fields):
        calendar_id, item = self._load(ref)
        master = self._master(calendar_id, item)
        scope = self._scope(item, master, scope)
        if scope == "following" and _is_first(item, master, self.account.tz):
            scope = "all"
        base = master if scope in ("all", "following") else item
        anchor = from_api(base["start"], self.account.tz)
        changes = self._fields(base, anchor, **fields)
        times = self._new_times(item, start, end, duration)
        if not changes and not times:
            raise bad_input("nothing to change", "add a field to change, for example --title or --start")
        if "recurrence" in changes and scope == "instance":
            raise bad_input("one instance cannot get its own repeat rule", "use --scope following or --scope all")
        # A split makes a new series for the guests, so even a color change there is not personal.
        split = {"recurrence": True} if scope == "following" else {}
        self._check_notify(base, calendar_id, {**changes, **times, **split}, notify, "this change")
        if scope in (None, "instance"):
            updated = self.api.request("PATCH", self._url(calendar_id, item["id"]), self._params(notify, changes), {**changes, **times})
            return self._shape(updated, calendar_id)
        if scope == "all":
            changes.update(self._series_times(item, master, times, bool(start), "recurrence" in changes))
            updated = self.api.request("PATCH", self._url(calendar_id, master["id"]), self._params(notify, changes), changes)
            return self._shape(updated, calendar_id)
        recurrence.check_split(master.get("recurrence") or [])
        return self._split(calendar_id, item, master, changes, times, notify, bool(start), "recurrence" in changes)

    def move(self, ref, destination, notify=None, scope=None):
        calendar_id, item = self._load(ref)
        master = self._master(calendar_id, item)
        if master and self._scope(item, master, scope) != "all":
            raise bad_input("Google moves a repeating event only as a whole series", "add --scope all")
        target = master or item
        destination_id = self.calendars.resolve(destination)
        self._check_notify(target, calendar_id, {"organizer": True}, notify, "moving this event")
        moved = self.api.request(
            "POST",
            self._url(calendar_id, target["id"], "/move"),
            {"destination": destination_id, "sendUpdates": notify},
        )
        return self._shape(moved, destination_id)

    def delete(self, ref, scope=None, yes=False, notify=None):
        calendar_id, item = self._load(ref)
        master = self._master(calendar_id, item)
        scope = self._scope(item, master, scope)
        if scope == "following" and _is_first(item, master, self.account.tz):
            scope = "all"
        if scope == "following":
            recurrence.check_split(master.get("recurrence") or [])
        target = master if scope in ("all", "following") else item
        missing = [] if yes else ["--yes"]
        if shape.guests(target, calendar_id, self.account.email) and notify is None:
            missing.append(NOTIFY_FLAG)
        if missing:
            # The title can come from another person, and errors have no untrusted wrapper.
            raise approval(f"this deletes {shape.ref(self.calendars.real_id(calendar_id), target['id'])} ({scope or 'single event'})",
                           " ".join(missing))
        params = {"sendUpdates": notify}
        if scope == "following":
            rules = recurrence.truncate(master["recurrence"], recurrence.until_before(item["originalStartTime"], self.account.tz))
            self.api.request("PATCH", self._url(calendar_id, master["id"]), params, {"recurrence": rules})
        else:
            self.api.request("DELETE", self._url(calendar_id, target["id"]), params)
        return {"deleted": shape.ref(self.calendars.real_id(calendar_id), target["id"]), "scope": scope or "single"}

    def rsvp(self, ref, answer, notify=None, scope=None):
        if answer not in shape.RESPONSE_VALUES:
            raise bad_input(f"unknown answer: {answer}", "use yes, no, or maybe")
        calendar_id, item = self._load(ref)
        master = self._master(calendar_id, item)
        scope = self._scope(item, master, scope)
        if scope == "following":
            raise bad_input("an answer goes to one instance or to the whole series", "use --scope instance or --scope all")
        target = master if scope == "all" else item
        attendees = [dict(a) for a in target.get("attendees") or []]
        mine = shape.my_attendee(attendees, self.calendars.real_id(calendar_id), self.account.email)
        if not mine:
            raise bad_input("you are not a guest of this event")
        if notify is None:
            raise approval(f"this answers {answer} to {(target.get('organizer') or {}).get('email')}", NOTIFY_FLAG)
        mine["responseStatus"] = shape.RESPONSE_VALUES[answer]
        updated = self.api.request("PATCH", self._url(calendar_id, target["id"]), {"sendUpdates": notify}, {"attendees": attendees})
        return self._shape(updated, calendar_id)

    # Helpers

    def _url(self, calendar_id, event_id=None, suffix=""):
        url = f"{CALENDAR}/calendars/{quote(calendar_id)}/events"
        return url + (f"/{quote(event_id)}{suffix}" if event_id else "")

    def _load(self, ref):
        calendar, event_id = shape.split_ref(ref, "primary")
        calendar_id = self.calendars.resolve(calendar)
        item = self.api.get(self._url(calendar_id, event_id))
        # Google still answers for a deleted event, but with only its id and status.
        if item.get("status") == "cancelled":
            raise GcalError(NOT_FOUND, f"not found: {ref}", "the event is deleted; list the events again")
        return calendar_id, item

    def _master(self, calendar_id, item):
        if item.get("recurrence"):
            return item
        if item.get("recurringEventId"):
            return self.api.get(self._url(calendar_id, item["recurringEventId"]))
        return None

    def _scope(self, item, master, scope):
        if master is None:
            return None
        if master is item:
            if scope not in (None, "all"):
                raise bad_input("this ref is the whole series", "use a ref of one instance from `agenda`, or --scope all")
            return "all"
        if scope not in SCOPES:
            raise bad_input("this event repeats", "add --scope instance, --scope following, or --scope all")
        return scope

    def _shape(self, item, calendar_id, body=False, recurrence=None):
        real = self.calendars.real_id(calendar_id)
        return shape.event(item, real, self.calendars.name(real), self.account.email, self.account.tz, body, recurrence,
                           self.calendars.only_mine(real))

    def _params(self, notify, body):
        return {"sendUpdates": notify, "conferenceDataVersion": 1 if "conferenceData" in body else None}

    def _check_notify(self, base, calendar_id, changes, notify, action):
        if notify is not None or set(changes) <= PERSONAL_FIELDS:
            return
        emails = [g.get("email") for g in shape.guests(base, calendar_id, self.account.email)]
        emails += [a["email"] for a in changes.get("attendees", []) if a["email"] not in emails and a["email"] != self.account.email]
        if emails:
            raise approval(f"{action} emails {', '.join(emails)}", NOTIFY_FLAG)

    def _times(self, begin, end, duration):
        tz, name = self.account.tz, self.account.tz_name
        if not isinstance(begin, datetime):
            if duration:
                raise bad_input("an all-day event takes --end (the last day), not --duration")
            last = parse_moment(end, tz) if end else begin
            if isinstance(last, datetime) or last < begin:
                raise bad_input("--end of an all-day event is a date on or after --start")
            return {"start": to_api(begin, name), "end": to_api(last + timedelta(days=1), name)}
        if end:
            finish = parse_moment(end, tz)
            if not isinstance(finish, datetime):
                raise bad_input("--end of a timed event is a time", "for example 2026-11-12T15:00")
        else:
            finish = later(begin, parse_duration(duration or DEFAULT_DURATION), tz)
        if elapsed(begin, finish) <= timedelta(0):
            raise bad_input("the event must end after it starts")
        return {"start": to_api(begin, name), "end": to_api(finish, name)}

    def _new_times(self, item, start, end, duration):
        if not (start or end or duration):
            return {}
        tz = self.account.tz
        old_start, old_end = from_api(item["start"], tz), from_api(item["end"], tz)
        begin = parse_moment(start, tz) if start else old_start
        if end or duration:
            return self._times(begin, end, duration)
        if isinstance(begin, datetime) != isinstance(old_start, datetime):
            return self._times(begin, None, None)
        return {"start": to_api(begin, self.account.tz_name), "end": to_api(later(begin, elapsed(old_start, old_end), tz), self.account.tz_name)}

    def _series_times(self, item, series, times, moved, new_rule):
        """Move a series so that this instance lands on the new time; keep the time zone of the series."""
        if not times:
            return {}
        # Google repeats a series on the wall clock of its own time zone, so the move is measured there.
        own_zone = series["start"].get("timeZone")
        tz = zone(own_zone) if own_zone else self.account.tz
        anchor = from_api(item.get("originalStartTime") or item["start"], tz)
        begin, finish = from_api(times["start"], tz), from_api(times["end"], tz)
        if isinstance(anchor, datetime) != isinstance(begin, datetime):
            raise bad_input("a whole series cannot switch between all-day and timed", "use --scope instance")
        # An instance can sit off the pattern of its series, so only an explicit --start may move the series.
        # The series moves by the wall-clock difference, so each instance keeps its new hour across a clock change.
        wall = begin.replace(tzinfo=None) - anchor.replace(tzinfo=None) if isinstance(begin, datetime) else begin - anchor
        new_start = shift(series["start"], wall, tz) if moved else series["start"]
        result = {"start": new_start, "end": shift(new_start, elapsed(begin, finish), tz, real_time=True)}
        rules = series.get("recurrence") or []
        # A new --repeat is the rule for the new time, so it stays as the user gave it.
        new_rules = recurrence.moved(rules, anchor, begin, tz) if moved and not new_rule else rules
        return {**result, "recurrence": new_rules} if new_rules != rules else result

    def _fields(self, base, anchor, title=None, description=None, location=None, add_guests=None, remove_guests=None,
                meet=False, repeat=None, until=None, count=None, remind=None, color=None, show_as=None):
        changes = {}
        for key, value in (("summary", title), ("description", description), ("location", location)):
            if value is not None:
                changes[key] = value
        if remind is not None:
            overrides = parse_reminders(remind)
            changes["reminders"] = {"useDefault": True} if overrides is None else {"useDefault": False, "overrides": overrides}
        if color is not None:
            if color != "default" and color not in shape.COLOR_IDS:
                raise bad_input(f"unknown color: {color}", f"use one of {', '.join(shape.COLOR_IDS)}, or default")
            changes["colorId"] = shape.COLOR_IDS.get(color)
        if show_as is not None:
            changes["transparency"] = {"busy": "opaque", "free": "transparent"}[show_as]
        if meet:
            changes["conferenceData"] = {
                "createRequest": {"requestId": uuid.uuid4().hex, "conferenceSolutionKey": {"type": "hangoutsMeet"}}
            }
        if add_guests or remove_guests:
            changes["attendees"] = _guest_list(base.get("attendees") or [], add_guests or [], remove_guests or [])
        if repeat is not None or until or count:
            changes["recurrence"] = recurrence.build(repeat, until, count, anchor, base.get("recurrence") or [], self.account.tz)
        return changes

    def _split(self, calendar_id, item, master, changes, times, notify, moved, new_rule):
        """Scope "following": a new series from this instance on, then the old series ends before it."""
        tz = self.account.tz
        original = item["originalStartTime"]
        series = {key: master[key] for key in COPIED_FIELDS if key in master}
        series["attendees"] = [
            {key: a[key] for key in GUEST_FIELDS if a.get(key)} for a in master.get("attendees") or []
        ]
        length = elapsed(from_api(master["start"], tz), from_api(master["end"], tz))
        start = dict(original)
        if "dateTime" in start and master["start"].get("timeZone"):
            start["timeZone"] = master["start"]["timeZone"]
        instances = self.api.pages(self._url(calendar_id, master["id"], "/instances"), showDeleted="true")
        changed = _changed_later(instances, master, from_api(original, tz), tz)
        if changed:
            raise bad_input(
                f"{len(changed)} instances from this one on have their own changes, and a split would lose them",
                "use --scope all, or --scope instance for each instance",
            )
        series.update(start=start, end=shift(start, length, tz, real_time=True), recurrence=recurrence.remaining(master["recurrence"], _done(instances, from_api(original, tz), tz)))
        series.update(changes)
        series.update(self._series_times(item, series, times, moved, new_rule))
        created = self.api.request("POST", self._url(calendar_id), self._params(notify, series), series)
        rules = recurrence.truncate(master["recurrence"], recurrence.until_before(original, tz))
        try:
            self.api.request("PATCH", self._url(calendar_id, master["id"]), {"sendUpdates": notify}, {"recurrence": rules})
        except GcalError as error:
            if not self._split_saved(calendar_id, master, rules, created, notify, error):
                raise
        result = self._shape(created, calendar_id)
        result["note"] = "the series is now two series: the old one ends before this instance"
        return result

    def _split_saved(self, calendar_id, master, rules, created, notify, error):
        """After a failed end of the old series: True if Google saved it after all, else remove the new series.

        Google can save a change and still fail to answer. Two full series would overlap, and a retry would add a third.
        """
        real = self.calendars.real_id(calendar_id)
        new_ref, old_ref = shape.ref(real, created["id"]), shape.ref(real, master["id"])
        try:
            if self.api.get(self._url(calendar_id, master["id"])).get("recurrence") == rules:
                return True
            self.api.request("DELETE", self._url(calendar_id, created["id"]), {"sendUpdates": notify})
        except GcalError:
            raise GcalError(
                error.code,
                f"{error.message}; the split is maybe half done: the new series is {new_ref}, the old series is {old_ref}",
                f"get both refs: if the old series did not end, delete {new_ref} with --scope all, then try again",
            ) from error
        return False


def _place(instance, tz):
    return from_api(instance.get("originalStartTime") or instance["start"], tz)


def _changed_later(instances, master, cutoff, tz):
    length = elapsed(from_api(master["start"], tz), from_api(master["end"], tz))
    return [
        i for i in instances
        if _place(i, tz) >= cutoff and (
            i.get("status") == "cancelled"
            or from_api(i["start"], tz) != _place(i, tz)
            or elapsed(from_api(i["start"], tz), from_api(i["end"], tz)) != length
            or any(i.get(key) != master.get(key) for key in (i.keys() | master.keys()) - PER_INSTANCE)
            or _guests(i) != _guests(master)
        )
    ]


def _guests(item):
    """Guests as a split copies them; RSVP answers differ per instance and stay out."""
    return {
        (a.get("email", "").casefold(), bool(a.get("optional")), bool(a.get("resource")), a.get("additionalGuests") or 0)
        for a in item.get("attendees") or []
    }


def _done(instances, cutoff, tz):
    """Instances before the cutoff; an instance counts by its place in the pattern, so a moved instance keeps its place."""
    return sum(1 for i in instances if _place(i, tz) < cutoff)


def _is_first(item, master, tz):
    return from_api(item["originalStartTime"], tz) == from_api(master["start"], tz)


def _guest_list(current, add, remove):
    removed = {email.casefold() for email in remove}
    known = {a.get("email", "").casefold() for a in current}
    missing = removed - known
    if missing:
        raise bad_input(f"not a guest: {', '.join(sorted(missing))}")
    kept = [a for a in current if a.get("email", "").casefold() not in removed]
    return kept + [{"email": email} for email in add if email.casefold() not in known]


def free_slots(begin, finish, busy, length, hours, weekends, tz):
    try:
        first, last = (time.fromisoformat(part) for part in hours.split("-"))
    except ValueError as error:
        raise bad_input(f"not working hours: {hours}", "use for example 09:00-17:00") from error
    busy = sorted(busy)
    slots, day = [], begin.date()
    while day <= finish.date():
        if weekends or day.weekday() < 5:
            cursor = max(begin, pin(datetime.combine(day, first, tzinfo=tz), tz))
            close = min(finish, pin(datetime.combine(day, last, tzinfo=tz), tz))
            for busy_start, busy_end in busy:
                if busy_end <= cursor or busy_start >= close:
                    continue
                if busy_start - cursor >= length:
                    slots.append((cursor, busy_start))
                cursor = max(cursor, busy_end)
            if close - cursor >= length:
                slots.append((cursor, close))
        day += timedelta(days=1)
    return [{"start": show(a), "end": show(b), "minutes": int((b - a).total_seconds() // 60)} for a, b in slots]
