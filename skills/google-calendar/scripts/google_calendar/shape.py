"""Turn API events and tasks into the flat output items that the agent reads."""

from datetime import datetime, timedelta

from .times import from_api, show, show_reminders
from .untrusted import clean, clean_line, guard

EVENT_COLORS = {
    "1": "lavender",
    "2": "sage",
    "3": "grape",
    "4": "flamingo",
    "5": "banana",
    "6": "tangerine",
    "7": "peacock",
    "8": "graphite",
    "9": "blueberry",
    "10": "basil",
    "11": "tomato",
}
COLOR_IDS = {name: key for key, name in EVENT_COLORS.items()}
RESPONSES = {"needsAction": "none", "accepted": "yes", "declined": "no", "tentative": "maybe"}
RESPONSE_VALUES = {"yes": "accepted", "no": "declined", "maybe": "tentative"}


def ref(container_id, item_id):
    return f"{container_id}:{item_id}"


def split_ref(text, default):
    """(container id, item id). IDs from Google have no ":", so the last ":" separates the two."""
    container, _, item = text.rpartition(":")
    return (container or default), item


def is_own(event, account_email, calendar_id):
    # The "self" flags describe the calendar that holds this copy, not the user, so compare the email.
    if event.get("guestsCanModify") and event.get("attendees"):
        # Guests can then change the text, and Google keeps the user as creator.
        return False
    creator = (event.get("creator") or {}).get("email", "")
    # A moved event keeps its creator, but its organizer can change the text of all copies.
    return creator.casefold() == account_email.casefold() and is_organizer(event, calendar_id, account_email)


def guests(event, calendar_id, account_email):
    me = {account_email.casefold(), calendar_id.casefold()}
    return [
        attendee
        for attendee in event.get("attendees") or []
        if not attendee.get("resource") and attendee.get("email", "").casefold() not in me
    ]


def my_attendee(attendees, calendar_id, account_email):
    """The attendee that is the user, or None.

    "self" marks the attendee of the calendar that holds this copy, so it names the user only on the primary calendar.
    There it also finds the user when the invitation went to an alias.
    """
    me = account_email.casefold()
    primary = calendar_id.casefold() == me
    return next((a for a in attendees if a.get("email", "").casefold() == me or (primary and a.get("self"))), None)


def is_organizer(event, calendar_id, account_email):
    organizer = (event.get("organizer") or {}).get("email", "").casefold()
    return organizer in (account_email.casefold(), calendar_id.casefold())


def meet_link(event):
    for point in (event.get("conferenceData") or {}).get("entryPoints") or []:
        if point.get("entryPointType") == "video":
            return point.get("uri")
    return event.get("hangoutLink")


def times(event, tz):
    start, end = from_api(event["start"], tz), from_api(event["end"], tz)
    if not isinstance(start, datetime):
        # The API end of an all-day event is the day after; people say the last day.
        end = end - timedelta(days=1)
    return start, end


def event(item, calendar_id, calendar_name, account_email, tz, with_body=False, recurrence=None, own_calendar=True):
    # The name of a shared calendar comes from its owner, and its writers can change any event in it.
    own = own_calendar and is_own(item, account_email, calendar_id)
    start, end = times(item, tz)
    others = guests(item, calendar_id, account_email)
    mine = my_attendee(item.get("attendees") or [], calendar_id, account_email)
    reminders = item.get("reminders") or {}
    result = {
        "ref": ref(calendar_id, item["id"]),
        "calendar": calendar_name,
        "title": clean_line(item.get("summary") or "(no title)"),
        "start": show(start),
        "end": show(end),
        "all_day": "date" in item["start"],
        "status": item.get("status"),
        "organizer": (item.get("organizer") or {}).get("email"),
        "own": own,
        "location": clean_line(item["location"]) if item.get("location") else None,
        "guests": [{"email": g.get("email"), "response": RESPONSES.get(g.get("responseStatus"), "none")} for g in others],
        "my_response": RESPONSES.get(mine.get("responseStatus")) if mine else None,
        "meet": meet_link(item),
        "recurring": bool(item.get("recurringEventId") or item.get("recurrence")),
        "recurrence": recurrence or item.get("recurrence"),
        "color": EVENT_COLORS.get(item.get("colorId")),
        "reminders": "default" if reminders.get("useDefault", True) else show_reminders(reminders.get("overrides")),
        "show_as": "free" if item.get("transparency") == "transparent" else "busy",
        "link": item.get("htmlLink"),
        "updated": item.get("updated"),
    }
    if with_body:
        _add_body(result, item.get("description"), own)
    return {key: value for key, value in result.items() if value not in (None, [], "")}


def task(item, list_id, list_name, tz, with_body=False):
    # Tasks from Docs or Chat carry assignmentInfo: another person wrote them.
    own = "assignmentInfo" not in item
    result = {
        "ref": ref(list_id, item["id"]),
        "list": list_name,
        "title": clean_line(item.get("title") or "(no title)"),
        "status": "done" if item.get("status") == "completed" else "open",
        "due": item["due"][:10] if item.get("due") else None,
        "parent": ref(list_id, item["parent"]) if item.get("parent") else None,
        "completed": show(from_api({"dateTime": item["completed"]}, tz)) if item.get("completed") else None,
        "own": own,
        "updated": item.get("updated"),
    }
    if own:
        result["notes"] = clean(item.get("notes")) or None
    elif with_body:
        _add_body(result, item.get("notes"), own, key="notes")
    return {key: value for key, value in result.items() if value not in (None, [], "")}


def _add_body(result, text, own, key="description"):
    text = clean(text)
    if own or not text:
        result[key] = text
        return
    verdict = guard(f"{result['title']}\n\n{text}")
    if verdict in (None, "safe"):
        result[key] = text
        result["text_status"] = f"shown (guard: {verdict or 'off'})"
    else:
        result["text_status"] = "withheld: guard" if verdict == "flagged" else "withheld: guard unavailable"
