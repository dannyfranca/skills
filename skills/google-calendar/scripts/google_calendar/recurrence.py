"""The repeat rules of a series (RFC 5545 RRULE, RDATE, and EXDATE lines): build, end, split, and move them."""

import re
from datetime import datetime, time, timedelta, timezone

from .errors import bad_input
from .times import from_api, parse_moment

REPEATS = {
    "daily": "RRULE:FREQ=DAILY",
    "weekdays": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "weekly": "RRULE:FREQ=WEEKLY",
    "biweekly": "RRULE:FREQ=WEEKLY;INTERVAL=2",
    "monthly": "RRULE:FREQ=MONTHLY",
    "yearly": "RRULE:FREQ=YEARLY",
}
# A rule part such as BYDAY=MO pins the series to a day or hour, so a move must give a new rule.
DAY_PARTS = ("BYDAY", "BYMONTHDAY", "BYYEARDAY", "BYWEEKNO", "BYMONTH", "BYSETPOS")
HOUR_PARTS = ("BYHOUR", "BYMINUTE", "BYSECOND")
UTC_STAMP = "%Y%m%dT%H%M%SZ"


def build(repeat, until, count, anchor, current, tz):
    """The rules after --repeat, --until, and --count; current is the rule list of the series, if any."""
    if repeat == "none":
        return []
    if repeat is None:
        rules = [r for r in current if _is_rrule(r)]
        if not rules:
            raise bad_input("--until and --count need --repeat")
        rule = rules[0]
        others = [r for r in current if r != rule]
    else:
        rule = repeat if _is_rrule(repeat) else REPEATS.get(repeat)
        others = []
        if not rule:
            raise bad_input(f"unknown repeat: {repeat}", f"use {', '.join(REPEATS)}, none, or an RRULE:... line")
    if until and count:
        raise bad_input("give --until or --count, not both")
    rule = re.sub(r";?(UNTIL|COUNT)=[^;]*", "", rule, flags=re.I) if (until or count) else rule
    if until:
        last = parse_moment(until, tz)
        last = last.date() if isinstance(last, datetime) else last
        if isinstance(anchor, datetime):
            end_of_day = datetime.combine(last, time(23, 59, 59), tzinfo=tz)
            rule += ";UNTIL=" + end_of_day.astimezone(timezone.utc).strftime(UTC_STAMP)
        else:
            rule += ";UNTIL=" + last.strftime("%Y%m%d")
    if count:
        rule += f";COUNT={int(count)}"
    return [rule, *others]


def until_before(original_start, tz):
    """The UNTIL value that ends a series just before the given instance."""
    moment = from_api(original_start, tz)
    if isinstance(moment, datetime):
        # RFC 5545 needs UNTIL in UTC when the series start has a time zone.
        return (moment - timedelta(seconds=1)).astimezone(timezone.utc).strftime(UTC_STAMP)
    return (moment - timedelta(days=1)).strftime("%Y%m%d")


def truncate(rules, until):
    """End every RRULE at until; COUNT goes, because RFC 5545 allows only one of the two."""
    return [_with_parts(rule, lambda part: not part.upper().startswith(("UNTIL=", "COUNT=")), f"UNTIL={until}") for rule in rules]


def remaining(rules, done):
    """The rules for the part of a series after its first done instances."""
    result = []
    for rule in rules:
        counted = re.search(r"COUNT=(\d+)", rule, re.I)
        if _is_rrule(rule) and counted:
            rule = re.sub(r"COUNT=\d+", f"COUNT={max(int(counted.group(1)) - done, 1)}", rule, flags=re.I)
        result.append(rule)
    return result


def check_split(rules):
    upper = [rule.upper() for rule in rules]
    # UNTIL does not stop RDATE dates, so a split would keep them in the old series and copy them into the new one.
    # An EXDATE date uses a place of COUNT but has no instance, so the new COUNT would be wrong.
    counted = any(_is_rrule(rule) and "COUNT=" in rule for rule in upper)
    if any(rule.startswith("RDATE") for rule in upper) or (counted and any(rule.startswith("EXDATE") for rule in upper)):
        raise bad_input("this series has dates outside its rule (RDATE or EXDATE), so it cannot split",
                        "use --scope instance or --scope all, or change it in Google Calendar")


def moved(rules, anchor, begin, tz):
    """The rules of a series whose instance at anchor moves to begin, on the wall clock of tz.

    The last instance (UNTIL) moves with the series. Rule parts and extra dates that fix a day or an hour cannot move
    with it, so they stop the move.
    """
    if begin == anchor:
        return rules
    timed = isinstance(begin, datetime)
    days = (begin.date() if timed else begin) - (anchor.date() if timed else anchor)
    new_hour = timed and begin.time() != anchor.time()
    parts = {part.split("=")[0] for rule in rules if _is_rrule(rule) for part in _parts(rule)}
    pinned = sorted(part for part in parts if (days and part in DAY_PARTS) or (new_hour and part in HOUR_PARTS))
    # RDATE and EXDATE list exact instances, and a move would leave them on the old times.
    pinned += sorted({rule.split(":")[0].split(";")[0].upper() for rule in rules if rule.upper().startswith(("RDATE", "EXDATE"))})
    if pinned:
        raise bad_input(
            f"the repeat rules fix {', '.join(pinned)}, so the series cannot move to this time",
            "add --repeat with a rule for the new time, or use --scope instance",
        )
    wall = begin.replace(tzinfo=None) - anchor.replace(tzinfo=None) if timed else days
    return [_with_parts(rule, lambda part: True, None, lambda part: _move_until(part, wall, days, tz)) for rule in rules]


def _move_until(part, wall, days, tz):
    name, _, value = part.partition("=")
    if name.upper() != "UNTIL":
        return part
    if len(value) == 8:
        last = datetime.strptime(value, "%Y%m%d").date() + days
        return f"{name}={last.strftime('%Y%m%d')}"
    if value.upper().endswith("Z"):
        local = datetime.strptime(value.upper(), UTC_STAMP).replace(tzinfo=timezone.utc).astimezone(tz)
        last = (local.replace(tzinfo=None) + wall).replace(tzinfo=tz)
        return f"{name}={last.astimezone(timezone.utc).strftime(UTC_STAMP)}"
    return f"{name}={(datetime.strptime(value, '%Y%m%dT%H%M%S') + wall).strftime('%Y%m%dT%H%M%S')}"


def _is_rrule(rule):
    return rule.upper().startswith("RRULE:")


def _parts(rule):
    return [part for part in rule.partition(":")[2].upper().split(";") if part]


def _with_parts(rule, keep, extra=None, change=lambda part: part):
    if not _is_rrule(rule):
        return rule
    head, _, body = rule.partition(":")
    parts = [change(part) for part in body.split(";") if part and keep(part)]
    return f"{head}:{';'.join([*parts, *([extra] if extra else [])])}"
