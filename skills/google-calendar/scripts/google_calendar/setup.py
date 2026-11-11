"""Interactive setup that the user runs once in a terminal: Google Cloud project, OAuth client, login."""

import re
import sys
from pathlib import Path

from . import auth, store
from .account import Account
from .api import Api
from .errors import GcalError, bad_input

PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
BOLD, DIM, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m"


class Quit(Exception):
    pass


class Wizard:
    def __init__(self, downloads=None, read=input, write=print):
        self.downloads = Path(downloads or Path.home() / "Downloads")
        self.read, self.write = read, write
        self.project = None
        self.stages = [
            ("Create a Google Cloud project", self.create_project),
            ("Turn on the Calendar API and the Tasks API", self.enable_apis),
            ("Set up the Google Auth Platform", self.consent_screen),
            ("Publish the app", self.publish),
            ("Create a Desktop OAuth client", self.create_client),
            ("Import the client file", self.import_client),
            ("Log in", self.login),
            ("Check the access", self.verify),
        ]

    def run(self):
        start = 0
        if store.load().get("client_id"):
            self.screen("An OAuth client is already set up", "Stage 7 of 8 logs in again with this client.")
            if self.ask("Log in again with the client that you have? [Y/n]", default="y").lower().startswith("y"):
                start = 6
        for number, (title, stage) in enumerate(self.stages[start:], start + 1):
            self.screen(f"Stage {number} of {len(self.stages)}: {title}")
            stage()
        self.write(f"\n{GREEN}{BOLD}Setup is complete.{RESET} Tell the agent: \"Google Calendar setup is done\".")

    # Stages

    def create_project(self):
        self.say(
            "A Google Cloud project holds the OAuth client of this skill. It is free.",
            "1. On the page, type a project name, for example: agent-calendar.",
            "2. Copy the Project ID under the name field. Google can add numbers to it.",
            "3. Click Create.",
            "If you already have a project for this, you can use it. Skip the Create step.",
        )
        self.open("https://console.cloud.google.com/projectcreate")
        while True:
            answer = self.ask("Type the Project ID").strip()
            if PROJECT_ID.match(answer):
                self.project = answer
                return
            self.warn("A Project ID has 6 to 30 characters: lowercase letters, digits, and hyphens.")

    def enable_apis(self):
        self.say(
            f"1. Make sure that the project {self.project} is selected at the top of the page.",
            "2. Click Next, then click Enable.",
        )
        self.open(
            "https://console.cloud.google.com/flows/enableapi"
            f"?apiid=calendar-json.googleapis.com,tasks.googleapis.com&project={self.project}"
        )
        self.wait()

    def consent_screen(self):
        self.say(
            "Google shows this information on the page where you click Allow.",
            "1. Click Get started.",
            "2. App name: Agent Calendar. User support email: your email. Click Next.",
            "3. Audience: External. (For a Google Workspace account, Internal is also good.) Click Next.",
            "4. Contact information: your email. Click Next.",
            "5. Agree to the policy. Click Continue, then click Create.",
            "If the page shows an overview with no Get started button, this stage is already done.",
        )
        self.open(f"https://console.cloud.google.com/auth/overview?project={self.project}")
        self.wait()

    def publish(self):
        self.say(
            f"{BOLD}Do this stage.{RESET} In the Testing status, Google stops the login after 7 days.",
            "1. Under Publishing status, click Publish app, then click Confirm.",
            "2. Make sure that the status is In production.",
            "Google does not need to verify the app. Only you use it.",
            "For an Internal audience, there is no Publish button. Skip this stage.",
        )
        self.open(f"https://console.cloud.google.com/auth/audience?project={self.project}")
        self.wait()

    def create_client(self):
        self.say(
            "1. Application type: Desktop app.",
            "2. Name: agent-calendar. Click Create.",
            f"3. In the dialog, click {BOLD}Download JSON{RESET}.",
            "   Google shows the client secret only in this dialog. If you close it first, delete the client and make a new one.",
            f"The next stage reads the file from {self.downloads} and then deletes it.",
        )
        self.open(f"https://console.cloud.google.com/auth/clients/create?project={self.project}")
        self.wait("Press Enter when the file is downloaded")

    def import_client(self):
        while True:
            found = sorted(self.downloads.glob("client_secret_*.json"), key=lambda path: path.stat().st_mtime)
            if found:
                path = found[-1]
                answer = self.ask(f"Use {path.name}? [Y/n, or type a path]", default="y").strip()
                if answer and not answer.lower().startswith("y"):
                    path = Path(answer).expanduser() if answer.lower() != "n" else None
            else:
                answer = self.ask(f"No client_secret_*.json in {self.downloads}. Type the path of the file").strip()
                path = Path(answer).expanduser() if answer else None
            if not path:
                continue
            try:
                auth.import_client(path)
            except GcalError as error:
                self.warn(error.message + (f" ({error.hint})" if error.hint else ""))
                continue
            # The secret is now in the credentials file (mode 600); a second copy in Downloads is a leak risk.
            path.unlink(missing_ok=True)
            self.write(f"{GREEN}Imported to {store.credentials_file()} and deleted {path}.{RESET}")
            return

    def login(self):
        self.say(
            "The browser opens a Google page.",
            "1. Choose the Google account of your calendar.",
            "2. Google says that it has not verified the app. This is your own app.",
            "   Click Advanced, then click Go to Agent Calendar (unsafe).",
            "3. Tick every box, then click Continue.",
        )
        self.wait("Press Enter to open the login page")
        while True:
            try:
                auth.login(notify=lambda url: self.write(f"{DIM}If the browser does not open, open this URL:\n{url}{RESET}"))
                return
            except GcalError as error:
                self.warn(error.message)
                if not self.ask("Try again? [Y/n]", default="y").lower().startswith("y"):
                    raise Quit from error

    def verify(self):
        facts = Account(Api()).refresh()
        self.write(f"{GREEN}Connected to {facts['account']}, time zone {facts['time_zone']}.{RESET}")

    # Screen helpers

    def screen(self, title, *lines):
        if sys.stdout.isatty():
            self.write("\033[2J\033[H", end="")
        self.write(f"{BOLD}Google Calendar setup{RESET}  {DIM}(q to quit){RESET}\n\n{BOLD}{title}{RESET}\n")
        self.say(*lines)

    def say(self, *lines):
        for line in lines:
            self.write(line)

    def warn(self, text):
        self.write(f"{RED}{text}{RESET}")

    def open(self, url):
        opened = auth.open_browser(url)
        self.write(f"\n{'Opened' if opened else 'Open'}: {url}")

    def wait(self, prompt="Press Enter when you are done"):
        self.ask(prompt)

    def ask(self, prompt, default=""):
        answer = self.read(f"\n{prompt} > ")
        if answer.strip().lower() == "q":
            raise Quit
        return answer or default


def run():
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise bad_input(
            "setup is interactive and needs a terminal",
            "ask the user to run `scripts/gcal setup` in a terminal; references/setup.md has the same steps",
        )
    try:
        Wizard().run()
    except (Quit, KeyboardInterrupt, EOFError):
        print(f"\nSetup stopped. Run it again to continue. Progress in {store.home()} stays.")
        return 1
    return 0

