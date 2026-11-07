"""Loopback-only HTTP interface for the model service."""
import json
import re
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from pydantic import ValidationError as SchemaError
from .ollama import OllamaError, _strict_json


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, runtime):
        self.runtime = runtime
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "InferenceWorkbench"

    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def json_response(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def json_body(self):
        sizes = self.headers.get_all("Content-Length", [])
        if len(sizes) != 1 or not re.fullmatch(r"[0-9]{1,8}", sizes[0]):
            raise ValueError("Supply a bounded JSON body")
        size = int(sizes[0])
        if size > 8_000_000:
            raise ValueError("Request body is too large")
        data = self.rfile.read(size)
        if len(data) != size:
            raise ValueError("Incomplete body")
        value = _strict_json(data.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value

    def _handle(self, method):
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host")
        origins = self.headers.get_all("Origin", [])
        if (host not in hosts or len(self.headers.get_all("Host", [])) != 1
                or (origins and origins != ["http://" + host])
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            self.json_response(403, {"error":"Local origin required"})
            return
        if method == "POST" and (self.headers.get_content_type() != "application/json"
                                 or self.headers.get("Transfer-Encoding")):
            self.json_response(415, {"error":"Expected application/json"})
            return
        try:
            path = urlsplit(self.path).path
            from .panel import dispatch_ui
            if dispatch_ui(self, method, path):
                return
            result = self._api(method, path)
        except KeyError:
            self.json_response(404, {"error":"Unknown resource"})
        except SchemaError as exc:
            self.json_response(400, {"error":"Invalid fields", "fields":[list(e["loc"]) for e in exc.errors()]})
        except (ValueError, UnicodeError) as exc:
            self.json_response(400, {"error":str(exc)[:300]})
        except OllamaError:
            self.json_response(503, {"error":"Local model backend unavailable"})
        except Exception:
            self.json_response(500, {"error":"Service operation failed"})
        else:
            self.json_response(200, result)

    def _api(self, method, path):
        runtime = self.server.runtime
        if method == "GET":
            if path == "/api/status":
                return {"roles":runtime.config.roles, "test_commands": list(runtime.config.test_commands), "active_run_id":runtime.active_run_id,
                        "queued":runtime.queue.qsize(), "inference_active":runtime.inference_lock.locked(),
                        "runs":runtime.store.list()}
            if path == "/api/models":
                return {"models":runtime.provider.ollama.models()}
            if path == "/api/runs":
                return {"runs":runtime.store.list()}
        if method == "POST" and path == "/api/runs":
            return runtime.prepare(self.json_body())
        match = re.fullmatch(r"/api/runs/([0-9a-f-]{36})(?:/(approve|resume|apply|cancel))?", path)
        if not match:
            raise KeyError(path)
        run_id, action = match.groups()
        uuid.UUID(run_id)
        if method == "GET" and action is None:
            return runtime.store.get(run_id)
        if method == "POST" and action:
            payload = self.json_body()
            if action == "cancel" and payload == {}:
                return runtime.cancel(run_id)
            if set(payload) != {"digest"} or not isinstance(payload["digest"], str):
                raise ValueError("Expected approval digest")
            if action in {"approve","resume"}:
                return runtime.approve(run_id, payload["digest"], resume=action=="resume")
            if action == "apply":
                return runtime.apply(run_id, payload["digest"])
        raise KeyError(path)

    def do_GET(self):
        self.connection.settimeout(30)
        self._handle("GET")

    def do_POST(self):
        self.connection.settimeout(30)
        self._handle("POST")
