#!/usr/bin/env python3

import argparse
import http.cookiejar
import json
import os
import queue
import re
import ssl
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request


class MCPError(Exception):
    pass


_EXTERNAL_ENDPOINTS = {
    ("mcp.linearb.io", "/mcp"),
}
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_HEADERS = {"accept", "content-length", "content-type", "host"}


def approved_url(raw):
    parts = urllib.parse.urlsplit(raw)
    host = parts.hostname or ""
    path = urllib.parse.unquote(parts.path)
    external_endpoint = (host, path.rstrip("/") or "/") in _EXTERNAL_ENDPOINTS
    if (
        parts.scheme != "https"
        or (not host.endswith(".kraken.zone") and not external_endpoint)
        or parts.port not in (None, 443)
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or ".." in path.split("/")
    ):
        raise argparse.ArgumentTypeError(
            "URL must be an approved HTTPS endpoint without credentials, query, fragment, or traversal"
        )
    return raw.rstrip("/")


def parse_header_env(raw):
    header, separator, env_name = raw.partition("=")
    if (
        not separator
        or not _HEADER_NAME.fullmatch(header)
        or not _ENV_NAME.fullmatch(env_name)
        or header.lower() in _RESERVED_HEADERS
    ):
        raise argparse.ArgumentTypeError(
            "header mapping must be SAFE-HEADER=ENV_VAR and cannot override transport headers"
        )
    return header, env_name


def resolve_headers(mappings):
    headers = {}
    for header, env_name in mappings:
        value = os.environ.get(env_name)
        if not value:
            raise MCPError(f"required environment variable is not set: {env_name}")
        if "\r" in value or "\n" in value:
            raise MCPError(f"environment variable contains a newline: {env_name}")
        headers[header] = value
    return headers


def make_opener():
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )


def decode_response(body, content_type, request_id):
    text = body.decode("utf-8", "replace")
    if "text/event-stream" not in content_type:
        return json.loads(text) if text else None

    for event in text.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(
            line.split(":", 1)[1].lstrip()
            for line in event.splitlines()
            if line.startswith("data:")
        )
        if not data:
            continue
        message = json.loads(data)
        if request_id is None or message.get("id") == request_id:
            return message
    raise MCPError("MCP response did not contain the requested event")


class Client:
    protocol_version = "2025-03-26"

    def __init__(self, url, timeout, headers=None):
        self.url = url
        self.timeout = timeout
        self.headers = headers or {}
        self.next_id = 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def initialize(self):
        result = self.request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "mcp-cli-skill", "version": "1"},
            },
        )
        self.request("notifications/initialized", {}, notification=True)
        return result

    def tools(self):
        return self.request("tools/list", {}).get("tools", [])

    def call(self, name, arguments):
        return self.request("tools/call", {"name": name, "arguments": arguments})


