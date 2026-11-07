"""Keyless retrieval through Exa's hosted Streamable HTTP MCP search tool."""
import json
import re
import socket
import time
from contextlib import closing, suppress
from datetime import UTC, datetime
from http.client import HTTPException, HTTPSConnection
from threading import Timer

from .research import WebResearch

_HOST = "mcp.exa.ai"
_VERSION = "2025-06-18"
_MAX_BYTES = 512_000
_TOOL = "web_search_exa"
_HEADER = re.compile(r"Title: (?P<title>[^\n]+)\nURL: (?P<url>[^\n]+)\n"
                     r"Published:[^\n]*\nAuthor:[^\n]*\nHighlights:\n")
_OBJECTIVE = "Find public primary sources that directly answer this query. Return supporting passages."


class _Failure(ValueError):
    """Message is a safe local error code; remote error text is never interpolated."""


def _read_response(response, message_id):
    kind = response.headers.get_content_type()
    if kind == "application/json":
        body = response.read(_MAX_BYTES + 1)
        if len(body) > _MAX_BYTES:
            raise _Failure("response_too_large")
        return json.loads(body)
    if kind != "text/event-stream":
        raise _Failure("invalid_provider_response")

    # A server may keep the stream open after its terminal JSON-RPC response.
    remaining = _MAX_BYTES
    event = []
    while remaining:
        line = response.readline(remaining + 1)
        remaining -= len(line)
        if remaining < 0:
            raise _Failure("response_too_large")
        if not line:
            break
        line = line.rstrip(b"\r\n")
        if line.startswith(b"data:"):
            event.append(line[5:].lstrip(b" "))
        elif not line and event:
            value = json.loads(b"\n".join(event))
            if not isinstance(value, dict):
                raise _Failure("invalid_provider_response")
            if value.get("id") == message_id:
                return value
            event.clear()
    raise _Failure("response_too_large" if not remaining else "invalid_provider_response")


class _Exchange:
    def __init__(self, seconds):
        self.deadline = time.monotonic() + seconds
        self.session = None

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _Failure("provider_timeout")
        return remaining

    def send(self, method, params, message_id=None):
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if method != "initialize":
            headers["MCP-Protocol-Version"] = _VERSION
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if message_id is not None:
            payload["id"] = message_id

        # This fixed HTTPS connection neither sends ambient credentials nor follows redirects.
        with closing(HTTPSConnection(_HOST, timeout=self.remaining())) as conn:
            conn.connect()
            sock = conn.sock

            def interrupt():
                with suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)

            # Bound slowly trickling headers or SSE lines as well as idle sockets.
            timer = Timer(self.remaining(), interrupt)
            timer.daemon = True
            timer.start()
            try:
                conn.request("POST", "/mcp", body=json.dumps(payload).encode("utf-8"), headers=headers)
                with conn.getresponse() as response:
                    if response.status == 429:
                        raise _Failure("provider_rate_limited")
                    if response.status in (401, 403):
                        raise _Failure("provider_access_denied")
                    if message_id is None and response.status == 202:
                        return None
                    if response.status != 200 or message_id is None:
                        raise _Failure("web_search_failed")
                    if method == "initialize":
                        session = response.getheader("Mcp-Session-Id")
                        if session is not None and not re.fullmatch(r"[!-~]{1,512}", session):
                            raise _Failure("invalid_provider_response")
                        self.session = session
                    value = _read_response(response, message_id)
            finally:
                timer.cancel()
        self.remaining()
        if (not isinstance(value, dict) or value.get("jsonrpc") != "2.0"
                or type(value.get("id")) is not int or value["id"] != message_id):
            raise _Failure("invalid_provider_response")
        if "error" in value:
            raise _Failure("provider_rpc_error")
        if not isinstance(value.get("result"), dict):
            raise _Failure("invalid_provider_response")
        return value["result"]


class ExaResearch(WebResearch):
    name = "exa"

    def readiness(self):
        return {"provider": self.name, "available": self.config.enabled,
                "reason": None if self.config.enabled else "disabled"}

    def _fetch(self, query):
        if not self.config.enabled:
            return {"error": "disabled"}
        connection = _Exchange(self.config.timeout_seconds)
        try:
            info = connection.send("initialize", {
                "protocolVersion": _VERSION, "capabilities": {},
                "clientInfo": {"name": "local-inference-workbench", "version": "1.0"}}, 1)
            if info.get("protocolVersion") != _VERSION:
                raise _Failure("unsupported_search_protocol")
            connection.send("notifications/initialized", {})
            catalog = connection.send("tools/list", {}, 2)
            tool = next((t for t in catalog["tools"] if isinstance(t, dict) and t.get("name") == _TOOL), None)
            arguments = {"query": query, "numResults": self.config.results_per_query, "objective": _OBJECTIVE}
            if tool is None:
                raise _Failure("search_tool_unavailable")
            schema = tool["inputSchema"]
            if not (set(schema.get("required", [])) <= arguments.keys() <= schema["properties"].keys()):
                raise _Failure("incompatible_search_tool")
            result = connection.send("tools/call", {"name": _TOOL, "arguments": arguments}, 3)
            receipt = {"body": json.dumps(result, ensure_ascii=False),
                       "retrieved_at": datetime.now(UTC).isoformat()}
            if result.get("isError"):
                receipt["error"] = "provider_tool_error"
            return receipt
        except _Failure as error:
            return {"error": str(error)}
        except TimeoutError:
            return {"error": "provider_timeout"}
        except (HTTPException, OSError):
            return {"error": "web_search_failed"}
        except (ValueError, AttributeError, KeyError, TypeError):
            return {"error": "invalid_provider_response"}

    @staticmethod
    def results(body):
        rows = []
        blocks = body["content"]
        if not isinstance(blocks, list):
            raise ValueError("Invalid search content")
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "text" or not isinstance(block.get("text"), str):
                raise ValueError("Invalid search content")
            text = block["text"].replace("\r\n", "\n").strip()
            if not text or re.fullmatch(r"No (?:search )?results(?: found)?[.!]?", text, re.IGNORECASE):
                continue
            for item in re.split(r"\n---\n\n(?=Title: )", text):
                header = _HEADER.match(item)
                if not header:
                    raise ValueError("Unrecognized search result")
                rows.append({**header.groupdict(), "content": item[header.end():].strip()})
        return rows

    @staticmethod
    def excerpt(result):
        return " ".join(result["content"].split()).encode("utf-8")[:1200].decode("utf-8", errors="ignore")
