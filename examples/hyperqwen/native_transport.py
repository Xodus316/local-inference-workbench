"""Relay one private OpenCode session to an owned HyperQwen vLLM endpoint."""

from __future__ import annotations

from contextlib import closing, contextmanager
from copy import deepcopy
import hmac
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
import re
import secrets
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from native_launch_profile import MAX_FRAME_BYTES, MAX_STREAM_BYTES, encode
from native_import_guard import assert_public_runtime
assert_public_runtime()
from orchestrator.ollama import _strict_json
import select
import socket

def _watch_disconnect(downstream, upstream, done, stopping):
    while not done.is_set():
        try:
            readable, _, _ = select.select([downstream], [], [], .1)
            if not stopping.is_set() and not readable:
                continue
            if not stopping.is_set() and downstream.recv(1, socket.MSG_PEEK):
                # This session uses Connection: close. Ignore pipelined traffic.
                return
        except OSError:
            pass
        if not done.is_set():
            try:
                upstream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        return


MODELS = "/v1/models"
CHAT = "/v1/chat/completions"
EFFORT = "medium"
TURN_EFFORTS = {EFFORT, "xhigh"}
TOKEN_CAPS = ("max_tokens", "max_completion_tokens")
REQUEST_FIELDS = {
    "model", "messages", "stream", "stream_options", "tools", "tool_choice", "parallel_tool_calls",
    "max_tokens", "max_completion_tokens", "reasoning_effort", "chat_template_kwargs", "temperature",
    "top_p", "top_k", "min_p", "repetition_penalty", "n", "stop", "seed", "user", "frequency_penalty",
    "presence_penalty", "response_format",
}
FINISH_REASONS = {"stop", "tool_calls", "length", "content_filter", "function_call"}
SERVER_METRICS = ("time_to_first_token_ms", "generation_time_ms", "queue_time_ms", "mean_itl_ms",
                  "tokens_per_second", "prefill_time_ms", "decode_time_ms")
SPECULATIVE_METRICS = ("mean_acceptance_length", "draft_acceptance_rate", "num_spec_steps",
                       "num_accepted_draft_tokens", "num_draft_tokens", "num_spec_tokens")


class HyperQwenBridgeError(ValueError):
    """A bounded private HyperQwen transport failure."""


class HyperQwenGenerationPolicyError(HyperQwenBridgeError):
    """An upstream-completed response failed its approved generation budget."""


def validate_model_listing(listing, model, context, model_root, *, aliases=None):
    """Aliases may identify one deployment; unrelated or ambiguous models may not."""
    expected = [model] if aliases is None else aliases
    records = listing.get('data') if isinstance(listing,dict) else None
    if (not isinstance(expected,list) or not expected or model not in expected
            or any(not isinstance(alias,str) or not alias for alias in expected)
            or len(set(expected)) != len(expected)
            or not isinstance(records,list) or len(records) != len(expected)
            or any(not isinstance(row,dict) or not isinstance(row.get('id'),str) for row in records)):
        raise HyperQwenBridgeError('Owned model listing differs from the approved alias inventory')
    if (set(row['id'] for row in records) != set(expected)
            or any(row.get('max_model_len') != context
                   or model_root is not None and row.get('root') != model_root for row in records)):
        raise HyperQwenBridgeError('Owned model listing differs from the approved aliases, root or context')
    return next(row for row in records if row['id']==model)


def _generation_policy(stage, effort, value):
    if value is None:
        return None
    from native_adapter import generation_policy
    expected = generation_policy(stage, value.get('role'), value.get('tool_policy'))
    if effort != 'xhigh' or value != expected:
        raise HyperQwenBridgeError('Require the exact approved fresh-role generation policy')
    return deepcopy(value)



def _generation_usage(result, policy):
    usage = result.get("usage") or {}
    required = ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens")
    if (not all(type(usage.get(key)) is int and usage[key] >= 0 for key in required)
            or usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]
            or usage["reasoning_tokens"] > usage["completion_tokens"]
            or usage["total_tokens"] > result["context"]):
        raise HyperQwenGenerationPolicyError("Completed smoke response lacks consistent terminal token-usage proof")
    # qwen3's forced reasoning terminator can consume a small parser allowance.
    if (usage["reasoning_tokens"] > policy["thinking_token_budget"] + 32
            or usage["completion_tokens"] > policy["max_completion_tokens"]):
        raise HyperQwenGenerationPolicyError("Completed smoke response exceeded its approved reasoning or completion budget")
    result["generation_policy_verified"] = True


