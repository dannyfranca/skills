import unittest
from datetime import datetime, timedelta

import fakes
from fakes import ME, FakeAccount, FakeApi, calendar_list, event
from google_calendar import untrusted
from google_calendar.calendars import Calendars
from google_calendar.errors import APPROVAL, BAD_INPUT, GcalError
from google_calendar.events import Events, free_slots, truncate

EVENTS = "/calendars/primary/events"
GUEST = "guest@example.com"
SERIES = event("s1", "2026-11-02T10:00:00+00:00", "2026-11-02T10:30:00+00:00", summary="Standup",
               recurrence=["RRULE:FREQ=WEEKLY;COUNT=10"])
FOURTH = event("s1_20261123T100000Z", "2026-11-23T10:00:00+00:00", "2026-11-23T10:30:00+00:00", summary="Standup",
               recurringEventId="s1", originalStartTime={"dateTime": "2026-11-23T10:00:00Z", "timeZone": fakes.ZONE})
FIRST = dict(FOURTH, id="s1_20261102T100000Z", originalStartTime={"dateTime": "2026-11-02T10:00:00Z"})


def make(routes=None):
    api = FakeApi({**calendar_list(), **(routes or {})})
    account = FakeAccount()
    return Events(api, account, Calendars(api, account)), api


def code_of(call):
    with unittest.TestCase().assertRaises(GcalError) as caught:
        call()
    return caught.exception


class CreateTests(unittest.TestCase):
    def test_own_event_needs_no_flag_and_lasts_one_hour(self):
        events, api = make({("POST", EVENTS): lambda params, body: {"id": "n", **body, "creator": {"email": ME}}})
        result = events.create("Focus", "2026-11-12T09:00")
        self.assertEqual(result["end"], "2026-11-12T10:00:00+00:00")
        self.assertTrue(result["own"])
        self.assertNotIn("sendUpdates", api.sent("POST", EVENTS)[0][2])

    def test_guests_need_notify(self):
        events, api = make()
        error = code_of(lambda: events.create("Lunch", "2026-11-12T12:00", guests=[GUEST]))
        self.assertEqual(error.code, APPROVAL)
        self.assertIn("--notify", error.hint)
        self.assertEqual(api.sent("POST"), [])

    def test_all_day_end_is_the_last_day(self):
        events, api = make({("POST", EVENTS): lambda params, body: {"id": "n", **body}})
        result = events.create("Trip", "2026-11-12", end="2026-11-14")
        self.assertEqual(api.sent("POST", EVENTS)[0][3]["end"], {"date": "2026-11-15"})
        self.assertEqual(result["end"], "2026-11-14")

    def test_meet_and_color_and_reminders(self):
        events, api = make({("POST", EVENTS): lambda params, body: {"id": "n", **body}})
        events.create("Call", "2026-11-12T09:00", meet=True, color="tomato", remind="10m")
        method, path, params, body = api.sent("POST", EVENTS)[0]
        self.assertEqual(params["conferenceDataVersion"], 1)
        self.assertEqual(body["colorId"], "11")
        self.assertEqual(body["reminders"], {"useDefault": False, "overrides": [{"method": "popup", "minutes": 10}]})
        self.assertEqual(code_of(lambda: events.create("x", "2026-11-12T09:00", color="pink")).code, BAD_INPUT)


