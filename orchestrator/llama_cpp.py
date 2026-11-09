"""Single-attempt llama.cpp transport for a pre-launched local model.

The native API verifies only observable server properties. Binary/model digests
and launch-only options are operator evidence, frozen into approvals but never
misrepresented as values attested by HTTP metadata.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import math
import os
import re
import socket
import threading
import time
import urllib.parse
from collections.abc import Iterator
from typing import Any

from .config import LlamaCppBackend
from .ollama import OllamaCancelled, OllamaError, _strict_json
from .repetition import ThinkingRepetitionGuard


MAX_TRANSPORT_BYTES = 4_000_000
IDENTITY_MESSAGES = [{"role": "user", "content": "Workbench identity probe."}]


class LlamaCppError(OllamaError):
    """Transport failure with an optional private, nonterminal raw receipt."""

    raw_response: dict[str, Any] | None = None


class LlamaCppCancelled(OllamaCancelled, LlamaCppError):
    pass


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class LlamaCppClient:
    def __init__(self, config: LlamaCppBackend, timeout: float = 900):
        self.config = config
        self.base_url = config.url
        self.timeout = timeout

    def _headers(self, accept="application/json"):
        result = {"Accept": accept, "Content-Type": "application/json"}
        key = os.environ.get(self.config.api_key_env)
        if key:
            result["Authorization"] = "Bearer " + key
        return result

    def _connection(self, timeout):
        parsed = urllib.parse.urlsplit(self.base_url)
        return http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)

    def _json(self, path, body=None, *, timeout=2.0):
        connection = self._connection(timeout)
        try:
            data = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            connection.request("GET" if body is None else "POST", path, data, self._headers())
            response = connection.getresponse()
            raw = response.read(MAX_TRANSPORT_BYTES + 1)
            if response.status != 200 or len(raw) > MAX_TRANSPORT_BYTES:
                raise ValueError("Unexpected metadata status or size")
            return _strict_json(raw.decode("utf-8"))
        except (OSError, http.client.HTTPException, ValueError, UnicodeError, RecursionError) as error:
            raise LlamaCppError("llama.cpp metadata unavailable or invalid", diagnostics={
                "native_completion": "not_started", "failure_category": "identity_mismatch",
            }) from error
        finally:
            connection.close()

    def _render(self, messages, schema=None):
        body = {"messages": messages, "add_generation_prompt": True}
        if schema is not None:
            body["response_format"] = self._schema(schema)
        value = self._json("/apply-template", body)
        if not isinstance(value, dict) or not isinstance(value.get("prompt"), str):
            raise LlamaCppError("llama.cpp did not return a rendered prompt", diagnostics={
                "native_completion": "not_started",
            })
        return value["prompt"]

    @staticmethod
    def _schema(schema):
        return {"type": "json_schema", "json_schema": {"name": "workbench_result", "strict": True, "schema": schema}}

    def observe_identity(self):
        """Read raw non-generating metadata; this is not identity admission.

        A host may use the observation while exclusively owning a verified
        startup to freeze its first approval. Normal generation still calls
        identity(), which requires every configured fingerprint to match.
        """
        props = self._json("/props")
        models = self._json("/v1/models")
        if not isinstance(props, dict) or not isinstance(models, dict):
            raise LlamaCppError("llama.cpp identity metadata is invalid", diagnostics={"native_completion": "not_started"})
        defaults = props.get("default_generation_settings") or {}
        params = defaults.get("params") or {} if isinstance(defaults, dict) else {}
        rendered = self._render(IDENTITY_MESSAGES)
        observed = {
            "model": props.get("model_alias"),
            "build_info": props.get("build_info"),
            "context": defaults.get("n_ctx") if isinstance(defaults, dict) else None,
            "slots": props.get("total_slots"),
            "template_sha256": sha256_text(props["chat_template"]) if isinstance(props.get("chat_template"), str) else None,
            "model_path_sha256": sha256_text(props["model_path"]) if isinstance(props.get("model_path"), str) else None,
            "rendered_template_sha256": sha256_text(rendered),
            "is_sleeping": props.get("is_sleeping"),
            "slots_endpoint": props.get("endpoint_slots"),
            "default_output_tokens": params.get("n_predict") if isinstance(params, dict) else None,
        }
        rows = models.get("data")
        names = [row.get("id") for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        return {"observed": observed, "model_ids": names, "rendered_identity_prompt": rendered,
                "chat_template": props.get("chat_template"), "model_path": props.get("model_path")}

    def identity(self):
        expected = self.config.identity
        observation = self.observe_identity()
        observed = observation["observed"]
        required = {key: getattr(expected, key) for key in (
            "model", "build_info", "context", "slots", "template_sha256", "model_path_sha256", "rendered_template_sha256",
        )}
        required.update(is_sleeping=False, slots_endpoint=True, default_output_tokens=-1)
        names = observation["model_ids"]
        build_hashes = re.findall(r"(?<![a-f0-9])[a-f0-9]{7,40}(?![a-f0-9])", expected.build_info)
        if (observed != required or type(observed["context"]) is not int or type(observed["slots"]) is not int
                or type(observed["default_output_tokens"]) is not int or expected.model not in names
                or not any(expected.source_commit.startswith(h) for h in build_hashes)):
            raise LlamaCppError("Loaded llama.cpp identity differs from the approved configuration", diagnostics={
                "native_completion": "not_started", "failure_category": "identity_mismatch",
            })
        # /props constructs fresh request defaults: n_predict here does not
        # attest the global launch cap, nor are executable/GGUF hashes exposed.
        return {"name": "llama-cpp", "endpoint": self.base_url, **expected.model_dump(), "observed": observed,
                "verification": {"http": sorted(observed), "operator_verified": [
                    "engine", "source_commit", "runtime_sha256", "package_sha256", "model_manifest_sha256",
                    "config_sha256", "launch_attestation_sha256", "cache_type_k", "cache_type_v",
                    "context_shift", "speculative_mode", "memory_mode", "server_output_tokens",
                    "native_attempts_observable", "approximation_switches",
                ]}}

    def models(self):
        identity = self.identity()
        return [{"name": identity["model"], "model": identity["model"], "local": True,
                 "capabilities": ["completion"], "context_length": identity["context"], "backend": "llama-cpp"}]

    def loaded_models(self):
        return self.models()

    def show(self, model):
        identity = self.identity()
        if model != identity["model"]:
            raise LlamaCppError("Requested llama.cpp model is not loaded", diagnostics={"native_completion": "not_started"})
        return {"capabilities": ["completion"], "model_info": {"llama_cpp.context_length": identity["context"]},
                "llama_cpp_identity": identity}

    def _preflight(self, model, messages, options, think, schema, cancel, deadline):
        self._check_deadline(cancel, deadline, dispatched=False)
        identity = self.identity()
        if model != identity["model"] or options.get("num_ctx") != identity["context"]:
            raise LlamaCppError("Requested model/context differs from the pinned launch", diagnostics={"native_completion": "not_started"})
        if think is not None and think is not False:
            raise LlamaCppError("This llama.cpp profile supports native non-thinking only", diagnostics={
                "native_completion": "not_started", "backend_error_code": "thinking_unsupported"})
        rendered = self._render(messages, schema)
        tokens = self._json("/tokenize", {"content": rendered, "add_special": True, "parse_special": True})
        token_ids = tokens.get("tokens") if isinstance(tokens, dict) else None
        if not isinstance(token_ids, list) or not all(type(token) is int and token >= 0 for token in token_ids):
            raise LlamaCppError("llama.cpp tokenization failed", diagnostics={"native_completion": "not_started"})
        cap = options.get("num_predict", -1)
        if type(cap) is not int or cap == 0 or cap < -1:
            raise LlamaCppError("Invalid output token limit", diagnostics={"native_completion": "not_started"})
        if len(token_ids) + (cap if cap > 0 else 1) >= identity["context"]:
            raise LlamaCppError("Rendered prompt/output allowance exceeds the pinned context", diagnostics={
                "native_completion": "not_started", "backend_error_code": "context_limit_exceeded"})
        if not self.cleanup_confirmed():
            raise LlamaCppError("llama.cpp is still processing a native request", diagnostics={
                "native_completion": "not_started", "backend_error_code": "service_busy"})
        self._check_deadline(cancel, deadline, dispatched=False)
        return identity

    @staticmethod
    def _check_deadline(cancel, deadline, *, dispatched):
        diagnostics = {"native_completion": "unknown" if dispatched else "not_started", "automatic_retry": False}
        if cancel.is_set():
            raise LlamaCppCancelled("Generation cancellation requested", diagnostics=diagnostics)
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline) or time.monotonic() >= deadline):
            raise LlamaCppError("llama.cpp request deadline exceeded", diagnostics=diagnostics)

    def cleanup_confirmed(self):
        slots = self._json("/slots", timeout=min(1.0, self.config.cleanup_timeout_seconds))
        return (isinstance(slots, list) and len(slots) == self.config.identity.slots
                and all(isinstance(row, dict) and type(row.get("id")) is int
                        and row.get("n_ctx") == self.config.identity.context
                        and row.get("is_processing") is False for row in slots))

    def confirm_idle(self):
        """Require observed native slot cleanup before a host unload/transition."""
        if not self.cleanup_confirmed():
            raise LlamaCppError("llama.cpp native slot is still processing", diagnostics={
                "native_completion": "unknown", "cleanup_confirmed": False,
            })
        return True

    def _wait_cleanup(self):
        until = time.monotonic() + self.config.cleanup_timeout_seconds
        while True:
            try:
                if self.cleanup_confirmed():
                    return True
            except LlamaCppError:
                pass
            if time.monotonic() >= until:
                return False
            time.sleep(min(.05, max(0, until - time.monotonic())))

    def stream_chat(self, model, messages, options, think=None, cancel=None, *,
                    format_schema=None, deadline=None, repetition_guard=True,
                    cancel_cleanup_terminal=False) -> Iterator[dict[str, Any]]:
        cancel = cancel or threading.Event()
        identity = self._preflight(model, messages, options, think, format_schema, cancel, deadline)
        body = {"model": model, "messages": messages, "stream": True,
                "stream_options": {"include_usage": True}, "max_tokens": options.get("num_predict", -1),
                "n": 1, "id_slot": 0, "cache_prompt": True}
        if format_schema is not None:
            body["response_format"] = self._schema(format_schema)
        for source, target in (("temperature", "temperature"), ("top_p", "top_p"), ("top_k", "top_k"),
                               ("min_p", "min_p"), ("presence_penalty", "presence_penalty"),
                               ("frequency_penalty", "frequency_penalty"), ("repetition_penalty", "repeat_penalty")):
            if options.get(source) is not None:
                body[target] = options[source]
        yield from self._stream(body, identity, cancel, deadline,
                                ThinkingRepetitionGuard() if repetition_guard else None,
                                cancel_cleanup_terminal=cancel_cleanup_terminal)

    def _stream(self, body, identity, cancel, deadline, guard=None, *, cancel_cleanup_terminal=False):
        self._check_deadline(cancel, deadline, dispatched=False)
        connection = self._connection(1.0)
        response = None
        finished = threading.Event()
        wire = bytearray()
        content, reasoning = [], []
        final, usage, dispatched = None, {}, False
        stream_complete = False
        active_socket = None
        cleanup = False

        def abort():
            while not finished.wait(.025):
                if not cancel.is_set() and not (deadline is not None and time.monotonic() >= deadline):
                    continue
                sock = active_socket
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    sock.close()

        def receipt():
            return {"model": body["model"], "choices": [{"finish_reason": final,
                    "message": {"content": "".join(content), "reasoning_content": "".join(reasoning)}}],
                    "usage": usage, "native": {"backend": "llama-cpp", "terminal_state": final or "unknown",
                    "cleanup_confirmed": cleanup, "cancellation_requested": cancel.is_set(),
                    "attempts": 1 if identity["native_attempts_observable"] and dispatched else None,
                    **({"repetition": guard.summary()} if guard is not None else {})},
                    "native_transport": {"encoding": "base64", "bytes": base64.b64encode(wire).decode("ascii"),
                    "complete": stream_complete}}

        watcher = threading.Thread(target=abort, name="llama-cpp-cancellation", daemon=True)
        watcher.start()
        failure = None
        try:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            connection.connect()
            active_socket = connection.sock
            self._check_deadline(cancel, deadline, dispatched=False)
            if connection.sock is not None:
                connection.sock.settimeout(self.timeout if deadline is None else max(.001, deadline - time.monotonic()))
            # request() can partially write before failing; native dispatch is
            # uncertain from this point, even before response bytes arrive.
            dispatched = True
            connection.request("POST", "/v1/chat/completions", data, self._headers("text/event-stream"))
            response = connection.getresponse()
            if response.status != 200:
                wire.extend(response.read(min(MAX_TRANSPORT_BYTES, 65_536)))
                raise LlamaCppError("llama.cpp generation returned an HTTP error", diagnostics={
                    "native_completion": "unknown", "backend_http_status": response.status,
                    "backend_error_code": "backend_http_error", "automatic_retry": False})
            while True:
                self._check_deadline(cancel, deadline, dispatched=True)
                line = response.readline(min(1_048_577, MAX_TRANSPORT_BYTES - len(wire) + 1))
                if len(wire) + len(line) > MAX_TRANSPORT_BYTES:
                    wire.extend(line[:MAX_TRANSPORT_BYTES - len(wire)])
                    raise LlamaCppError("llama.cpp transport exceeded its bound")
                wire.extend(line)
                if not line:
                    raise LlamaCppError("llama.cpp stream ended without DONE")
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload == b"[DONE]":
                    if final is None:
                        raise LlamaCppError("llama.cpp stream ended without a terminal choice")
                    stream_complete = True
                    break
                try:
                    frame = _strict_json(payload.decode("utf-8"))
                except (ValueError, UnicodeError, RecursionError) as error:
                    raise LlamaCppError("llama.cpp returned malformed streaming JSON") from error
                if not isinstance(frame, dict) or frame.get("error"):
                    raise LlamaCppError("llama.cpp reported invalid streaming output")
                choices = frame.get("choices")
                if isinstance(frame.get("usage"), dict):
                    usage = frame["usage"]
                # OpenAI usage-only frame follows the terminal choice.
                if choices == [] and isinstance(frame.get("usage"), dict):
                    continue
                if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                    raise LlamaCppError("llama.cpp returned an invalid streaming choice")
                choice = choices[0]
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    raise LlamaCppError("llama.cpp returned an invalid message delta")
                if delta.get("tool_calls"):
                    raise LlamaCppError("llama.cpp returned unsupported tool calls")
                part = delta.get("content") or ""
                thought = delta.get("reasoning_content") or ""
                if not isinstance(part, str) or not isinstance(thought, str):
                    raise LlamaCppError("llama.cpp returned non-text output")
                if final is not None and (part or thought):
                    raise LlamaCppError("llama.cpp emitted content after its terminal choice")
                content.append(part)
                reasoning.append(thought)
                if guard is not None and (repetition := guard.observe(thought)):
                    raise LlamaCppError("Stopped sustained repeated reasoning", diagnostics={
                        "failure_category": "repetition_detected", "native_completion": "unknown",
                        "automatic_retry": False, "repetition": repetition})
                finish = choice.get("finish_reason")
                if finish is not None:
                    if finish not in {"stop", "length"} or final is not None:
                        raise LlamaCppError("llama.cpp returned an unsupported terminal choice")
                    final = finish
                if part or thought:
                    yield {"message": {"content": part, "thinking": thought}, "done": False}
        except Exception as error:
            if cancel.is_set():
                failure = LlamaCppCancelled("Generation cancellation requested")
            elif isinstance(error, LlamaCppError):
                failure = error
            else:
                failure = LlamaCppError("llama.cpp generation transport failed")
        finally:
            if response is not None:
                response.close()
            connection.close()
            finished.set()
            watcher.join(timeout=.2)
            if dispatched:
                cleanup = self._wait_cleanup()
        if failure is None and not cleanup:
            failure = LlamaCppError("llama.cpp native cleanup is unconfirmed")
        if failure is not None:
            # An explicitly requested cancellation probe may establish termination
            # from the ordinary stream's closed socket and the assigned slot's
            # verified idle state. Require observed generation first: idle alone
            # cannot disambiguate a request that has not entered its slot yet.
            # Preserve the missing native EOS and incomplete transport separately.
            cancelled_cleanup = (cancel_cleanup_terminal is True
                                 and isinstance(failure, LlamaCppCancelled)
                                 and dispatched and cleanup and final is None
                                 and any(content + reasoning))
            if cancelled_cleanup:
                final = "cancelled"
            diagnostics = dict(getattr(failure, "diagnostics", {}))
            diagnostics.update(native_completion="terminal" if final is not None and cleanup else "unknown" if dispatched else "not_started",
                               automatic_retry=False, cleanup_confirmed=cleanup)
            if cancelled_cleanup:
                diagnostics["cancellation_proof"] = "observed-generation-stream-closed-slot-idle-v1"
            failure.diagnostics = diagnostics
            failure.raw_response = receipt() if dispatched else None
            raise failure
        yield {"message": {"content": "", "thinking": ""}, "done": True, "done_reason": final,
               "prompt_eval_count": usage.get("prompt_tokens"), "eval_count": usage.get("completion_tokens"),
               "llama_cpp_receipt": receipt()}
