"""The one credentials file: OAuth client, refresh token, access token cache, and account facts."""

import json
import os
import tempfile
from pathlib import Path

from .errors import AUTH, GcalError

SETUP_HINT = "the user runs `scripts/gcal setup` in a terminal (see references/setup.md)"


def home():
    return Path(os.environ.get("GOOGLE_CALENDAR_HOME") or Path.home() / ".config" / "google-calendar")


def credentials_file():
    return home() / "credentials.json"


def load():
    path = credentials_file()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise GcalError(AUTH, f"cannot read {path}: {error}", SETUP_HINT) from error


def save(data):
    folder = home()
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    # Write and rename, so a crash cannot leave a half-written refresh token behind.
    descriptor, temporary = tempfile.mkstemp(dir=folder, prefix=".credentials-")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(data, handle, indent=2)
        os.replace(temporary, credentials_file())
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def update(**fields):
    data = load()
    data.update(fields)
    save(data)
    return data


def require_client(data):
    if not data.get("client_id") or not data.get("client_secret"):
        raise GcalError(AUTH, "no OAuth client is set up", SETUP_HINT)
