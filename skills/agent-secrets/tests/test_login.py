import contextlib
import importlib.util
import io
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "login.py"
SPEC = importlib.util.spec_from_file_location("login", MODULE_PATH)
login = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(login)

ENV = {"BWS_ACCESS_TOKEN": "token", "AGENT_SECRETS_PROJECT_ID": "project"}


class FakeBws:
    def __init__(self):
        self.secrets, self.calls = [], []

    def add(self, key, value, status):
        self.secrets.append({"id": f"id-{len(self.secrets)}", "key": key, "value": value, "note": json.dumps({"status": status, "url": "https://example.com"})})

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("secret", "list"):
            return json.dumps(self.secrets)
        if args[:2] == ("secret", "create"):
            note = args[args.index("--note") + 1]
            key, value, _ = args[args.index("--") + 1 :]
            self.secrets.append({"id": "new", "key": key, "value": value, "note": note})
        if args[:2] == ("secret", "edit"):
            entry = next(s for s in self.secrets if s["id"] == args[3])
            field, value = args[2].removeprefix("--").split("=", 1)
            entry[field] = value
        if args[:2] == ("secret", "delete"):
            self.secrets = [s for s in self.secrets if s["id"] != args[2]]
        return ""


def run(fake, *argv, stdin=""):
    out = io.StringIO()
    with patch.dict(os.environ, ENV), patch.object(login, "bws", fake), patch.object(login.sys, "stdin", io.StringIO(stdin)), contextlib.redirect_stdout(out):
        login.main(list(argv))
    return json.loads(out.getvalue())


class LoginTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeBws()

    def test_new_stores_random_pending_password_without_printing_it(self):
        result = run(self.fake, "new", "example.com", "agent", "https://example.com/signup", "agent@mail.to")

        stored = self.fake.secrets[0]
        self.assertEqual(stored["key"], "example.com/agent")
        self.assertEqual(len(stored["value"]), 32)
        self.assertEqual(json.loads(stored["note"])["email"], "agent@mail.to")
        self.assertEqual(result["status"], "pending")
        self.assertNotIn("password", result)

    def test_new_refuses_existing_login(self):
        self.fake.add("example.com/agent", "pw", "active")

        with self.assertRaises(SystemExit):
            run(self.fake, "new", "example.com", "agent", "https://example.com")

    def test_list_hides_passwords_and_filters_by_domain(self):
        self.fake.add("example.com/agent", "pw1", "active")
        self.fake.add("other.com/agent", "pw2", "pending")

        result = run(self.fake, "list", "example.com")

        self.assertEqual([entry["login"] for entry in result], ["example.com/agent"])
        self.assertNotIn("pw1", json.dumps(result))

    def test_get_shows_password(self):
        self.fake.add("example.com/agent", "pw", "active")

        self.assertEqual(run(self.fake, "get", "example.com", "agent")["password"], "pw")

    def test_activate_keeps_note_fields(self):
        self.fake.add("example.com/agent", "pw", "pending")

        result = run(self.fake, "activate", "example.com", "agent")

        self.assertEqual(result["status"], "active")
        self.assertEqual(result["url"], "https://example.com")

    def test_active_login_cannot_change(self):
        self.fake.add("example.com/agent", "pw", "active")

        for argv in (["set-password", "example.com", "agent"], ["delete", "example.com", "agent"], ["activate", "example.com", "agent"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                run(self.fake, *argv)
        self.assertEqual(self.fake.secrets[0]["value"], "pw")

    def test_pending_login_can_change_and_delete(self):
        self.fake.add("example.com/agent", "pw", "pending")

        run(self.fake, "set-password", "example.com", "agent", stdin="-Site1!\n")
        self.assertEqual(self.fake.secrets[0]["value"], "-Site1!")

        run(self.fake, "delete", "example.com", "agent")
        self.assertEqual(self.fake.secrets, [])

    def test_set_password_keeps_spaces_and_drops_only_the_newline(self):
        self.fake.add("example.com/agent", "pw", "pending")

        run(self.fake, "set-password", "example.com", "agent", stdin=" Site1! \r\n")

        self.assertEqual(self.fake.secrets[0]["value"], " Site1! ")

    def test_set_password_checks_status_after_reading_stdin(self):
        self.fake.add("example.com/agent", "pw", "pending")

        class ActivateOnRead(io.StringIO):
            def read(stream):
                note = json.loads(self.fake.secrets[0]["note"])
                self.fake.secrets[0]["note"] = json.dumps({**note, "status": "active"})
                return "new"

        with patch.dict(os.environ, ENV), patch.object(login, "bws", self.fake), patch.object(login.sys, "stdin", ActivateOnRead()):
            with self.assertRaises(SystemExit):
                login.main(["set-password", "example.com", "agent"])
        self.assertEqual(self.fake.secrets[0]["value"], "pw")

    def test_note_that_is_not_an_object_counts_as_empty(self):
        self.fake.add("example.com/agent", "pw", "pending")
        for note in ("null", "[]", '"text"'):
            self.fake.secrets[0]["note"] = note
            with self.subTest(note=note):
                self.assertEqual(run(self.fake, "list")[0]["status"], None)
                self.assertEqual(run(self.fake, "get", "example.com", "agent")["password"], "pw")

    def test_missing_env_points_to_setup(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit) as error:
            login.project_id()

        self.assertIn("setup.md", str(error.exception.code))


class BwsProcessTest(unittest.TestCase):
    def run_with(self, process, *argv):
        out = io.StringIO()
        with patch.dict(os.environ, ENV), patch.object(login.subprocess, "run", process), contextlib.redirect_stdout(out):
            login.main(list(argv))
        return out.getvalue()

    def test_secret_output_of_bws_stays_captured(self):
        stored = {"id": "1", "key": "example.com/agent", "value": "hunter2", "note": json.dumps({"status": "pending"})}

        def process(command, **options):
            self.assertTrue(options["capture_output"])
            stdout = json.dumps([stored]) if command[3:5] == ["secret", "list"] else json.dumps(stored)
            return subprocess.CompletedProcess(command, 0, stdout, "")

        for argv in (["activate", "example.com", "agent"], ["delete", "example.com", "agent"], ["list"]):
            with self.subTest(argv=argv):
                self.assertNotIn("hunter2", self.run_with(process, *argv))

    def test_bws_failure_reports_stderr(self):
        def process(command, **_):
            return subprocess.CompletedProcess(command, 1, "", "invalid token")

        with self.assertRaises(SystemExit) as error:
            self.run_with(process, "list")
        self.assertEqual(error.exception.code, "login: invalid token")

    def test_missing_bws_points_to_setup(self):
        def process(command, **_):
            raise FileNotFoundError(command[0])

        with self.assertRaises(SystemExit) as error:
            self.run_with(process, "list")
        self.assertIn("bws is not installed", error.exception.code)


if __name__ == "__main__":
    unittest.main()
