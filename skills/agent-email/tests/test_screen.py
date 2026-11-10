import os
import shlex
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from inbox import guard, screen  # noqa: E402
from inbox.base import Armor, Message  # noqa: E402


def message(labels=(), armor=None, text="Your code is 123456"):
    return Message("1", "noreply@site.com", "Code", None, labels=list(labels), armor=armor, text=text)


def guard_command(script):
    return shlex.join([sys.executable, "-c", script])


@patch.dict(os.environ, {}, clear=True)
class HoldTests(unittest.TestCase):
    def test_held_labels(self):
        for label in ("spam", "unauthenticated", "agent-armor-held"):
            self.assertEqual(screen.hold(message(labels=["received", label])), f"label {label}")

    def test_armor_verdicts(self):
        self.assertIsNone(screen.hold(message()))
        self.assertIsNone(screen.hold(message(armor=Armor("complete", "clear"))))
        self.assertEqual(screen.hold(message(armor=Armor("complete", "review", "asks agent"))), "armor review: asks agent")
        self.assertEqual(screen.hold(message(armor=Armor("unavailable", reason="quota"))), "armor unavailable")
        self.assertEqual(screen.hold(message(armor=Armor("pending"))), "armor pending")

    def test_withheld_mail_shows_no_code(self):
        text = screen.inspect(message(labels=["spam"]))
        self.assertIn("withheld: label spam", text)
        self.assertNotIn("123456", text)

    def test_shown_without_guard(self):
        text = screen.inspect(message(armor=Armor("complete", "clear")))
        self.assertIn("shown (armor: clear, guard: off)", text)
        self.assertIn("<codes>123456</codes>", text)

    def test_listing_never_shows_codes(self):
        self.assertNotIn("123456", screen.listing(message()))


class GuardTests(unittest.TestCase):
    def inspect_with(self, script, **fields):
        return self.inspect_with_command(guard_command(script), **fields)

    def inspect_with_command(self, command, **fields):
        with patch.dict(os.environ, {"AGENT_EMAIL_GUARD_CMD": command}, clear=True):
            return screen.inspect(message(**fields))

    def test_exact_safe_passes(self):
        self.assertIn("guard: safe", self.inspect_with("import sys; sys.stdin.read(); print('SAFE')"))

    def test_any_other_answer_withholds(self):
        for answer in ("INJECTION", "SAFE, probably", "safe"):
            text = self.inspect_with(f"print({answer!r})")
            self.assertIn("withheld: guard", text)
            self.assertNotIn("123456", text)

    def test_failure_withholds(self):
        self.assertIn("withheld: guard unavailable", self.inspect_with("raise SystemExit(1)"))
        with patch.dict(os.environ, {"AGENT_EMAIL_GUARD_CMD": "/no/such/guard"}):
            self.assertEqual(guard.check("x"), "unavailable")

    def test_blank_command_withholds(self):
        self.assertIn("withheld: guard unavailable", self.inspect_with_command("   "))

    def test_timeout_withholds(self):
        with patch.object(guard, "TIMEOUT_SECONDS", 0.2):
            self.assertIn("guard unavailable", self.inspect_with("import time; time.sleep(5)"))

    def test_guard_skipped_for_held_mail(self):
        self.assertIn("withheld: label spam", self.inspect_with("raise SystemExit('ran')", labels=["spam"]))

    def test_guard_reads_prompt_and_body_in_an_empty_folder(self):
        script = (
            "import os, sys; data = sys.stdin.read(); "
            "ok = 'Reply with one word' in data and 'Your code is 123456' in data and not os.listdir('.'); "
            "print('SAFE' if ok else 'NO')"
        )
        self.assertIn("guard: safe", self.inspect_with(script))


if __name__ == "__main__":
    unittest.main()
