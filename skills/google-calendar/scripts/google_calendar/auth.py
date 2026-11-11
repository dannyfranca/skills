"""OAuth for a Desktop client: loopback login with PKCE, token refresh, client import, and logout."""

import base64
import hashlib
import json
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from . import store
from .errors import AUTH, BAD_INPUT, REMOTE, GcalError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SCOPES = ("https://www.googleapis.com/auth/calendar", "https://www.googleapis.com/auth/tasks")
LOGIN_TIMEOUT_SECONDS = 300
# Refresh a little early, so a token does not expire between the check and the request.
EXPIRY_MARGIN_SECONDS = 60
LOGIN_HINT = "run `scripts/gcal auth`: the user clicks Allow in the browser"


def open_browser(url):
    for opener in ("xdg-open", "open", "wslview"):
        if shutil.which(opener):
            subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
    return False


def _post(url, form):
    request = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        try:
            return json.loads(error.read() or b"{}") | {"_status": error.code}
        except json.JSONDecodeError:
            return {"_status": error.code, "error": "http_error"}
    except (urllib.error.URLError, TimeoutError) as error:
        raise GcalError(REMOTE, f"cannot reach Google: {error}", "check the network, then try again") from error


def import_client(path):
    """Copy client ID and secret from a downloaded client_secret_*.json into the credentials file."""
    try:
        downloaded = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise GcalError(BAD_INPUT, f"cannot read the client file {path}: {error}") from error
    client = downloaded.get("installed")
    if not client:
        kind = next(iter(downloaded), "unknown")
        raise GcalError(BAD_INPUT, f"the client file is for a '{kind}' client", "create an OAuth client of type Desktop app")
    data = store.load()
    if data.get("client_id") != client["client_id"]:
        # A new client cannot use the refresh token of the old client.
        for key in ("refresh_token", "access_token", "expires_at", "account", "time_zone", "checked_at"):
            data.pop(key, None)
    data.update(client_id=client["client_id"], client_secret=client["client_secret"], project_id=client.get("project_id"))
    store.save(data)
    return data


class _Callback(BaseHTTPRequestHandler):
    result = None

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" not in query and "error" not in query:
            self.send_response(404)
            self.end_headers()
            return
        type(self).result = {key: values[0] for key, values in query.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<p>Google Calendar access is set. You can close this tab.</p>")

    def log_message(self, *args):
        pass


def login(open_url=True, notify=None):
    data = store.load()
    store.require_client(data)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    _Callback.result = None
    server = HTTPServer(("127.0.0.1", 0), _Callback)
    server.timeout = 1
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    url = AUTH_URL + "?" + urllib.parse.urlencode(
        {
            "client_id": data["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            # offline and consent together make Google return a refresh token on every login.
            "access_type": "offline",
            "prompt": "consent",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
    )
    (notify or _notice)(url)
    if open_url:
        open_browser(url)
    deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
    try:
        while _Callback.result is None and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    result = _Callback.result
    if result is None:
        raise GcalError(AUTH, "no login within 5 minutes", LOGIN_HINT)
    if result.get("state") != state:
        raise GcalError(AUTH, "the login answer has a wrong state value", LOGIN_HINT)
    if "error" in result:
        raise GcalError(AUTH, f"Google refused the login: {result['error']}", LOGIN_HINT)
    tokens = _post(
        TOKEN_URL,
        {
            "code": result["code"],
            "client_id": data["client_id"],
            "client_secret": data["client_secret"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
    )
    if "refresh_token" not in tokens:
        raise GcalError(AUTH, f"Google gave no refresh token: {tokens.get('error_description') or tokens.get('error')}", LOGIN_HINT)
    missing = [scope.rsplit("/", 1)[1] for scope in SCOPES if scope not in tokens.get("scope", "").split()]
    if missing:
        raise GcalError(AUTH, f"the login did not grant: {', '.join(missing)}", "run `scripts/gcal auth` again and tick every box on the Google page")
    data.update(
        refresh_token=tokens["refresh_token"],
        access_token=tokens["access_token"],
        expires_at=time.time() + tokens.get("expires_in", 3600),
    )
    store.save(data)
    return data


def _notice(url):
    print(json.dumps({"notice": "open this URL and click Allow", "url": url}), file=sys.stderr, flush=True)


def access_token(force_refresh=False):
    data = store.load()
    store.require_client(data)
    if not data.get("refresh_token"):
        raise GcalError(AUTH, "not logged in", LOGIN_HINT)
    if not force_refresh and data.get("access_token") and data.get("expires_at", 0) - EXPIRY_MARGIN_SECONDS > time.time():
        return data["access_token"]
    tokens = _post(
        TOKEN_URL,
        {
            "client_id": data["client_id"],
            "client_secret": data["client_secret"],
            "refresh_token": data["refresh_token"],
            "grant_type": "refresh_token",
        },
    )
    if "access_token" not in tokens:
        # invalid_grant: the user revoked access, or the app is in Testing mode and the token is older than 7 days.
        raise GcalError(AUTH, f"Google refused the refresh token: {tokens.get('error')}", LOGIN_HINT)
    store.update(access_token=tokens["access_token"], expires_at=time.time() + tokens.get("expires_in", 3600))
    return tokens["access_token"]


def logout():
    data = store.load()
    token = data.get("refresh_token")
    if token:
        answer = _post(REVOKE_URL, {"token": token})
        # invalid_token: Google already revoked it, so only the local copy is left to remove.
        if "_status" in answer and answer.get("error") != "invalid_token":
            raise GcalError(REMOTE, f"Google did not revoke the login: HTTP {answer['_status']}", "try logout again later")
    for key in ("refresh_token", "access_token", "expires_at"):
        data.pop(key, None)
    store.save(data)