def _body(payload, model, context, request_id, effort, generation_policy=None):
    body = _strict_json(payload.decode("utf-8"))
    if not isinstance(body, dict) or set(body) - REQUEST_FIELDS:
        raise HyperQwenBridgeError("Unsupported OpenCode request fields")
    if (body.get("model") != model or body.get("stream") is not True
            or type(body.get("n", 1)) is not int or body.get("n", 1) != 1
            or not isinstance(body.get("messages"), list) or not body["messages"]):
        raise HyperQwenBridgeError("The HyperQwen bridge requires one streamed choice for the pinned alias")
    template = body.get("chat_template_kwargs", {})
    options = body.get("stream_options", {})
    if (not isinstance(template, dict) or set(template) - {"enable_thinking", "reasoning_effort"}
            or not isinstance(options, dict) or set(options) - {"include_usage", "continuous_usage_stats"}):
        raise HyperQwenBridgeError("Unsupported HyperQwen template or stream options")
    if generation_policy is not None and any(field in body and (type(body[field]) is not int
            or not 1 <= body[field] <= generation_policy["max_completion_tokens"]) for field in TOKEN_CAPS):
        raise HyperQwenBridgeError("OpenCode cannot override the approved smoke completion budget")
    for field in TOKEN_CAPS:
        # A ceiling equal to the entire context asks the model for all room
        # available. vLLM discovers that room from the rendered prompt.
        if type(body.get(field)) is int and body[field] == context:
            del body[field]
    from native_adapter import is_opencode_compaction
    if generation_policy is not None and not is_opencode_compaction(body):
        schema_format = body.get("response_format")
        schema = schema_format.get("json_schema") if isinstance(schema_format, dict) else None
        if (body.get("tools") not in (None, []) or body.get("tool_choice") not in (None, "none")
                or body.get("parallel_tool_calls") not in (None, False)
                or any(not isinstance(message, dict) or message.get("role") in {"tool", "function"}
                       or message.get("tool_calls") or message.get("function_call") for message in body["messages"])
                or not isinstance(schema_format, dict) or schema_format.get("type") != "json_schema"
                or not isinstance(schema, dict) or schema.get("strict") is not True
                or not isinstance(schema.get("schema"), dict) or schema["schema"].get("type") != "object"):
            raise HyperQwenBridgeError("Bounded smoke generation requires one strict JSON-schema response without tools")
        for field in TOKEN_CAPS:
            body.pop(field, None)
        body["max_tokens"] = generation_policy["max_completion_tokens"]
        body["thinking_token_budget"] = generation_policy["thinking_token_budget"]
    body.setdefault("top_k", 20)
    body.update(request_id=request_id, reasoning_effort=effort,
                chat_template_kwargs={"enable_thinking": True, "reasoning_effort": effort},
                stream_options={**options, "include_usage": True})
    return body


def _events(response):
    raw_size = event_size = 0
    lines, data = [], []
    while line := response.readline(MAX_FRAME_BYTES + 1):
        raw_size += len(line)
        event_size += len(line)
        if len(line) > MAX_FRAME_BYTES or event_size > MAX_FRAME_BYTES or raw_size > MAX_STREAM_BYTES:
            raise HyperQwenBridgeError("HyperQwen exceeded its reviewed SSE transport bounds")
        lines.append(line)
        part = line.rstrip(b"\r\n")
        if part.startswith(b"data:"):
            data.append(part[5:].removeprefix(b" "))
        if not part:
            yield b"".join(lines), b"\n".join(data) if data else None
            lines.clear()
            data.clear()
            event_size = 0
    raise HyperQwenBridgeError("The HyperQwen stream ended before [DONE]")


def _counts(value, names):
    if not isinstance(value, dict):
        return {}
    return {name: value[name] for name in names if type(value.get(name)) is int and value[name] >= 0}


def _numeric_metrics(value, names):
    if not isinstance(value, dict):
        return {}
    return {name: value[name] for name in names if type(value.get(name)) in (int, float)
            and 0 <= value[name] and math.isfinite(value[name])}


