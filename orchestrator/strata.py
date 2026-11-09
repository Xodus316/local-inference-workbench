"""Strict local Strata transport with immutable launch identity checks.

The adapter intentionally uses the OpenAI-compatible streaming endpoint without
server-side response-format validation.  That preserves the model's native final
text in the durable receipt even when local schema validation later rejects it.
"""

from __future__ import annotations

import hashlib
import base64
import http.client
import json
import math
import os
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterator
from typing import Any

from .config import StrataBackend
from .ollama import OllamaCancelled, OllamaError, _strict_json, deadline_error


_MAX_RESPONSE = 4_000_000
# Token-framed SSE has substantially more overhead than decoded model text.
_MAX_STREAM_RESPONSE = 32_000_000
_MAX_STREAM_LINE = 1_048_576
_TOKEN_COUNT_POLICY = "strata-openai-native-token-count-v1"
_CLEANUP_POLICY = "strata-serial-stop-done-v1"
_CLEANUP_SECONDS = 30.0
_OWNER_FIELDS = frozenset({"server_instance_id", "engine_pid", "engine_generation"})


class StrataError(OllamaError):
    """A bounded Strata transport or protocol failure."""

    raw_response: dict[str, Any] | None = None


class StrataCancelled(OllamaCancelled, StrataError):
    """The caller stopped an in-flight Strata request."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request_sha256(body: dict[str, Any]) -> str:
    """Bind a receipt to the ordinary request, independent of wire spacing."""
    return _sha256_text(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False, allow_nan=False))


def _canonical_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _qualified_owner(value: Any) -> bool:
    return (isinstance(value, dict) and value.keys() == _OWNER_FIELDS
            and isinstance(value.get("server_instance_id"), str)
            and re.fullmatch(r"[0-9a-f]{32}", value["server_instance_id"]) is not None
            and type(value.get("engine_pid")) is int and value["engine_pid"] > 0
            and type(value.get("engine_generation")) is int and value["engine_generation"] > 0)


def _error_diagnostics(status: Any, body: bytes = b"") -> dict[str, Any]:
    if type(status) is not int or not 100 <= status <= 599:
        return {}
    code = "backend_http_error"
    if status == 413:
        code = "request_too_large"
    elif status == 429:
        code = "admission_full"
    elif status == 409:
        code = "service_busy"
    elif status == 503:
        code = "backend_unavailable"
    if len(body) <= 65_536:
        try:
            value = _strict_json(body.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            value = None
        error = value.get("error") if isinstance(value, dict) else None
        native = error.get("code") if isinstance(error, dict) else None
        if native in {"admission_full", "queue_wait_timeout", "server_draining"}:
            code = native
    return {"backend_http_status": status, "backend_error_code": code}


class StrataClient:
    """OpenAI-compatible client for one pre-launched, pinned Strata model."""

    def __init__(self, config: StrataBackend, timeout: float = 900) -> None:
        self.config = config
        self.base_url = config.url
        self.timeout = timeout

    def _headers(self, accept="application/json") -> dict[str, str]:
        headers = {"Accept": accept}
        key = os.environ.get(self.config.api_key_env)
        if key:
            headers["Authorization"] = "Bearer " + key
        return headers

    def _json(self, path: str, body: dict[str, Any] | None = None, *, timeout=2.0) -> Any:
        data = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={**self._headers(), **({"Content-Type": "application/json"} if data is not None else {})},
            method="GET" if body is None else "POST",
        )
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(_MAX_RESPONSE + 1)
        except Exception as error:
            raise StrataError("Strata metadata request failed", diagnostics={"native_completion": "not_started"}) from error
        if len(raw) > _MAX_RESPONSE:
            raise StrataError("Strata metadata response exceeded its bound", diagnostics={"native_completion": "not_started"})
        try:
            return _strict_json(raw.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError) as error:
            raise StrataError("Strata returned invalid metadata", diagnostics={"native_completion": "not_started"}) from error

    def identity(self) -> dict[str, Any]:
        status = self._json("/v1/status")
        props = self._json("/props")
        listing = self._json("/v1/models")
        expected = self.config.identity
        models = listing.get("data") if isinstance(listing, dict) else None
        model_ids = {row.get("id") for row in models or [] if isinstance(row, dict)}
        context = (status.get("context") or {}).get("native") if isinstance(status, dict) else None
        concurrency = status.get("concurrency") if isinstance(status, dict) else None
        template = props.get("chat_template") if isinstance(props, dict) else None
        model_path = props.get("model_path") if isinstance(props, dict) else None
        engine = status.get("engine") if isinstance(status, dict) else None
        structured = status.get("structured_output") if isinstance(status, dict) else None
        observed = {
            "service": status.get("service") if isinstance(status, dict) else None,
            "model": status.get("model") if isinstance(status, dict) else None,
            "loaded": status.get("loaded") if isinstance(status, dict) else None,
            "engine": str(engine) if engine is not None else None,
            "context": context,
            "slots": concurrency.get("serving") if isinstance(concurrency, dict) else None,
            "template_sha256": _sha256_text(template) if isinstance(template, str) else None,
            "model_path_sha256": _sha256_text(model_path) if isinstance(model_path, str) else None,
            "structured_output_method": structured.get("method") if isinstance(structured, dict) else None,
        }
        required = {
            "service": "strata",
            "model": expected.model,
            "loaded": True,
            "engine": expected.engine,
            "context": expected.context,
            "slots": expected.slots,
            "template_sha256": expected.template_sha256,
            "model_path_sha256": expected.model_path_sha256,
            "structured_output_method": "prompt_and_validate",
        }
        if expected.native_attempts_observable:
            if expected.approximation_switches.get("native_attempts_policy") != "serial-gen-v1":
                raise StrataError("Observable attempts require the reviewed serial attempt policy", diagnostics={
                    "failure_category": "identity_mismatch", "native_completion": "not_started",
                })
            if status.get("native_single_attempt") is not True:
                raise StrataError("Strata did not enable the reviewed native attempt contract", diagnostics={
                    "failure_category": "identity_mismatch", "native_completion": "not_started",
                })
            observed.update(native_single_attempt=status.get("native_single_attempt"),
                            native_attempts_policy=status.get("native_attempts_policy"))
            required.update(native_single_attempt=True, native_attempts_policy="serial-gen-v1")
        switches = expected.approximation_switches
        for name, policy in (("native_protocol_token_count", _TOKEN_COUNT_POLICY),
                             ("native_cleanup_policy", _CLEANUP_POLICY)):
            if name not in switches:
                continue
            if switches[name] != policy or not isinstance(status, dict) or status.get(name) != policy:
                raise StrataError("Strata did not enable the configured protocol policy", diagnostics={
                    "failure_category": "identity_mismatch", "native_completion": "not_started",
                })
            observed[name] = status[name]
            required[name] = policy
        if "native_cleanup_policy" in switches:
            owner = status.get("native_protocol_owner")
            if (not _qualified_owner(owner) or status.get("protocol_quarantined") is not False
                    or status.get("protocol_request_id_required") is not True):
                raise StrataError("Strata protocol owner is unavailable or quarantined", diagnostics={
                    "failure_category": "identity_mismatch", "native_completion": "not_started",
                })
            observed.update(native_protocol_owner=dict(owner), protocol_quarantined=False, protocol_request_id_required=True)
            required.update(native_protocol_owner=dict(owner), protocol_quarantined=False, protocol_request_id_required=True)
        if observed != required or expected.model not in model_ids:
            raise StrataError("Loaded Strata identity does not match the approved configuration", diagnostics={
                "failure_category": "identity_mismatch", "native_completion": "not_started"
            })
        return {
            "name": "strata",
            "endpoint": self.base_url,
            **expected.model_dump(),
            "observed": observed,
        }

    def models(self) -> list[dict[str, Any]]:
        identity = self.identity()
        return [{
            "name": identity["model"], "model": identity["model"], "local": True,
            "capabilities": ["completion", "thinking"],
            "context_length": identity["context"], "backend": "strata",
        }]

    def loaded_models(self) -> list[dict[str, Any]]:
        return self.models()

    def show(self, model: str) -> dict[str, Any]:
        identity = self.identity()
        if model != identity["model"]:
            raise StrataError("Configured model is not loaded", diagnostics={
                "failure_category": "model_unavailable", "native_completion": "not_started"
            })
        return {
            "capabilities": ["completion", "thinking"],
            "model_info": {"general.architecture": "strata", "strata.context_length": identity["context"]},
            "strata_identity": identity,
        }

    def stream_chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        options: dict[str, Any],
        think: bool | str | None = None,
        cancel: threading.Event | None = None,
        *,
        format_schema: dict[str, Any] | None = None,
        deadline: float | None = None,
        repetition_guard: bool = True,
        protocol_request_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        del repetition_guard  # The host applies its shared final-output guard.
        identity = self.identity()
        body = self._chat_body(identity, model, messages, options, think, format_schema)
        protocol = None
        if protocol_request_id is not None and not _canonical_uuid(protocol_request_id):
            raise StrataError("Protocol request ID must be a canonical UUID", diagnostics={
                "native_completion": "not_started",
            })
        if self.config.identity.approximation_switches.get("native_cleanup_policy") == _CLEANUP_POLICY:
            protocol = {"request_id": protocol_request_id or str(uuid.uuid4()), "request_sha256": _request_sha256(body),
                        "owner": identity["observed"]["native_protocol_owner"]}
        yield from self._stream(body, cancel or threading.Event(), deadline, protocol=protocol)

    @staticmethod
    def _chat_body(identity, model, messages, options, think, format_schema):
        """Build the same ordinary OpenAI request for counting and generation."""
        if model != identity["model"]:
            raise StrataError("Configured model is not loaded", diagnostics={
                "failure_category": "model_unavailable", "native_completion": "not_started"
            })
        if options.get("num_ctx") != identity["context"]:
            raise StrataError("Requested context differs from the pinned Strata launch", diagnostics={
                "failure_category": "context_limit_exceeded", "native_completion": "not_started"
            })
        if think == "xhigh":
            raise StrataError("Strata exposes high, not an unverified xhigh mapping", diagnostics={
                "failure_category": "thinking_unsupported", "native_completion": "not_started"
            })
        effort = "none" if think is False else "high" if think is True or think is None else think
        if effort not in {"none", "low", "medium", "high"}:
            raise StrataError("Unsupported Strata reasoning effort", diagnostics={
                "failure_category": "thinking_unsupported", "native_completion": "not_started"
            })
        outbound = [dict(message) for message in messages]
        if format_schema is not None:
            outbound.insert(0, {
                "role": "system",
                "content": "Return exactly one JSON object matching this JSON Schema. Do not use Markdown. "
                           + json.dumps(format_schema, ensure_ascii=False, sort_keys=True),
            })
        body: dict[str, Any] = {
            "model": model,
            "messages": outbound,
            "stream": True,
            "reasoning_effort": effort,
            "reasoning_budget_tokens": 0,
            "tools": None,
            "strata_mcp": False,
            "strata_checkpoint": False,
        }
        for source, target in (
            ("temperature", "temperature"), ("top_p", "top_p"), ("top_k", "top_k"),
            ("min_p", "min_p"), ("presence_penalty", "presence_penalty"),
            ("frequency_penalty", "frequency_penalty"), ("repetition_penalty", "repetition_penalty"),
        ):
            if options.get(source) is not None:
                body[target] = options[source]
        cap = options.get("num_predict")
        if type(cap) is int and cap > 0:
            body["max_tokens"] = cap
        try:
            _request_sha256(body)
        except (ValueError, TypeError, RecursionError) as error:
            raise StrataError("Strata request is not valid JSON", diagnostics={
                "native_completion": "not_started",
            }) from error
        return body

    def count_tokens(self, model: str, messages: list[dict[str, Any]], options: dict[str, Any],
                     think: bool | str | None = None, *, format_schema=None) -> dict[str, Any]:
        """Count the exact ordinary chat request without dispatching generation."""
        identity = self.identity()
        if self.config.identity.approximation_switches.get("native_protocol_token_count") != _TOKEN_COUNT_POLICY:
            raise StrataError("Exact Strata token counting requires an explicit protocol policy", diagnostics={
                "native_completion": "not_started",
            })
        body = self._chat_body(identity, model, messages, options, think, format_schema)
        value = self._json("/v1/chat/completions/count_tokens", body)
        if (not isinstance(value, dict) or value.get("policy") != _TOKEN_COUNT_POLICY
                or value.get("model") != model or type(value.get("input_tokens")) is not int
                or value["input_tokens"] < 0
                or value.get("request_sha256") != _request_sha256(body)
                or not isinstance(value.get("token_ids_sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", value["token_ids_sha256"]) is None
                or type(value.get("native_dispatches")) is not int or value["native_dispatches"] != 0
                or value.get("prefix_cache_written") is not False):
            raise StrataError("Strata did not return a bound, non-generating token count", diagnostics={
                "native_completion": "not_started", "failure_category": "invalid_response",
            })
        return value

    @staticmethod
    def _confirmed_protocol_receipt(value, protocol, model, *, terminal_reason=None):
        """Accept only the request owner's acknowledged DONE and released handler."""
        if not isinstance(value, dict):
            return False
        required = {"policy", "request_id", "server_instance_id", "model", "request_sha256",
                    "server_native_request_id", "native_attempts", "engine_pid", "engine_generation",
                    "generation_observed", "first_native_token_monotonic", "cancellation_requested",
                    "cancellation_requested_monotonic", "stop_sent", "stop_sent_monotonic", "native_done_ack",
                    "native_done_reason", "native_done_monotonic", "native_completion", "native_terminal_state",
                    "cleanup_confirmed", "handler_finished", "admission_released", "handler_finished_monotonic",
                    "cleanup_elapsed_seconds", "cleanup_deadline_seconds", "state", "failure_category"}
        if not required <= value.keys():
            return False
        owner = protocol["owner"]
        if not _qualified_owner(owner):
            return False
        bound = {"policy": _CLEANUP_POLICY, "request_id": protocol["request_id"],
                 "request_sha256": protocol["request_sha256"], "model": model, **owner}
        if any(value.get(key) != expected for key, expected in bound.items()):
            return False
        if (not _qualified_owner({name: value.get(name) for name in _OWNER_FIELDS})
                or type(value.get("native_attempts")) is not int
                or value["native_attempts"] != 1
                or not isinstance(value.get("server_native_request_id"), str)
                or re.fullmatch(r"[0-9a-f]{12}", value["server_native_request_id"]) is None):
            return False
        for name in ("generation_observed", "cancellation_requested", "stop_sent", "native_done_ack",
                     "cleanup_confirmed", "handler_finished", "admission_released"):
            if type(value.get(name)) is not bool:
                return False
        if (not all(value[name] for name in ("generation_observed", "native_done_ack", "cleanup_confirmed",
                                             "handler_finished", "admission_released"))
                or value.get("native_completion") != "terminal" or value.get("failure_category") is not None):
            return False

        def number(name):
            item = value.get(name)
            return type(item) in (int, float) and math.isfinite(item) and item >= 0

        if (not all(number(name) for name in ("first_native_token_monotonic", "native_done_monotonic",
                                              "handler_finished_monotonic", "cleanup_deadline_seconds"))
                or value["cleanup_deadline_seconds"] != _CLEANUP_SECONDS):
            return False
        first = value["first_native_token_monotonic"]
        done = value["native_done_monotonic"]
        finished = value["handler_finished_monotonic"]
        if not first <= done <= finished:
            return False
        if "created_monotonic" in value and (not number("created_monotonic") or value["created_monotonic"] > first):
            return False
        for flag, stamp in (("cancellation_requested", "cancellation_requested_monotonic"),
                            ("stop_sent", "stop_sent_monotonic")):
            if value[flag] and not number(stamp) or not value[flag] and value.get(stamp) is not None:
                return False
        if value["cancellation_requested"] and value["cancellation_requested_monotonic"] > finished:
            return False
        if (value["stop_sent"] and (value["stop_sent_monotonic"] > finished
                or value["cancellation_requested"] and value["cancellation_requested_monotonic"]
                > value["stop_sent_monotonic"])):
            return False
        reason = value.get("native_done_reason")
        if terminal_reason is not None and (terminal_reason not in {"stop", "length"} or reason != terminal_reason):
            return False
        if reason == "cancel":
            if (value.get("native_terminal_state") != "cancelled" or value.get("state") != "cancelled"
                    or value["cancellation_requested"] is not True or value["stop_sent"] is not True
                    or not number("cleanup_elapsed_seconds")):
                return False
            cancelled = value["cancellation_requested_monotonic"]
            stopped = value["stop_sent_monotonic"]
            elapsed = value["cleanup_elapsed_seconds"]
            return (first <= cancelled <= stopped <= done <= finished
                    and elapsed <= _CLEANUP_SECONDS and finished - cancelled <= _CLEANUP_SECONDS
                    and math.isclose(elapsed, finished - cancelled, rel_tol=1e-9, abs_tol=1e-6))
        if reason in {"stop", "length"} and (reason == "stop" or terminal_reason == "length"):
            # Native DONE can win the disconnect race. It proves a natural stop,
            # not that STOP exercised cancellation.
            if value["cancellation_requested"]:
                if (not number("cleanup_elapsed_seconds") or value["cleanup_elapsed_seconds"] > _CLEANUP_SECONDS
                        or not math.isclose(value["cleanup_elapsed_seconds"],
                                            finished - value["cancellation_requested_monotonic"],
                                            rel_tol=1e-9, abs_tol=1e-6)):
                    return False
            elif value["stop_sent"]:
                # The normal response iterator also sends STOP while draining
                # native DONE. That cleanup does not imply user cancellation.
                stopped = value["stop_sent_monotonic"]
                if (not first <= stopped <= done or not number("cleanup_elapsed_seconds")
                        or value["cleanup_elapsed_seconds"] > _CLEANUP_SECONDS
                        or finished - stopped > _CLEANUP_SECONDS
                        or not math.isclose(value["cleanup_elapsed_seconds"], finished - stopped,
                                            rel_tol=1e-9, abs_tol=1e-6)):
                    return False
            elif value["cleanup_elapsed_seconds"] is not None:
                return False
            return value.get("native_terminal_state") == reason and value.get("state") == reason
        return False

    def _wait_protocol_receipt(self, protocol, model, *, terminal_reason=None):
        until = time.monotonic() + _CLEANUP_SECONDS
        latest = None
        while time.monotonic() < until:
            try:
                status = self._json("/v1/status", timeout=min(1.0, until - time.monotonic()))
                if (not isinstance(status, dict) or status.get("native_cleanup_policy") != _CLEANUP_POLICY
                        or status.get("protocol_quarantined") is not False
                        or status.get("protocol_request_id_required") is not True
                        or not _qualified_owner(status.get("native_protocol_owner"))
                        or status.get("native_protocol_owner") != protocol["owner"]):
                    return latest, False
                if time.monotonic() >= until:
                    break
                latest = self._json("/v1/protocol/requests/" + protocol["request_id"],
                                    timeout=min(1.0, until - time.monotonic()))
                if time.monotonic() > until:
                    break
                if self._confirmed_protocol_receipt(latest, protocol, model, terminal_reason=terminal_reason):
                    return latest, True
                if (not isinstance(latest, dict)
                        or latest.get("state") not in {"registered", "dispatched", "stopping", "native_done"}
                        or latest.get("policy") != _CLEANUP_POLICY
                        or latest.get("request_id") != protocol["request_id"]
                        or latest.get("request_sha256") != protocol["request_sha256"]
                        or latest.get("model") != model
                        or any(latest.get(key) != item for key, item in protocol["owner"].items())):
                    return latest, False
            except StrataError:
                pass
            remaining = until - time.monotonic()
            if remaining > 0:
                time.sleep(min(.05, remaining))
        return latest, False

    def _stream(self, body: dict[str, Any], cancel: threading.Event, deadline: float | None, *, protocol=None):
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise StrataError("Invalid Strata request deadline", diagnostics={"native_completion": "not_started"})
        if cancel.is_set():
            raise StrataCancelled("Generation stopped", diagnostics={"native_completion": "not_started"})
        if deadline is not None and time.monotonic() >= deadline:
            raise deadline_error(dispatched=False)
        parsed = urllib.parse.urlsplit(self.base_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=1.0)
        response = None
        finished = threading.Event()
        expired = threading.Event()
        dispatched = False
        active_socket = None
        wire = bytearray()
        saved_content, saved_reasoning = [], []
        attempts = None
        stream_complete = False
        protocol_receipt = None
        protocol_confirmed = False
        verified_attempts = self.config.identity.native_attempts_observable

        def abort():
            while not finished.wait(.025):
                deadline_reached = deadline is not None and time.monotonic() >= deadline
                if not cancel.is_set() and not deadline_reached:
                    continue
                if deadline_reached:
                    expired.set()
                transport = active_socket
                if transport is not None:
                    try:
                        transport.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    try:
                        transport.close()
                    except OSError:
                        pass

        watcher = threading.Thread(target=abort, name="strata-chat-cancellation", daemon=True)
        watcher.start()
        final = None
        usage = {}
        failure = None

        def observe_attempts(value):
            nonlocal attempts
            if verified_attempts and type(value) is int and 0 <= value <= 100:
                attempts = value

        def receipt():
            value = {"model": body["model"], "choices": [{"finish_reason": final,
                    "message": {"content": "".join(saved_content), "reasoning_content": "".join(saved_reasoning)}}],
                    "usage": usage, "native": {"backend": "strata", "attempts": attempts,
                    "terminal_state": final or "unknown"},
                    "native_transport": {"encoding": "base64", "bytes": base64.b64encode(wire).decode("ascii"),
                    "complete": stream_complete}}
            if protocol_receipt is not None:
                value["native_protocol"] = protocol_receipt
            if protocol is not None:
                value["native_protocol_request"] = {"policy": _CLEANUP_POLICY, "request_id": protocol["request_id"],
                                                    "request_sha256": protocol["request_sha256"],
                                                    "model": body["model"], **protocol["owner"]}
            if protocol_confirmed:
                value["native"].update(attempts=1, terminal_state=protocol_receipt["native_terminal_state"],
                                       cleanup_confirmed=True, native_done_reason=protocol_receipt["native_done_reason"],
                                       cancellation_exercised=protocol_receipt["native_done_reason"] == "cancel",
                                       cancellation_requested=protocol_receipt["cancellation_requested"],
                                       handler_finished=True, admission_released=True)
            return value
        try:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            connection.connect()
            active_socket = connection.sock
            if cancel.is_set():
                raise StrataCancelled("Generation stopped")
            if deadline is not None and time.monotonic() >= deadline:
                expired.set()
                raise deadline_error(dispatched=False)
            if connection.sock is not None:
                connection.sock.settimeout(self.timeout if deadline is None else max(.001, deadline - time.monotonic()))
            headers = {"Content-Type": "application/json", **self._headers("text/event-stream")}
            if protocol is not None:
                headers["X-Strata-Protocol-Request-ID"] = protocol["request_id"]
            # Once bytes may be written, lack of response is uncertain dispatch.
            dispatched = True
            connection.request("POST", "/v1/chat/completions", body=data, headers=headers)
            response = connection.getresponse()
            if response.status != 200:
                raw = response.read(65_537)
                wire.extend(raw)
                try:
                    error_value = _strict_json(raw.decode("utf-8"))
                    if isinstance(error_value, dict):
                        observe_attempts(error_value.get("strata_native_attempts"))
                except (ValueError, UnicodeError, RecursionError):
                    pass
                raise StrataError("Strata request failed", diagnostics={
                    **_error_diagnostics(response.status, raw), "native_completion": "unknown"
                })
            while True:
                line = response.readline(_MAX_STREAM_LINE + 1)
                if len(wire) + len(line) > _MAX_STREAM_RESPONSE:
                    wire.extend(line[:_MAX_STREAM_RESPONSE - len(wire)])
                    raise StrataError("Strata transport exceeded its response bound")
                wire.extend(line)
                if cancel.is_set():
                    raise StrataCancelled("Generation stopped")
                if len(line) > _MAX_STREAM_LINE:
                    raise StrataError("Strata returned an oversized stream frame")
                if not line:
                    raise StrataError("Strata stream ended before a terminal frame", diagnostics={
                        "failure_category": "invalid_response", "native_completion": "unknown"
                    })
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload == b"[DONE]":
                    if final is None:
                        raise StrataError("Strata stream ended without a terminal choice")
                    stream_complete = True
                    break
                try:
                    frame = _strict_json(payload.decode("utf-8"))
                except (ValueError, UnicodeError, RecursionError) as error:
                    raise StrataError("Strata returned invalid streaming JSON") from error
                if isinstance(frame, dict) and frame.get("error"):
                    observe_attempts(frame.get("strata_native_attempts"))
                    raise StrataError("Strata reported a streaming generation error", diagnostics={
                        "failure_category": "backend_error", "native_completion": "unknown"
                    })
                choices = frame.get("choices") if isinstance(frame, dict) else None
                if isinstance(frame, dict) and isinstance(frame.get("usage"), dict):
                    usage = frame["usage"]
                if choices == [] and isinstance(frame.get("usage"), dict) and final is not None:
                    continue
                choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else None
                delta = choice.get("delta") if isinstance(choice, dict) else None
                if not isinstance(delta, dict):
                    raise StrataError("Strata returned an invalid streaming choice")
                part = delta.get("content") or ""
                thought = delta.get("reasoning_content") or ""
                if not isinstance(part, str) or not isinstance(thought, str):
                    raise StrataError("Strata returned non-text message content")
                saved_content.append(part)
                saved_reasoning.append(thought)
                if final is not None and (part or thought):
                    raise StrataError("Strata returned content after its terminal choice")
                finish = choice.get("finish_reason")
                if finish is not None:
                    if finish not in {"stop", "length", "cancel", "tool_calls"} or final is not None:
                        raise StrataError("Strata returned an unsupported finish reason")
                    final = finish
                    if verified_attempts:
                        observe_attempts(frame.get("strata_native_attempts"))
                        if type(frame.get("strata_native_attempts")) is not int or attempts != 1:
                            raise StrataError("Strata did not confirm exactly one native attempt", diagnostics={
                                "failure_category": "native_attempts_unverified", "native_completion": "unknown",
                            })
                if part or thought:
                    yield {"message": {"content": part, "thinking": thought}, "done": False}
        except (StrataCancelled, StrataError) as error:
            failure = error
        except (OSError, http.client.HTTPException, ValueError) as error:
            if expired.is_set() or deadline is not None and time.monotonic() >= deadline:
                failure = StrataError("Strata request deadline exceeded", diagnostics={"failure_category": "backend_timeout"})
            elif cancel.is_set():
                failure = StrataCancelled("Generation stopped")
            else:
                failure = StrataError("Strata streaming request failed", diagnostics={"failure_category": "backend_error"})
        finally:
            if response is not None:
                response.close()
            connection.close()
            finished.set()
            watcher.join(timeout=.2)
        if protocol is not None and dispatched:
            # Closing a failed stream can cancel the native request. Retain the
            # failure while independently checking its bound cleanup receipt.
            protocol_receipt, protocol_confirmed = self._wait_protocol_receipt(
                protocol, body["model"], terminal_reason=final if failure is None else None)
            if protocol_confirmed:
                attempts = 1
                if failure is not None:
                    final = protocol_receipt["native_done_reason"]
            elif failure is None:
                failure = StrataError("Strata did not confirm request-bound native completion", diagnostics={
                    "failure_category": "native_completion_unverified",
                })
        if failure is not None:
            failure.diagnostics = {**failure.diagnostics,
                                   "native_completion": "terminal" if protocol_confirmed else "unknown" if dispatched else "not_started",
                                   "automatic_retry": False}
            if protocol is not None:
                failure.diagnostics["cleanup_confirmed"] = protocol_confirmed
            if protocol_confirmed:
                failure.diagnostics.update(native_done_reason=protocol_receipt["native_done_reason"],
                                           cancellation_exercised=protocol_receipt["native_done_reason"] == "cancel")
            if attempts is not None:
                failure.diagnostics["native_attempts"] = attempts
            failure.raw_response = receipt() if dispatched else None
            raise failure
        yield {"message": {"content": "", "thinking": ""}, "done": True,
               "done_reason": final, "prompt_eval_count": usage.get("prompt_tokens"),
               "eval_count": usage.get("completion_tokens"), "strata_native_attempts": attempts,
               "strata_native_transport": receipt()["native_transport"],
               **({"strata_native_protocol": protocol_receipt,
                   "strata_native": receipt()["native"],
                   "strata_native_protocol_request": receipt()["native_protocol_request"]} if protocol is not None else {})}