class EditTests(unittest.TestCase):
    def test_personal_change_on_a_shared_event_emails_nobody(self):
        shared = event("e1", "2026-11-12T09:00:00+00:00", "2026-11-12T10:00:00+00:00", attendees=[{"email": GUEST}])
        events, api = make({("GET", f"{EVENTS}/e1"): shared, ("PATCH", f"{EVENTS}/e1"): shared})
        events.edit("e1", color="sage", remind="none")
        self.assertEqual(len(api.sent("PATCH")), 1)
        self.assertEqual(code_of(lambda: events.edit("e1", title="New")).code, APPROVAL)

    def test_adding_a_guest_needs_notify(self):
        solo = event("e1", "2026-11-12T09:00:00+00:00", "2026-11-12T10:00:00+00:00")
        events, api = make({("GET", f"{EVENTS}/e1"): solo, ("PATCH", f"{EVENTS}/e1"): solo})
        self.assertEqual(code_of(lambda: events.edit("e1", add_guests=[GUEST])).code, APPROVAL)
        events.edit("e1", add_guests=[GUEST], notify="all")
        self.assertEqual(api.sent("PATCH")[0][3]["attendees"], [{"email": GUEST}])

    def test_new_start_keeps_the_length(self):
        solo = event("e1", "2026-11-12T09:00:00+00:00", "2026-11-12T09:45:00+00:00")
        events, api = make({("GET", f"{EVENTS}/e1"): solo, ("PATCH", f"{EVENTS}/e1"): solo})
        events.edit("e1", start="2026-11-13T14:00")
        body = api.sent("PATCH")[0][3]
        self.assertEqual(body["end"]["dateTime"], "2026-11-13T14:45:00+00:00")

    def test_an_instance_needs_a_scope(self):
        events, _ = make({("GET", f"{EVENTS}/{FOURTH['id']}"): FOURTH, ("GET", f"{EVENTS}/s1"): SERIES})
        error = code_of(lambda: events.edit(FOURTH["id"], title="x"))
        self.assertEqual(error.code, BAD_INPUT)
        self.assertIn("--scope", error.hint)

    def test_scope_all_moves_the_series_by_the_instance_delta(self):
        events, api = make({("GET", f"{EVENTS}/{FOURTH['id']}"): FOURTH, ("GET", f"{EVENTS}/s1"): SERIES,
                            ("PATCH", f"{EVENTS}/s1"): SERIES})
        events.edit(FOURTH["id"], scope="all", start="2026-11-23T11:00")
        body = api.sent("PATCH", f"{EVENTS}/s1")[0][3]
        self.assertEqual(body["start"]["dateTime"], "2026-11-02T11:00:00+00:00")
        self.assertEqual(body["end"]["dateTime"], "2026-11-02T11:30:00+00:00")

    def test_scope_following_splits_the_series(self):
        before = [{"id": f"i{n}"} for n in range(3)]
        events, api = make({
            ("GET", f"{EVENTS}/{FOURTH['id']}"): FOURTH,
            ("GET", f"{EVENTS}/s1"): SERIES,
            ("GET", f"{EVENTS}/s1/instances"): {"items": before},
            ("POST", EVENTS): lambda params, body: {"id": "s2", **body},
            ("PATCH", f"{EVENTS}/s1"): SERIES,
        })
        result = events.edit(FOURTH["id"], scope="following", title="Sync")
        created = api.sent("POST", EVENTS)[0][3]
        self.assertEqual(created["summary"], "Sync")
        self.assertEqual(created["recurrence"], ["RRULE:FREQ=WEEKLY;COUNT=7"])
        self.assertEqual(created["start"]["dateTime"], "2026-11-23T10:00:00Z")
        self.assertEqual(api.sent("PATCH", f"{EVENTS}/s1")[0][3], {"recurrence": ["RRULE:FREQ=WEEKLY;UNTIL=20261123T095959Z"]})
        self.assertIn("two series", result["note"])

    def test_following_from_the_first_instance_is_all(self):
        events, api = make({("GET", f"{EVENTS}/{FIRST['id']}"): FIRST, ("GET", f"{EVENTS}/s1"): SERIES,
                            ("PATCH", f"{EVENTS}/s1"): SERIES})
        events.edit(FIRST["id"], scope="following", title="Sync")
        self.assertEqual(api.sent("POST"), [])
        self.assertEqual(api.sent("PATCH", f"{EVENTS}/s1")[0][3], {"summary": "Sync"})