def _observe(frame, result, model, started):
    if not isinstance(frame, dict) or "error" in frame:
        raise HyperQwenBridgeError("HyperQwen reported a streaming error")
    request_id = result["request_id"]
    if frame.get("model") != model or frame.get("id") != f"chatcmpl-{request_id}":
        raise HyperQwenBridgeError("The completion stream differs from the pinned HyperQwen request")
    if frame.get("usage") is not None:
        usage = frame["usage"]
        if not isinstance(usage, dict):
            raise HyperQwenBridgeError("HyperQwen returned malformed completion usage")
        result["usage"] = {
            **_counts(usage, ("prompt_tokens", "completion_tokens", "total_tokens")),
            **_counts(usage.get("prompt_tokens_details"), ("cached_tokens", "created_cache_tokens")),
            **_counts(usage.get("completion_tokens_details"), ("reasoning_tokens",)),
        }
    if isinstance(frame.get("metrics"), dict):
        metrics = frame["metrics"]
        result["server_metrics"] = _numeric_metrics(metrics, SERVER_METRICS)
        spec = _numeric_metrics(metrics.get("speculative_decoding"), SPECULATIVE_METRICS)
        if spec:
            result["server_metrics"]["speculative_decoding"] = spec

    choices = frame.get("choices")
    if choices == []:
        return False
    choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else None
    if not isinstance(choice, dict) or type(choice.get("index")) is not int or choice["index"] != 0:
        raise HyperQwenBridgeError("HyperQwen did not return one streamed choice")
    delta = choice.get("delta")
    if not isinstance(delta, dict) or result["finish_reason"] is not None:
        raise HyperQwenBridgeError("HyperQwen returned an invalid or extra streamed choice")
    if result["seconds_to_first_output"] is None and any(delta.get(key) for key in (
            "content", "reasoning", "reasoning_content", "tool_calls", "function_call")):
        result["seconds_to_first_output"] = round(time.monotonic() - started, 3)
    if (finish := choice.get("finish_reason")) is not None:
        if not isinstance(finish, str) or finish not in FINISH_REASONS:
            raise HyperQwenBridgeError("HyperQwen returned an unrecognized finish reason")
        result["finish_reason"] = finish
    if isinstance(delta.get("reasoning"), str) and not delta.get("reasoning_content"):
        # AI SDK's OpenAI-compatible parser can consume the standard alias.
        # Preserve vLLM's own reasoning key as well as all other delta fields.
        delta["reasoning_content"] = delta["reasoning"]
        return True
    return False


