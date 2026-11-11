import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from google_calendar.api import CALENDAR, TASKS  # noqa: E402

ME = "me@example.com"
ZONE = "Europe/Lisbon"


class FakeAccount:
    email = ME
    tz_name = ZONE
    tz = ZoneInfo(ZONE)


class FakeApi:
    """Routes (method, path) to canned answers and records every call."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []

    def request(self, method, url, params=None, body=None):
        path = url.replace(CALENDAR, "").replace(TASKS, "")
        params = {key: value for key, value in (params or {}).items() if value is not None}
        self.calls.append((method, path, params, body))
        answer = self.routes.get((method, path), {})
        return answer(params, body) if callable(answer) else answer

    def get(self, url, **params):
        return self.request("GET", url, params)

    def pages(self, url, key="items", limit=None, **params):
        return list(self.request("GET", url, params).get(key, []))

    def sent(self, method, path=None):
        return [call for call in self.calls if call[0] == method and (path is None or call[1] == path)]


def calendar_list(*entries):
    base = [{"id": ME, "summary": ME, "primary": True, "accessRole": "owner", "selected": True, "timeZone": ZONE}]
    return {("GET", "/users/me/calendarList"): {"items": base + list(entries)}}


def event(event_id, start, end, **fields):
    key = "dateTime" if "T" in start else "date"
    return {
        "id": event_id,
        "summary": fields.pop("summary", "Meeting"),
        "start": {key: start, **({"timeZone": ZONE} if key == "dateTime" else {})},
        "end": {key: end, **({"timeZone": ZONE} if key == "dateTime" else {})},
        "creator": {"email": fields.pop("creator", ME)},
        "organizer": {"email": fields.pop("organizer", ME)},
        "status": "confirmed",
        **fields,
    }