class DeleteAndRsvpTests(unittest.TestCase):
    def test_delete_needs_yes_and_notify_for_guests(self):
        shared = event("e1", "2026-11-12T09:00:00+00:00", "2026-11-12T10:00:00+00:00", attendees=[{"email": GUEST}])
        events, api = make({("GET", f"{EVENTS}/e1"): shared})
        error = code_of(lambda: events.delete("e1"))
        self.assertEqual(error.code, APPROVAL)
        self.assertIn("--yes", error.hint)
        self.assertIn("--notify", error.hint)
        events.delete("e1", yes=True, notify="none")
        self.assertEqual(api.sent("DELETE")[0][2], {"sendUpdates": "none"})

    def test_delete_following_ends_the_series(self):
        events, api = make({("GET", f"{EVENTS}/{FOURTH['id']}"): FOURTH, ("GET", f"{EVENTS}/s1"): SERIES})
        result = events.delete(FOURTH["id"], scope="following", yes=True)
        self.assertEqual(api.sent("DELETE"), [])
        self.assertIn("UNTIL=20261123T095959Z", api.sent("PATCH")[0][3]["recurrence"][0])
        self.assertEqual(result["scope"], "following")

    def test_rsvp_always_needs_notify(self):
        invite = event("e1", "2026-11-12T09:00:00+00:00", "2026-11-12T10:00:00+00:00", creator=GUEST, organizer=GUEST,
                       attendees=[{"email": GUEST, "organizer": True}, {"email": ME, "self": True, "responseStatus": "needsAction"}])
        events, api = make({("GET", f"{EVENTS}/e1"): invite, ("PATCH", f"{EVENTS}/e1"): invite})
        self.assertEqual(code_of(lambda: events.rsvp("e1", "yes")).code, APPROVAL)
        events.rsvp("e1", "maybe", notify="all")
        mine = [a for a in api.sent("PATCH")[0][3]["attendees"] if a["email"] == ME]
        self.assertEqual(mine[0]["responseStatus"], "tentative")


class ReadTests(unittest.TestCase):
    def test_agenda_merges_calendars_and_wraps_events_of_others(self):
        team = {"id": "team@group.calendar.google.com", "summary": "Team", "accessRole": "reader", "selected": True}
        hidden = {"id": "old@group.calendar.google.com", "summary": "Old", "accessRole": "owner", "selected": False}
        api = FakeApi({
            **calendar_list(team, hidden),
            ("GET", "/calendars/me%40example.com/events"): {"items": [event("a", "2026-11-12T11:00:00+00:00", "2026-11-12T12:00:00+00:00")]},
            ("GET", "/calendars/team%40group.calendar.google.com/events"): {
                "items": [event("b", "2026-11-12T09:00:00+00:00", "2026-11-12T10:00:00+00:00", creator="x@example.com")]
            },
        })
        account = FakeAccount()
        events = Events(api, account, Calendars(api, account))
        items = events.agenda("2026-11-12", "2026-11-13")
        self.assertEqual([item["calendar"] for item in items], ["Team", ME])
        self.assertFalse(items[0]["own"])
        self.assertIn("<untrusted-event", untrusted.render(items))
        self.assertNotIn("old%40", " ".join(call[1] for call in api.calls))

    def test_free_slots_skip_busy_time_and_weekends(self):
        tz = FakeAccount.tz
        begin = datetime(2026, 11, 13, 0, 0, tzinfo=tz)
        busy = [(datetime(2026, 11, 13, 10, 0, tzinfo=tz), datetime(2026, 11, 13, 11, 0, tzinfo=tz))]
        slots = free_slots(begin, begin + timedelta(days=4), busy, timedelta(minutes=30), "09:00-12:00", False, tz)
        self.assertEqual([(s["start"][11:16], s["end"][11:16]) for s in slots], [("09:00", "10:00"), ("11:00", "12:00"), ("09:00", "12:00")])
        self.assertEqual(slots[-1]["start"][:10], "2026-11-16")

    def test_truncate_drops_count(self):
        rules = ["RRULE:FREQ=DAILY;COUNT=5;BYHOUR=9", "EXDATE:20261112T090000Z"]
        self.assertEqual(truncate(rules, "20261120"), ["RRULE:FREQ=DAILY;BYHOUR=9;UNTIL=20261120", "EXDATE:20261112T090000Z"])


if __name__ == "__main__":
    unittest.main()
