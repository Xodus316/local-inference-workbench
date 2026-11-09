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
import socket
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

from .config import StrataBackend
from .ollama import OllamaCancelled, OllamaError, _strict_json, deadline_error


_MAX_RESPONSE = 4_000_000
_MAX_STREAM_LINE = 1_048_576


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

    def _json(self, path: str, *, timeout=2.0) -> Any:
        request = urllib.request.Request(
            self.base_url + path,
            headers=self._headers(),
            method="GET",
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
    ) -> Iterator[dict[str, Any]]:
        del repetition_guard  # The host applies its shared final-output guard.
        identity = self.identity()
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
        yield from self._stream(body, cancel or threading.Event(), deadline)

    def _stream(self, body: dict[str, Any], cancel: threading.Event, deadline: float | None):
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
            return {"model": body["model"], "choices": [{"finish_reason": final,
                    "message": {"content": "".join(saved_content), "reasoning_content": "".join(saved_reasoning)}}],
                    "usage": usage, "native": {"backend": "strata", "attempts": attempts,
                    "terminal_state": final or "unknown"},
                    "native_transport": {"encoding": "base64", "bytes": base64.b64encode(wire).decode("ascii"),
                    "complete": stream_complete}}
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
                if len(wire) + len(line) > _MAX_RESPONSE:
                    wire.extend(line[:_MAX_RESPONSE - len(wire)])
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
        if failure is not None:
            failure.diagnostics = {**failure.diagnostics,
                                   "native_completion": "unknown" if dispatched else "not_started",
                                   "automatic_retry": False}
            if attempts is not None:
                failure.diagnostics["native_attempts"] = attempts
            failure.raw_response = receipt() if dispatched else None
            raise failure
        yield {"message": {"content": "", "thinking": ""}, "done": True,
               "done_reason": final, "prompt_eval_count": usage.get("prompt_tokens"),
               "eval_count": usage.get("completion_tokens"), "strata_native_attempts": attempts,
               "strata_native_transport": receipt()["native_transport"]}