class _Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, *args):
        pass

    def _authenticate(self):
        keys = self.headers.get_all("Authorization", [])
        if len(keys) == 1 and hmac.compare_digest(keys[0].encode("latin-1"), self.server.bridge._authorization):
            return True
        self.reply(401, {"error": {"message": "The private HyperQwen bridge requires its trial credential"}})
        return False

    def reply(self, status, body):
        data = encode(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._authenticate():
            return
        if self.path != MODELS:
            self.reply(404, {"error": {"message": "Unknown private HyperQwen route"}})
            return
        try:
            row = self.server.bridge.model_record()
        except (OSError, HTTPException, ValueError, RecursionError):
            self.reply(502, {"error": {"message": "The owned HyperQwen model listing could not be verified"}})
            return
        self.reply(200, {"object": "list", "data": [row]})

    def do_POST(self):
        if not self._authenticate():
            return
        if self.path != CHAT:
            self.reply(404, {"error": {"message": "Unknown private HyperQwen route"}})
            return
        lengths = self.headers.get_all("Content-Length", [])
        if (len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal()
                or len(lengths[0]) > 10 or self.headers.get("Transfer-Encoding") is not None):
            self.reply(400, {"error": {"message": "Provide one valid Content-Length"}})
            return
        length = int(lengths[0])
        if not 0 < length <= MAX_STREAM_BYTES:
            self.reply(413, {"error": {"message": "The HyperQwen HTTP body exceeded the transport bound"}})
            return
        self.server.bridge.dispatch(self, length)

    def start_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()


class _Server(ThreadingHTTPServer):
    daemon_threads = False


class HyperQwenOpenCodeBridge:
    """Keep OpenCode's API credential and per-request measurements private to the trial."""

    def __init__(self, upstream_url, upstream_api_key, *, model, context, directory, backend_identity=None):
        upstream = urlsplit(upstream_url)
        if (upstream.scheme != "http" or upstream.netloc != f"127.0.0.1:{upstream.port}"
                or upstream.port is None or upstream.path or upstream.query or upstream.fragment):
            raise HyperQwenBridgeError("Use an owned IPv4 loopback upstream endpoint")
        if (not isinstance(upstream_api_key, str) or not re.fullmatch(r"[!-~]{16,}", upstream_api_key)
                or not isinstance(model, str) or not model or "/" in model or any(c.isspace() for c in model)
                or type(context) is not int or context < 2048):
            raise HyperQwenBridgeError("Supply the HyperQwen trial's key, exact alias and context")
        self._port = upstream.port
        self._upstream_headers = {"Authorization": f"Bearer {upstream_api_key}"}
        self._identity = {**deepcopy(backend_identity or {}), "name": "hyperqwen-vllm", "model": model,
                          "context": context, "upstream_endpoint": upstream_url, "reasoning_effort": EFFORT}
        self.model, self.context = model, context
        self.directory = directory
        self.api_key = secrets.token_urlsafe(32)
        self._authorization = f"Bearer {self.api_key}".encode("ascii")
        self._dispatch = threading.Lock()
        self._turn_profile = None
        self._stopping = threading.Event()
        self._failure = False
        self._unsettled = False
        self._request_count = 0

    @property
    def request_count(self):
        return self._request_count

    def model_record(self):
        connection = HTTPConnection("127.0.0.1", self._port, timeout=5)
        try:
            connection.request("GET", MODELS, headers=self._upstream_headers)
            with connection.getresponse() as response:
                raw = response.read(MAX_FRAME_BYTES + 1)
                if response.status != 200 or len(raw) > MAX_FRAME_BYTES:
                    raise HyperQwenBridgeError("The owned HyperQwen model listing failed")
        finally:
            connection.close()
        listing = _strict_json(raw.decode("utf-8"))
        validate_model_listing(listing, self.model, self.context, self._identity.get('model_root'),
                               aliases=self._identity.get('served_aliases'))
        return {"id": self.model, "object": "model", "owned_by": "vllm", "max_model_len": self.context}

    def identity(self):
        self.model_record()
        return deepcopy(self._identity)

    def resident(self):
        self.ready()
        return deepcopy(self._identity)

    def ready(self):
        if self._failure or self._stopping.is_set() or self._dispatch.locked():
            raise HyperQwenBridgeError("The private HyperQwen bridge has an unsettled or failed request")
        self.model_record()

    def wait_cleanup(self):
        # Wait for the connection to close and distinguish [DONE] from an
        # interrupted request. The launch owner verifies process exit separately.
        with self._dispatch:
            if self._unsettled:
                raise HyperQwenBridgeError("The HyperQwen upstream connection closed before [DONE]")

    @contextmanager
    def turn_profile(self, stage, effort, *, generation_policy=None):
        """Bind every request in one synchronous OpenCode turn to an effort."""
        if (not isinstance(stage, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", stage)
                or not isinstance(effort, str) or effort not in TURN_EFFORTS):
            raise HyperQwenBridgeError("Require a workflow stage label and medium or xhigh effort")
        generation_policy = _generation_policy(stage, effort, generation_policy)
        with self._dispatch:
            if self._turn_profile is not None or self._failure or self._stopping.is_set():
                raise HyperQwenBridgeError("The HyperQwen bridge cannot start another turn profile")
            self._turn_profile = (stage, effort, generation_policy)
        try:
            yield
        finally:
            # Let an accepted request finish with its original profile before
            # restoring the medium default, even if the worker raised.
            with self._dispatch:
                self._turn_profile = None

    def __enter__(self):
        self.directory.mkdir(exist_ok=False)
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.bridge = self
        self.endpoint = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stopping.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()

    def dispatch(self, handler, length):
        if not self._dispatch.acquire(timeout=2):
            handler.reply(409, {"error": {"message": "The HyperQwen trial already has an active request"}})
            return
        try:
            if self._failure or self._stopping.is_set():
                handler.reply(400, {"error": {"message": "The HyperQwen trial cannot accept another request"}})
                return
            started = time.monotonic()
            request_id = str(uuid4())
            stage, effort, generation_policy = self._turn_profile or (None, EFFORT, None)
            result = {"request_id": request_id, "model": self.model, "context": self.context,
                      "stage": stage, "reasoning_effort": effort, "finish_reason": None, "done_event": False,
                      "seconds_to_first_output": None, "upstream_dispatched": False}
            if generation_policy is not None:
                result.update(generation_policy=deepcopy(generation_policy), generation_policy_verified=False)
            opened = False
            try:
                raw = handler.rfile.read(length)
                if len(raw) != length:
                    raise HyperQwenBridgeError("OpenCode did not send the declared HTTP body length")
                body = _body(raw, self.model, self.context, request_id, effort, generation_policy)
                with closing(self._forward(handler, body, result, started)) as events:
                    for event in events:
                        if not opened:
                            handler.start_sse()
                            opened = True
                        handler.wfile.write(event)
                self._request_count += 1
                result["status"] = "completed"
            except (OSError, HTTPException, ValueError, RecursionError) as error:
                self._failure = True
                self._unsettled = result["upstream_dispatched"] and not result["done_event"]
                result["status"] = ("generation_policy_rejected" if isinstance(error, HyperQwenGenerationPolicyError)
                                    else "transport_incomplete" if result["upstream_dispatched"] else "not_dispatched")
                result["error_type"] = type(error).__name__
                try:
                    diagnostic = str(error) if isinstance(error, HyperQwenBridgeError) else "HyperQwen transport failed"
                    payload = {"error": {"message": diagnostic, "type": "hyperqwen_trial_error"}}
                    if opened:
                        handler.wfile.write(b"data: " + encode(payload) + b"\n\n")
                    else:
                        handler.reply(502 if result["upstream_dispatched"] else 400, payload)
                except OSError:
                    pass
            finally:
                result["wall_seconds"] = round(time.monotonic() - started, 3)
                try:
                    with (self.directory / f"{request_id}.json").open("xb") as receipt:
                        receipt.write(encode(result) + b"\n")
                        receipt.flush()
                        os.fsync(receipt.fileno())
                except OSError:
                    self._failure = True
                    raise
        finally:
            self._dispatch.release()

    def _forward(self, handler, body, result, started):
        headers = {**self._upstream_headers, "X-Request-Id": result["request_id"],
                   "Content-Type": "application/json", "Accept": "text/event-stream"}
        connection = HTTPConnection("127.0.0.1", self._port, timeout=900)
        done = threading.Event()
        watcher = None
        terminal_frame = None
        try:
            connection.connect()
            watcher = threading.Thread(target=_watch_disconnect,
                                       args=(handler.connection, connection.sock, done, self._stopping), daemon=True)
            watcher.start()
            result["upstream_dispatched"] = True
            connection.request("POST", CHAT, body=encode(body), headers=headers)
            with connection.getresponse() as response:
                result["http_status"] = response.status
                result["seconds_to_headers"] = round(time.monotonic() - started, 3)
                if response.status != 200 or response.headers.get_content_type() != "text/event-stream":
                    raise HyperQwenBridgeError(f"HyperQwen did not open its SSE stream (HTTP {response.status})")
                for raw, data in _events(response):
                    if data == b"[DONE]":
                        if result["finish_reason"] is None:
                            raise HyperQwenBridgeError("HyperQwen returned [DONE] without a terminal choice")
                        result["done_event"] = True
                        if result.get("generation_policy") is not None:
                            terminal_frame = raw
                            break
                        yield raw
                        return
                    if data is not None:
                        frame = _strict_json(data.decode("utf-8"))
                        if _observe(frame, result, self.model, started):
                            raw = b"data: " + encode(frame) + b"\n\n"
                    yield raw
        finally:
            done.set()
            if watcher is not None:
                watcher.join(timeout=.2)
            connection.close()
        if terminal_frame is not None:
            # Withhold downstream success until the upstream has ended and its
            # connection is closed. A budget rejection is settled, not unknown.
            _generation_usage(result, result["generation_policy"])
            yield terminal_frame
