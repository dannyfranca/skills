"""Account facts that commands need: the email and the time zone of the Google account."""

import time

from . import store
from .api import CALENDAR
from .times import zone

# The user can change the time zone in Google Calendar, so the cached value expires.
TIME_ZONE_MAX_AGE_SECONDS = 86400


class Account:
    def __init__(self, api):
        self.api = api
        self._facts = None

    def refresh(self):
        primary = self.api.get(f"{CALENDAR}/calendars/primary")
        # The time zone of the user is a setting of its own; the primary calendar can keep an older zone.
        setting = self.api.get(f"{CALENDAR}/users/me/settings/timezone").get("value")
        self._facts = store.update(account=primary["id"], time_zone=setting or primary["timeZone"], checked_at=time.time())
        return self._facts

    def facts(self):
        if self._facts is None:
            data = store.load()
            fresh = time.time() - data.get("checked_at", 0) < TIME_ZONE_MAX_AGE_SECONDS
            self._facts = data if fresh and data.get("account") and data.get("time_zone") else self.refresh()
        return self._facts

    @property
    def email(self):
        return self.facts()["account"]

    @property
    def tz_name(self):
        return self.facts()["time_zone"]

    @property
    def tz(self):
        return zone(self.tz_name)
