"""Live suite against the real Google account. Run: GOOGLE_CALENDAR_LIVE=1 python3 -m unittest test_live

It works only in a calendar and a task list that it creates and then deletes. It never writes to the primary
calendar. Guests and shares go to $AGENT_EMAIL_ADDRESS; one case sends a real invitation and waits for it.
"""

import json
import os
import subprocess
import sys
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

GCAL = Path(__file__).parents[1] / "scripts" / "gcal"
MAIL = Path(__file__).parents[2] / "agent-email" / "scripts" / "mail.py"
LIVE = os.environ.get("GOOGLE_CALENDAR_LIVE") == "1"
GUEST = os.environ.get("AGENT_EMAIL_ADDRESS", "")
RUN = uuid.uuid4().hex[:6]
# Far enough ahead that the throwaway events cannot collide with real plans of the user.
DAY = date.today() + timedelta(days=60)
while DAY.weekday() != 0:
    DAY += timedelta(days=1)


def gcal(*args, code=0):
    done = subprocess.run([sys.executable, str(GCAL), *args], capture_output=True, text=True, timeout=120)
    if done.returncode != code:
        raise AssertionError(f"gcal {' '.join(args)}: exit {done.returncode}, wanted {code}\n{done.stdout}{done.stderr}")
    if code:
        return json.loads(done.stderr)
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    return [json.loads(line) for line in lines]


def one(*args):
    return gcal(*args)[0]


def at(day_offset, hour, minute=0):
    return f"{DAY + timedelta(days=day_offset)}T{hour:02d}:{minute:02d}"


