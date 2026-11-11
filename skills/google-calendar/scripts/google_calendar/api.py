"""Authorized JSON requests to the Calendar and Tasks APIs, with refresh, retry, and paging."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import auth
from .errors import AUTH, NOT_FOUND, REMOTE, GcalError

CALENDAR = "https://www.googleapis.com/calendar/v3"
TASKS = "https://tasks.googleapis.com/tasks/v1"
RETRIES = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}


def quote(value):
    return urllib.parse.quote(value, safe="")


class Api:
    def __init__(self, token_source=auth.access_token, sleep=time.sleep):
        self.token_source, self.sleep = token_source, sleep

    def request(self, method, url, params=None, body=None):
        query = {key: value for key, value in (params or {}).items() if value is not None}
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        refreshed = False
        for attempt in range(RETRIES + 1):
            status, payload = self._send(method, url, body, self.token_source(force_refresh=refreshed))
            if status == 401 and not refreshed:
                refreshed = True
                continue
            # Google reports quota limits as 403 with a rate reason, not only as 429.
            if (status in RETRY_STATUSES or _rate_limited(status, payload)) and attempt < RETRIES:
                self.sleep(2**attempt)
                continue
            break
        if status < 300:
            return payload
        raise _error(status, payload, method, url)

    def _send(self, method, url, body, token):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return error.code, {"error": {"message": raw.decode(errors="replace")[:300]}}
        except (urllib.error.URLError, TimeoutError) as error:
            raise GcalError(REMOTE, f"cannot reach Google: {error}", "check the network, then try again") from error

    def get(self, url, **params):
        return self.request("GET", url, params)

    def pages(self, url, key="items", limit=None, **params):
        items, token = [], None
        while True:
            page = self.request("GET", url, {**params, "pageToken": token})
            items += page.get(key, [])
            token = page.get("nextPageToken")
            if not token or (limit and len(items) >= limit):
                return items[:limit] if limit else items


def _rate_limited(status, payload):
    reasons = {item.get("reason") for item in payload.get("error", {}).get("errors", [])}
    return status == 403 and bool(reasons & {"rateLimitExceeded", "userRateLimitExceeded"})


def _error(status, payload, method, url):
    detail = payload.get("error", {})
    message = detail.get("message") if isinstance(detail, dict) else str(detail)
    reasons = {item.get("reason") for item in detail.get("errors", [])} if isinstance(detail, dict) else set()
    target = url.split("?")[0].split("/v3/")[-1].split("/v1/")[-1]
    if status == 401:
        return GcalError(AUTH, "Google refused the access token", auth.LOGIN_HINT)
    if status == 403 and ("accessNotConfigured" in reasons or "has not been used in project" in (message or "")):
        return GcalError(REMOTE, message, "enable the Calendar API and the Tasks API in the Google Cloud project (references/setup.md, stage 3)")
    if status == 403 and reasons & {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}:
        return GcalError(AUTH, message, auth.LOGIN_HINT + "; tick every box on the Google page")
    if status in (404, 410):
        return GcalError(NOT_FOUND, f"not found: {target}", "list the items again and use an id from the output")
    return GcalError(REMOTE, f"Google answered {status} to {method} {target}: {message}")
