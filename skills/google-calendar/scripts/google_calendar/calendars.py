"""Calendars in the list of the user, and who they are shared with."""

import re

from .api import CALENDAR, quote
from .errors import APPROVAL, NOT_FOUND, GcalError, approval, bad_input
from .times import parse_reminders, show_reminders
from .untrusted import clean, clean_line

ROLES = {"freebusy": "freeBusyReader", "reader": "reader", "writer": "writer", "owner": "owner"}
ROLE_NAMES = {value: key for key, value in ROLES.items()}
HEX_COLOR = r"#[0-9a-fA-F]{6}"


class Calendars:
    def __init__(self, api, account):
        self.api, self.account = api, account
        self._entries = None

    def entries(self):
        if self._entries is None:
            self._entries = self.api.pages(f"{CALENDAR}/users/me/calendarList", showHidden="true")
        return self._entries

    def resolve(self, name_or_id):
        """The calendar ID for a name or an ID. "primary" stays "primary"."""
        if name_or_id == "primary":
            return "primary"
        entries = self.entries()
        for entry in entries:
            if entry["id"] == name_or_id:
                return entry["id"]
        wanted = name_or_id.casefold()
        found = [entry for entry in entries if _name(entry).casefold() == wanted]
        if len(found) == 1:
            return found[0]["id"]
        candidates = [{"id": entry["id"], "name": clean_line(_name(entry))} for entry in (found or entries)]
        message = f"more than one calendar is named {name_or_id}" if found else f"no calendar fits {name_or_id}"
        raise GcalError(NOT_FOUND, message, "use an id from candidates", candidates)

    def entry(self, calendar_id):
        if calendar_id == "primary":
            calendar_id = self.account.email
        for entry in self.entries():
            if entry["id"] == calendar_id:
                return entry
        raise GcalError(NOT_FOUND, f"calendar {calendar_id} is not in the calendar list", "run `scripts/gcal calendars`")

    def name(self, calendar_id):
        try:
            return clean_line(_name(self.entry(calendar_id)))
        except GcalError:
            return calendar_id

    def real_id(self, calendar_id):
        return self.account.email if calendar_id == "primary" else calendar_id

    def shown(self):
        """Calendars that are ticked in the Google Calendar sidebar."""
        return [entry["id"] for entry in self.entries() if entry.get("selected")]

    def owned(self):
        return [entry["id"] for entry in self.entries() if entry.get("selected") and entry.get("accessRole") == "owner"]

    def list(self):
        return [shape(entry) for entry in self.entries()]

    def create(self, name, description=None, time_zone=None):
        body = {"summary": name, "timeZone": time_zone or self.account.tz_name}
        if description:
            body["description"] = description
        created = self.api.request("POST", f"{CALENDAR}/calendars", body=body)
        self._entries = None
        return shape(self.entry(created["id"]))

    def edit(self, calendar_id, name=None, description=None, time_zone=None, color=None, remind=None, shown=None):
        calendar_id = self.account.email if calendar_id == "primary" else calendar_id
        entry = self.entry(calendar_id)
        details = {key: value for key, value in (("summary", name), ("description", description), ("timeZone", time_zone)) if value is not None}
        if details:
            if entry.get("accessRole") != "owner":
                raise bad_input("only an owner can change the name, description, or time zone", "use --color, --remind, or --show / --hide")
            self.api.request("PATCH", f"{CALENDAR}/calendars/{quote(calendar_id)}", body=details)
        listing = {}
        if color is not None:
            if not re.fullmatch(HEX_COLOR, color):
                raise bad_input(f"not a color: {color}", "use a hex color such as #3f51b5")
            listing.update(backgroundColor=color, foregroundColor="#ffffff")
        if remind is not None:
            overrides = parse_reminders(remind)
            listing["defaultReminders"] = overrides or []
        if shown is not None:
            listing["selected"] = shown
        if listing:
            self.api.request(
                "PATCH",
                f"{CALENDAR}/users/me/calendarList/{quote(calendar_id)}",
                {"colorRgbFormat": "true" if color else None},
                listing,
            )
        self._entries = None
        return shape(self.entry(calendar_id))

    def delete(self, calendar_id, confirm):
        entry = self.entry(calendar_id)
        if entry.get("primary"):
            raise bad_input("the primary calendar cannot be deleted")
        if entry.get("accessRole") != "owner":
            raise bad_input("you do not own this calendar", "use `calendar unsubscribe` to remove it from the list")
        if confirm != _name(entry):
            raise GcalError(
                APPROVAL,
                "deleting a calendar deletes all its events, with no trash",
                f"get approval from the user, then add --confirm with the exact name: {clean_line(_name(entry))}",
            )
        self.api.request("DELETE", f"{CALENDAR}/calendars/{quote(entry['id'])}")
        return {"deleted": entry["id"], "name": _name(entry)}

    def subscribe(self, calendar_id):
        self.api.request("POST", f"{CALENDAR}/users/me/calendarList", body={"id": calendar_id})
        self._entries = None
        return shape(self.entry(calendar_id))

    def unsubscribe(self, calendar_id, yes):
        entry = self.entry(calendar_id)
        if entry.get("primary"):
            raise bad_input("the primary calendar cannot leave the list")
        if not yes:
            raise approval("this removes the calendar from your list", "--yes")
        self.api.request("DELETE", f"{CALENDAR}/users/me/calendarList/{quote(entry['id'])}")
        return {"unsubscribed": entry["id"]}

    def shares(self, calendar_id):
        rules = self.api.pages(f"{CALENDAR}/calendars/{quote(calendar_id)}/acl")
        return [_shape_rule(rule) for rule in rules]

    def share(self, calendar_id, who, role, yes, group=False):
        if role not in ROLES:
            raise bad_input(f"unknown role: {role}", f"use one of {', '.join(ROLES)}")
        if not yes:
            raise approval(f"this gives {who} the role {role} and emails them", "--yes")
        url = f"{CALENDAR}/calendars/{quote(calendar_id)}/acl"
        existing = next((rule for rule in self.api.pages(url) if _matches(rule, who)), None)
        if existing:
            rule = self.api.request("PATCH", f"{url}/{quote(existing['id'])}", {"sendNotifications": "true"}, {"role": ROLES[role]})
        else:
            scope = {"type": "group" if group else "user", "value": who}
            rule = self.api.request("POST", url, {"sendNotifications": "true"}, {"role": ROLES[role], "scope": scope})
        return _shape_rule(rule)

    def unshare(self, calendar_id, who, yes):
        rule = self._rule(calendar_id, who)
        if rule["scope"]["type"] == "user" and rule["role"] == "owner" and rule["scope"]["value"] == self.account.email:
            raise bad_input("you cannot remove your own owner access")
        if not yes:
            raise approval(f"this removes the access of {who}", "--yes")
        self.api.request("DELETE", f"{CALENDAR}/calendars/{quote(calendar_id)}/acl/{quote(rule['id'])}")
        return {"removed": who, "calendar": calendar_id}

    def _rule(self, calendar_id, who):
        rules = self.api.pages(f"{CALENDAR}/calendars/{quote(calendar_id)}/acl")
        for rule in rules:
            if _matches(rule, who):
                return rule
        candidates = [rule.get("scope", {}).get("value") for rule in rules if rule.get("scope", {}).get("value")]
        raise GcalError(NOT_FOUND, f"{who} has no access to this calendar", "use an email from candidates", candidates)


def _matches(rule, who):
    return rule.get("scope", {}).get("value", "").casefold() == who.casefold()


def _name(entry):
    return entry.get("summaryOverride") or entry.get("summary") or entry["id"]


def shape(entry):
    owned = entry.get("accessRole") == "owner"
    result = {
        "id": entry["id"],
        "own": owned,
        "name": clean_line(_name(entry)),
        "access": ROLE_NAMES.get(entry.get("accessRole"), entry.get("accessRole")),
        "shown": bool(entry.get("selected")),
        "time_zone": entry.get("timeZone"),
        "color": entry.get("backgroundColor"),
        "default_reminders": show_reminders(entry.get("defaultReminders")),
    }
    if entry.get("primary"):
        result["primary"] = True
    # A calendar of another person can carry text aimed at the agent, so only owned calendars show it.
    if owned and entry.get("description"):
        result["description"] = clean(entry["description"], 500)
    return result


def _shape_rule(rule):
    scope = rule.get("scope", {})
    return {"who": scope.get("value") or scope.get("type"), "type": scope.get("type"), "role": ROLE_NAMES.get(rule.get("role"), rule.get("role"))}