@unittest.skipUnless(LIVE, "set GOOGLE_CALENDAR_LIVE=1 to run against the real account")
class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.name = f"gcal-live-{RUN}"
        cls.calendar = one("calendar", "create", cls.name)["id"]
        cls.task_list = one("task", "new-list", cls.name)["id"]

    @classmethod
    def tearDownClass(cls):
        gcal("task", "delete-list", cls.task_list, "--yes")
        gcal("calendar", "delete", cls.calendar, "--confirm", cls.name)
        names = [entry["name"] for entry in gcal("calendars")]
        assert cls.name not in names, "the throwaway calendar is still in the list"

    def event(self, title, *args):
        return one("create", f"{title} {RUN}", "--calendar", self.calendar, *args)

    def agenda(self, *args):
        return gcal("agenda", "--calendar", self.calendar, "--from", str(DAY), "--to", str(DAY + timedelta(days=60)), *args)

    def test_account(self):
        facts = one("whoami")
        self.assertIn("@", facts["account"])
        self.assertTrue(facts["time_zone"])

    def test_event_life(self):
        created = self.event("Focus", "--start", at(0, 9), "--duration", "45m", "--location", "Desk",
                             "--color", "tomato", "--remind", "10m,email:1d", "--show-as", "free")
        self.assertTrue(created["own"])
        self.assertEqual(created["color"], "tomato")
        self.assertEqual(created["reminders"], ["popup:10m", "email:1440m"])
        self.assertEqual(created["show_as"], "free")
        self.assertIn(created["ref"], [item["ref"] for item in self.agenda()])
        found = gcal("search", f"Focus {RUN}", "--calendar", self.calendar, "--to", str(DAY + timedelta(days=2)))
        self.assertEqual([item["ref"] for item in found], [created["ref"]])
        changed = one("edit", created["ref"], "--start", at(0, 14), "--title", f"Deep work {RUN}", "--meet", "--description", "plan")
        self.assertEqual(changed["start"][11:16], "14:00")
        self.assertEqual(changed["end"][11:16], "14:45")
        self.assertTrue(changed["meet"].startswith("https://meet.google.com/"))
        self.assertEqual(one("get", created["ref"], "--body")["description"], "plan")
        self.assertEqual(gcal("delete", created["ref"], code=5)["hint"].count("--yes"), 1)
        gcal("delete", created["ref"], "--yes")
        self.assertNotIn(created["ref"], [item["ref"] for item in self.agenda()])

    def test_all_day_event(self):
        created = self.event("Trip", "--start", str(DAY + timedelta(days=2)), "--end", str(DAY + timedelta(days=4)))
        self.assertTrue(created["all_day"])
        self.assertEqual(created["end"], str(DAY + timedelta(days=4)))
        gcal("delete", created["ref"], "--yes")

    def test_colors_match_google(self):
        names = {item["color"] for item in gcal("colors")}
        self.assertEqual(names, {"lavender", "sage", "grape", "flamingo", "banana", "tangerine", "peacock",
                                 "graphite", "blueberry", "basil", "tomato"})

    def test_recurring_scopes(self):
        series = self.event("Standup", "--start", at(7, 10), "--duration", "15m", "--repeat", "weekly", "--count", "6")
        instances = [item for item in self.agenda() if item["title"] == f"Standup {RUN}"]
        self.assertEqual(len(instances), 6)
        second, fourth = instances[1]["ref"], instances[3]["ref"]
        self.assertEqual(gcal("edit", second, "--title", "x", code=2)["hint"].count("--scope"), 1)
        one("edit", second, "--scope", "instance", "--title", f"Moved standup {RUN}", "--start", at(15, 11))
        split = one("edit", fourth, "--scope", "following", "--title", f"Sync {RUN}")
        self.assertIn("two series", split["note"])
        titles = [item["title"] for item in self.agenda() if RUN in item["title"]]
        self.assertEqual(titles, [f"Standup {RUN}", f"Moved standup {RUN}", f"Standup {RUN}", *[f"Sync {RUN}"] * 3])
        one("edit", instances[0]["ref"], "--scope", "all", "--start", at(7, 9))
        firsts = [item for item in self.agenda() if item["title"] == f"Standup {RUN}"]
        self.assertTrue(all(item["start"][11:16] == "09:00" for item in firsts))
        later = [item for item in self.agenda() if item["title"] == f"Sync {RUN}"]
        gcal("delete", later[1]["ref"], "--scope", "following", "--yes")
        self.assertEqual(len([item for item in self.agenda() if item["title"] == f"Sync {RUN}"]), 1)
        gcal("delete", later[0]["ref"], "--scope", "all", "--yes")
        gcal("delete", firsts[0]["ref"], "--scope", "all", "--yes")
        self.assertEqual([item for item in self.agenda() if RUN in item["title"]], [])

    def test_free_busy(self):
        created = self.event("Busy block", "--start", at(1, 10), "--duration", "1h")
        window = ["--from", at(1, 0), "--to", at(2, 0), "--calendar", self.calendar]
        busy = one("free", *window)["busy"]
        self.assertEqual([(b["start"][11:16], b["end"][11:16]) for b in busy], [("10:00", "11:00")])
        slots = gcal("free", *window, "--slots", "30m", "--hours", "09:00-12:00")
        self.assertEqual([(s["start"][11:16], s["end"][11:16]) for s in slots], [("09:00", "10:00"), ("11:00", "12:00")])
        gcal("delete", created["ref"], "--yes")

    @unittest.skipUnless(GUEST, "set AGENT_EMAIL_ADDRESS for the guest cases")
    def test_guest_invitation_arrives(self):
        title = f"Invite {RUN}"
        self.assertIn("--notify", gcal("create", title, "--calendar", self.calendar, "--start", at(3, 15),
                                       "--guest", GUEST, code=5)["hint"])
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        created = one("create", title, "--calendar", self.calendar, "--start", at(3, 15), "--guest", GUEST, "--notify", "all")
        self.assertEqual([g["email"] for g in created["guests"]], [GUEST])
        waited = subprocess.run(
            [sys.executable, str(MAIL), "wait", "--from", "google.com", "--after", started, "--subject", title, "--timeout", "240"],
            capture_output=True, text=True, timeout=300,
        )
        self.assertEqual(waited.returncode, 0, waited.stderr)
        self.assertEqual(gcal("edit", created["ref"], "--title", f"Renamed {RUN}", code=5)["error"].count(GUEST), 1)
        one("edit", created["ref"], "--color", "basil")
        dropped = one("edit", created["ref"], "--remove-guest", GUEST, "--notify", "none")
        self.assertNotIn("guests", dropped)
        gcal("delete", created["ref"], "--yes")

    @unittest.skipUnless(GUEST, "set AGENT_EMAIL_ADDRESS for the sharing cases")
    def test_sharing(self):
        gcal("share", "add", self.calendar, GUEST, "--role", "reader", code=5)
        one("share", "add", self.calendar, GUEST, "--role", "reader", "--yes")
        one("share", "add", self.calendar, GUEST, "--role", "writer", "--yes")
        roles = {rule["who"]: rule["role"] for rule in gcal("share", "list", self.calendar)}
        self.assertEqual(roles[GUEST], "writer")
        gcal("share", "remove", self.calendar, GUEST, code=5)
        one("share", "remove", self.calendar, GUEST, "--yes")
        self.assertNotIn(GUEST, {rule["who"] for rule in gcal("share", "list", self.calendar)})

    def test_calendar_settings(self):
        changed = one("calendar", "edit", self.calendar, "--description", "throwaway", "--color", "#3f51b5", "--remind", "30m")
        self.assertEqual(changed["color"].lower(), "#3f51b5")
        self.assertEqual(changed["default_reminders"], ["popup:30m"])
        gcal("calendar", "delete", self.calendar, "--confirm", "wrong", code=5)

    def test_tasks(self):
        task_list = self.task_list
        parent = one("task", "add", "Pack", "--list", task_list, "--due", str(DAY))
        self.assertEqual(parent["due"], str(DAY))
        child = one("task", "add", "Charger", "--list", task_list, "--parent", parent["ref"])
        other = one("task", "add", "Book taxi", "--list", task_list)
        outline = [item["title"] for item in gcal("task", "show", "--list", task_list)]
        self.assertEqual(outline.index("Charger"), outline.index("Pack") + 1)
        self.assertEqual(one("task", "edit", other["ref"], "--due", "none", "--notes", "early").get("notes"), "early")
        one("task", "done", child["ref"])
        self.assertNotIn("Charger", [item["title"] for item in gcal("task", "show", "--list", task_list)])
        self.assertEqual(one("task", "reopen", child["ref"])["status"], "open")
        one("task", "move", other["ref"], "--top")
        self.assertEqual(gcal("task", "show", "--list", task_list)[0]["title"], "Book taxi")
        gcal("task", "delete", other["ref"], code=5)
        gcal("task", "delete", other["ref"], "--yes")
        renamed = one("task", "rename-list", task_list, f"{self.name} renamed")
        self.assertEqual(renamed["name"], f"{self.name} renamed")


if __name__ == "__main__":
    unittest.main()
