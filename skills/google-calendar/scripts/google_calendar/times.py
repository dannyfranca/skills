"""Turn command-line time words into API times, in the time zone of the Google account."""

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import bad_input

DURATION = re.compile(r"(\d+)\s*([wdhm])", re.I)
RELATIVE = re.compile(r"^([+-])\s*((?:\d+\s*[wdhm]\s*)+)$", re.I)
UNITS = {"w": "weeks", "d": "days", "h": "hours", "m": "minutes"}
TIME_FORMAT_HINT = "use an ISO date (2026-11-12), an ISO time (2026-11-12T14:00), now, today, tomorrow, or +2h / -3d / +1w"


def zone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise bad_input(f"unknown time zone: {name}") from error


def pin(moment, tz):
    """The moment in the zone tz, with the fixed offset of that moment.

    Python compares and subtracts two datetimes of one ZoneInfo by the wall clock. In the repeated hour of a clock
    change, two different moments then look equal. A fixed offset makes each datetime one exact moment.
    """
    local = moment.astimezone(tz)
    return local.replace(tzinfo=timezone(local.utcoffset()), fold=0)


def parse_duration(text):
    text = (text or "").strip()
    parts = DURATION.findall(text)
    if not parts or DURATION.sub("", text).strip():
        raise bad_input(f"not a duration: {text}", "use for example 30m, 1h, 1h30m, 2d, or 1w")
    return timedelta(**{UNITS[unit.lower()]: int(amount) for amount, unit in parts})


def parse_moment(text, tz, now=None):
    """A date for a whole day, or an aware datetime. Times without an offset are in the zone tz."""
    # Offsets such as +1d move the wall clock, so they add in the ZoneInfo before the moment is pinned.
    now = (now or datetime.now(tz)).astimezone(tz)
    word = (text or "").strip().lower()
    if word == "now":
        return pin(now, tz)
    if word in ("today", "tomorrow", "yesterday"):
        return now.date() + timedelta(days={"today": 0, "tomorrow": 1, "yesterday": -1}[word])
    relative = RELATIVE.match(word)
    if relative:
        delta = parse_duration(relative.group(2))
        return pin(now + delta if relative.group(1) == "+" else now - delta, tz)
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", word):
            return date.fromisoformat(word)
        moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise bad_input(f"not a time: {text}", TIME_FORMAT_HINT) from error
    return pin(moment if moment.tzinfo else moment.replace(tzinfo=tz), tz)


def start_of(moment, tz):
    """An aware datetime: a date becomes 00:00 of that day."""
    if isinstance(moment, datetime):
        return moment
    return pin(datetime.combine(moment, datetime.min.time(), tzinfo=tz), tz)


def to_api(moment, tz_name):
    if isinstance(moment, datetime):
        return {"dateTime": moment.isoformat(timespec="seconds"), "timeZone": tz_name}
    return {"date": moment.isoformat()}


def later(moment, delta, tz):
    if not isinstance(moment, datetime):
        return moment + timedelta(days=delta.days)
    return pin(moment.astimezone(timezone.utc) + delta, tz)


def elapsed(begin, finish):
    if not isinstance(begin, datetime):
        return finish - begin
    return finish.astimezone(timezone.utc) - begin.astimezone(timezone.utc)


def from_api(value, tz):
    """A date for all-day values, else a pinned datetime in the zone tz."""
    if "dateTime" in value:
        return pin(datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00")), tz)
    return date.fromisoformat(value["date"])


def shift(value, delta, tz, real_time=False):
    """Move an API time value by delta and keep its own time zone field.

    real_time adds elapsed time (for a length); else delta moves the wall clock (for a new start).
    """
    moment = from_api(value, tz)
    if isinstance(moment, datetime):
        own_zone = value.get("timeZone")
        moved = later(moment, delta, tz) if real_time else pin(moment.astimezone(tz) + delta, tz)
        if own_zone:
            moved = moved.astimezone(zone(own_zone))
        return {"dateTime": moved.isoformat(timespec="seconds"), **({"timeZone": own_zone} if own_zone else {})}
    return {"date": (moment + timedelta(days=delta.days)).isoformat()}


def show(moment):
    return moment.isoformat(timespec="seconds") if isinstance(moment, datetime) else moment.isoformat()


def short_duration(minutes):
    for unit, size in (("w", 10080), ("d", 1440), ("h", 60)):
        if minutes and minutes % size == 0:
            return f"{minutes // size}{unit}"
    return f"{minutes}m"


def parse_reminders(text):
    """None for the calendar default, [] for no reminders, else a list of overrides."""
    text = (text or "").strip().lower()
    if text == "default":
        return None
    if text == "none":
        return []
    overrides = []
    for item in filter(None, (part.strip() for part in text.split(","))):
        method, _, amount = item.rpartition(":")
        method = method or "popup"
        if method not in ("popup", "email"):
            raise bad_input(f"unknown reminder method: {method}", "use popup:10m or email:1d")
        overrides.append({"method": method, "minutes": int(parse_duration(amount).total_seconds() // 60)})
    if not overrides or len(overrides) > 5:
        raise bad_input("give 1 to 5 reminders", "for example --remind 10m,1h or --remind email:1d")
    return overrides


def show_reminders(items):
    return [f"{item['method']}:{short_duration(item['minutes'])}" for item in items or []]