class StreamableHTTPClient(Client):
    def __init__(self, url, timeout, headers=None):
        super().__init__(url, timeout, headers)
        self.opener = make_opener()
        self.session_id = None

    def request(self, method, params, notification=False):
        request_id = None if notification else self.next_id
        if request_id is not None:
            self.next_id += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            payload["id"] = request_id

        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **self.headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                self.session_id = response.headers.get("mcp-session-id", self.session_id)
                message = decode_response(
                    response.read(),
                    response.headers.get_content_type(),
                    request_id,
                )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise MCPError(f"{method} failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise MCPError(f"{method} failed: {exc.reason}") from exc
        except (json.JSONDecodeError, TimeoutError) as exc:
            raise MCPError(f"{method} returned an invalid response: {exc}") from exc

        if message and "error" in message:
            raise MCPError(f"{method} failed: {message['error']}")
        return message.get("result") if message else None


class LegacySSEClient(Client):
    protocol_version = "2024-11-05"

    def __init__(self, url, timeout, headers=None):
        super().__init__(url, timeout, headers)
        self.opener = make_opener()
        self.stream = None
        self.events = queue.Queue()
        self.endpoint = None

    def __enter__(self):
        request = urllib.request.Request(
            self.url,
            headers={"Accept": "text/event-stream", **self.headers},
            method="GET",
        )
        try:
            self.stream = self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise MCPError(f"opening SSE stream failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise MCPError(f"opening SSE stream failed: {exc.reason}") from exc
        threading.Thread(target=self._read_stream, daemon=True).start()
        self.endpoint = self._read_endpoint()
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.stream:
            self.stream.close()

    def _read_stream(self):
        event_type = None
        data_lines = []
        try:
            while True:
                raw_line = self.stream.readline()
                if not raw_line:
                    self.events.put(None)
                    return
                line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
                if not line:
                    if event_type or data_lines:
                        self.events.put(
                            {
                                "event": event_type or "message",
                                "data": "\n".join(data_lines),
                            }
                        )
                    event_type = None
                    data_lines = []
                elif line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].lstrip())
        except Exception as exc:
            self.events.put({"event": "error", "data": str(exc)})

    def _next_event(self):
        try:
            event = self.events.get(timeout=self.timeout)
        except queue.Empty as exc:
            raise MCPError("timed out waiting for SSE response") from exc
        if event is None:
            raise MCPError("SSE stream closed before a response arrived")
        if event.get("event") == "error":
            raise MCPError(f"SSE reader failed: {event.get('data', 'unknown error')}")
        return event

    def _read_endpoint(self):
        while True:
            event = self._next_event()
            if event["event"] == "endpoint" and event["data"].strip():
                return event["data"].strip()

    def _message_url(self):
        target = urllib.parse.urlsplit(urllib.parse.urljoin(self.url + "/", self.endpoint))
        base = urllib.parse.urlsplit(self.url)
        if (target.scheme, target.hostname, target.port) != (
            base.scheme,
            base.hostname,
            base.port,
        ):
            raise MCPError("SSE server returned a cross-origin message endpoint")
        return target.geturl()

    def _post(self, payload):
        request = urllib.request.Request(
            self._message_url(),
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **self.headers},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise MCPError(f"{payload['method']} failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise MCPError(f"{payload['method']} failed: {exc.reason}") from exc

    def request(self, method, params, notification=False):
        request_id = None if notification else self.next_id
        if request_id is not None:
            self.next_id += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            payload["id"] = request_id
        self._post(payload)
        if notification:
            return None

        while True:
            event = self._next_event()
            if event["event"] != "message":
                continue
            try:
                message = json.loads(event["data"])
            except json.JSONDecodeError:
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise MCPError(f"{method} failed: {message['error']}")
            return message.get("result")


def parse_arguments(raw):
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("tool arguments must be a JSON object")
    return value


def parse_args():
    parser = argparse.ArgumentParser(description="Call one approved MCP endpoint.")
    parser.add_argument("--url", required=True, type=approved_url)
    parser.add_argument(
        "--transport",
        choices=("streamable-http", "sse"),
        default="streamable-http",
    )
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument(
        "--header-env",
        action="append",
        type=parse_header_env,
        default=[],
        metavar="HEADER=ENV_VAR",
        help="read a request header value from an environment variable",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("tools", help="List tool names")

    describe = commands.add_parser("describe", help="Show one tool schema")
    describe.add_argument("tool")

    call = commands.add_parser("call", help="Call one tool")
    call.add_argument("tool")
    call.add_argument("--args", type=parse_arguments, default={})
    return parser.parse_args()


def main():
    args = parse_args()
    client_type = LegacySSEClient if args.transport == "sse" else StreamableHTTPClient
    try:
        headers = resolve_headers(args.header_env)
        with client_type(args.url, args.timeout, headers) as client:
            client.initialize()
            tools = client.tools()
            available_tools = {tool.get("name"): tool for tool in tools}
            if args.command == "tools":
                result = {"tools": sorted(available_tools)}
            else:
                tool = available_tools.get(args.tool)
                if not tool:
                    raise MCPError(f"unknown tool: {args.tool}")
                result = tool if args.command == "describe" else client.call(args.tool, args.args)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 2 if isinstance(result, dict) and result.get("isError") else 0
    except MCPError as exc:
        print(f"mcp.py: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
