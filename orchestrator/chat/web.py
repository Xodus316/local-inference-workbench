"""Same-origin chat UI for the loopback service."""

from __future__ import annotations

import ipaddress
import json
import math
import re
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from .service import ChatError


STATIC = Path(__file__).parent / "static"
MAX_BODY_BYTES = 1_000_000
MAX_UPLOAD_BODY_BYTES = 14 * 1024 * 1024
TERMINAL = {"complete", "stopped", "error", "interrupted"}


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON fields are not allowed")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("JSON numbers must be finite")


def _number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON numbers must be finite")
    return number


class ChatWeb:
    def __init__(self, service: Any) -> None:
        self.service = service

    def _host(self, handler: Any) -> str:
        hosts = handler.headers.get_all("Host", [])
        if len(hosts) != 1:
            raise ChatError(403, "invalid_host", "A valid service Host header is required")
        host = hosts[0]
        scheme = getattr(handler.server, "scheme", "http")
        try:
            parsed = urlsplit(scheme + "://" + host)
            if (parsed.username is not None or parsed.password is not None or parsed.path
                    or parsed.query or parsed.fragment or "," in host or any(char.isspace() for char in host)):
                raise ValueError
            hostname = parsed.hostname
            port = parsed.port or (443 if scheme == "https" else 80)
            bound = handler.server.server_address[0]
            valid = hostname == bound
            if bound in ("127.0.0.1", "::1", "localhost"):
                valid = hostname in ("127.0.0.1", "::1", "localhost")
            elif bound in ("0.0.0.0", "::"):
                valid = hostname == "localhost" or ipaddress.ip_address(hostname).is_private
            if not valid or port != handler.server.server_port:
                raise ValueError
        except (ValueError, TypeError):
            raise ChatError(403, "invalid_host", "Open the chat using this PC's service address") from None
        origins = handler.headers.get_all("Origin", [])
        if len(origins) > 1 or (origins and origins[0] != scheme + "://" + host):
            raise ChatError(403, "foreign_origin", "Requests must come from the same chat interface")
        fetch_site = handler.headers.get("Sec-Fetch-Site")
        if fetch_site and fetch_site not in ("same-origin", "none"):
            raise ChatError(403, "foreign_origin", "Cross-site chat requests are not allowed")
        return host

    def _headers(self, handler: Any, status: int, content_type: str, length: int | None = None,
                 filename: str | None = None) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        if length is not None:
            handler.send_header("Content-Length", str(length))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.send_header("X-Frame-Options", "DENY")
        handler.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: blob:; font-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        if filename:
            fallback = re.sub(r"[^A-Za-z0-9._ -]", "_", filename) or "attachment"
            handler.send_header("Content-Disposition", f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.close_connection = True

    def _json(self, handler: Any, status: int, value: Any) -> None:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self._headers(handler, status, "application/json; charset=utf-8", len(encoded))
        handler.wfile.write(encoded)

    def _error(self, handler: Any, error: ChatError) -> None:
        self._json(handler, error.status, {"error": {"code": error.code, "message": str(error)}})

    def _body(self, handler: Any, allowed: set[str], required: set[str] = frozenset(),
              *, maximum: int = MAX_BODY_BYTES) -> dict[str, Any]:
        if handler.headers.get_all("Transfer-Encoding"):
            raise ChatError(400, "invalid_body", "Transfer-Encoding is not supported")
        lengths = handler.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            raise ChatError(400, "invalid_body", "One Content-Length header is required")
        limit_message = "Upload request exceeds the 14 MiB limit" if maximum == MAX_UPLOAD_BODY_BYTES else "Chat request exceeds the 1 MB limit"
        if len(lengths[0]) > 9:
            raise ChatError(413, "body_too_large", limit_message)
        length = int(lengths[0])
        if not 0 < length <= maximum:
            raise ChatError(413, "body_too_large", limit_message)
        types = handler.headers.get_all("Content-Type", [])
        if len(types) != 1 or types[0].split(";", 1)[0].strip().lower() != "application/json":
            raise ChatError(415, "invalid_content_type", "Use application/json")
        handler.connection.settimeout(30 if maximum == MAX_UPLOAD_BODY_BYTES else 10)
        try:
            encoded = handler.rfile.read(length)
            if len(encoded) != length:
                raise ValueError("Incomplete request body")
            decoded = encoded.decode("utf-8")
            value = json.loads(decoded, object_pairs_hook=_object, parse_constant=_constant, parse_float=_number)
            # Reject escaped lone surrogates before they reach UTF-8 storage.
            json.dumps(value, ensure_ascii=False).encode("utf-8")
        except (ValueError, UnicodeDecodeError, TimeoutError, OSError, RecursionError):
            raise ChatError(400, "invalid_json", "Request must contain valid, unambiguous JSON") from None
        if not isinstance(value, dict) or set(value) - allowed or required - set(value):
            raise ChatError(400, "invalid_fields", "Request contains missing or unsupported fields")
        return value

    @staticmethod
    def _query(handler: Any, allowed: set[str]) -> dict[str, str]:
        query = parse_qs(urlsplit(handler.path).query, keep_blank_values=True)
        if set(query) - allowed or any(len(value) != 1 for value in query.values()):
            raise ChatError(400, "invalid_query", "Unsupported or repeated query fields")
        return {key: values[0] for key, values in query.items()}

    def get(self, handler: Any) -> None:
        try:
            self._host(handler)
            path = urlsplit(handler.path).path
            assets = {"/chat/": ("index.html", "text/html; charset=utf-8"),
                      "/chat": ("index.html", "text/html; charset=utf-8"),
                      "/chat/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/chat/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in assets:
                self._query(handler, set())
                name, content_type = assets[path]
                encoded = (STATIC / name).read_bytes()
                self._headers(handler, 200, content_type, len(encoded))
                handler.wfile.write(encoded)
                return
            if path == "/chat/api/state":
                query = self._query(handler, {"conversation_id", "q"})
                value = self.service.state(query.get("conversation_id") or None, query.get("q", ""))
                self._json(handler, 200, value)
            elif path == "/chat/api/models":
                query = self._query(handler, {"refresh"})
                if query.get("refresh", "") not in ("", "0", "1"):
                    raise ChatError(400, "invalid_query", "refresh must be 0 or 1")
                self._json(handler, 200, self.service.models(refresh=query.get("refresh") == "1"))
            elif path == "/chat/api/attachment":
                query = self._query(handler, {"attachment_id", "download"})
                if not query.get("attachment_id") or query.get("download", "") not in ("", "0", "1"):
                    raise ChatError(400, "invalid_query", "attachment_id is required; download must be 0 or 1")
                attachment = self.service.attachment(query["attachment_id"])
                metadata, encoded = attachment["attachment"], attachment["body"]
                is_image = metadata["kind"] == "image" and metadata["media_type"] in ("image/png", "image/jpeg", "image/webp")
                download = query.get("download") == "1" or not is_image
                self._headers(handler, 200, metadata["media_type"] if is_image else "application/octet-stream",
                              len(encoded), metadata["name"] if download else None)
                handler.wfile.write(encoded)
            elif path == "/chat/api/stream":
                query = self._query(handler, {"job_id"})
                if not query.get("job_id"):
                    raise ChatError(400, "invalid_query", "job_id is required")
                self._stream(handler, query["job_id"])
            else:
                raise ChatError(404, "not_found", "Chat endpoint not found")
        except ChatError as error:
            self._error(handler, error)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception:
            self._json(handler, 500, {"error": {"code": "internal_error", "message": "The chat request failed; saved conversations were preserved"}})

    def post(self, handler: Any) -> None:
        try:
            self._host(handler)
            self._query(handler, set())
            path = urlsplit(handler.path).path
            if path == "/chat/api/upload":
                body = self._body(handler, {"name", "content_base64"}, {"name", "content_base64"}, maximum=MAX_UPLOAD_BODY_BYTES)
                self._json(handler, 200, self.service.upload(**body))
                return
            operations = {
                "/chat/api/create": (self.service.create, {"model", "settings", "title"}, {"model"}),
                "/chat/api/update": (self.service.update, {"conversation_id", "title", "model", "settings", "revision"}, {"conversation_id"}),
                "/chat/api/delete": (self.service.delete, {"conversation_id", "revision"}, {"conversation_id"}),
                "/chat/api/send": (self.service.send, {"conversation_id", "content", "revision", "request_id", "attachment_ids"}, {"conversation_id", "content"}),
                "/chat/api/regenerate": (self.service.regenerate, {"conversation_id", "revision", "request_id"}, {"conversation_id"}),
                "/chat/api/edit": (self.service.edit, {"conversation_id", "message_id", "content", "revision", "request_id", "attachment_ids"}, {"conversation_id", "message_id", "content"}),
                "/chat/api/stop": (self.service.stop, {"job_id"}, {"job_id"}),
                "/chat/api/remove-upload": (self.service.remove_upload, {"attachment_id"}, {"attachment_id"}),
            }
            if path not in operations:
                raise ChatError(404, "not_found", "Chat endpoint not found")
            operation, allowed, required = operations[path]
            body = self._body(handler, allowed, required)
            self._json(handler, 200, operation(**body))
        except ChatError as error:
            self._error(handler, error)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception:
            self._json(handler, 500, {"error": {"code": "internal_error", "message": "The chat request failed; saved conversations were preserved"}})

    def _stream(self, handler: Any, job_id: str) -> None:
        snapshot = self.service.job(job_id)
        self._headers(handler, 200, "application/x-ndjson; charset=utf-8")
        handler.connection.settimeout(10)
        last = ""
        sent = time.monotonic()
        first = True
        try:
            while True:
                job = snapshot["job"]
                terminal = job["status"] in TERMINAL
                if first or terminal:
                    item = snapshot
                else:
                    message = next((message for message in snapshot["conversation"]["messages"]
                                    if message["id"] == job["assistant_message_id"]), None)
                    item = {"job": job, "message": message, "revision": snapshot["conversation"]["revision"]}
                encoded = json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                if encoded != last:
                    handler.wfile.write((encoded + "\n").encode("utf-8"))
                    handler.wfile.flush()
                    last = encoded
                    sent = time.monotonic()
                elif time.monotonic() - sent >= 10:
                    handler.wfile.write(b'{"heartbeat":true}\n')
                    handler.wfile.flush()
                    sent = time.monotonic()
                if terminal:
                    return
                first = False
                time.sleep(0.25)
                snapshot = self.service.job(job_id)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            # Navigation/disconnection detaches this viewer, not the PC worker.
            return
        except (ChatError, ValueError, TypeError, RecursionError):
            return
