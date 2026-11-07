"""Minimal Ollama HTTP client."""

from __future__ import annotations

import json
import http.client
import ipaddress
import math
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable

from .backend_diagnostics import safe_backend_diagnostics
from .validation import ValidationError
from .generation_policy import resolve_sampling
from .repetition import ThinkingRepetitionGuard


class OllamaError(RuntimeError):
    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        if diagnostics and diagnostics.get("failure_category") == "output_budget_exhausted":
            message = "Ollama exhausted its output budget before returning valid structured content"
        super().__init__(message)
        self.diagnostics = diagnostics or {}


def structured_output_diagnostics(
    response: Any, max_output_tokens: int, *, failure_category: str, context_window: int = 8192,
) -> dict[str, Any]:
    """Describe a failed generation without retaining any generated text.

    An output limit is evidence about generation, not about input truncation.
    Do not infer a truncated prompt from a token count or a configured context.
    """
    diagnostics: dict[str, Any] = {
        "failure_category": failure_category,
        "max_output_tokens": max_output_tokens,
        "context_window": context_window,
    }
    if not isinstance(response, dict):
        return diagnostics
    for source, target in (("prompt_eval_count", "prompt_tokens"), ("eval_count", "output_tokens")):
        count = response.get(source)
        if type(count) is int and 0 <= count <= 1_000_000_000:
            diagnostics[target] = count
    # Never echo an arbitrary backend string in diagnostics.
    reason = response.get("done_reason")
    if isinstance(reason, str):
        diagnostics["done_reason"] = reason if reason in {"stop", "length", "load", "unload"} else "other"
    message = response.get("message")
    message = message if isinstance(message, dict) else {}
    for field in ("content", "thinking"):
        value = message.get(field)
        diagnostics[f"{field}_present"] = isinstance(value, str) and bool(value.strip())
        if isinstance(value, str):
            diagnostics[f"{field}_length"] = len(value)
    exhausted = reason == "length" or diagnostics.get("output_tokens", 0) >= max_output_tokens
    diagnostics["output_budget_exhausted"] = exhausted
    if exhausted:
        diagnostics["structured_failure"] = failure_category
        diagnostics["failure_category"] = "output_budget_exhausted"
    return diagnostics


class OllamaCancelled(OllamaError):
    """The caller stopped an in-flight conversational request."""


_REQUEST_DEADLINE: ContextVar[float | None] = ContextVar("ollama_request_deadline", default=None)


@contextmanager
def request_deadline(deadline: float | None):
    """Scope an absolute deadline without changing shared client settings."""
    if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
        raise ValueError("Invalid request deadline")
    previous = _REQUEST_DEADLINE.get()
    effective = previous if deadline is None else deadline if previous is None else min(previous, deadline)
    token = _REQUEST_DEADLINE.set(effective)
    try:
        yield effective
    finally:
        _REQUEST_DEADLINE.reset(token)


def deadline_error(*, dispatched: bool) -> OllamaError:
    """A closed HTTP transport does not acknowledge native cancellation."""
    return OllamaError("Ollama request deadline exceeded", diagnostics={
        "failure_category": "backend_timeout",
        "native_completion": "unknown" if dispatched else "not_started",
    })


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


_MAX_STREAM_LINE = 1024 * 1024
_MAX_BACKEND_ERROR_BYTES = 64 * 1024
_SYSTEM_ONE_PROMPT_TOO_LONG = (
    "the prompt is longer than the context length currently available to the model; "
    "shorten the prompt, adjust the context length in settings, or use a model with a longer context length"
)
_MODEL_NOT_FOUND_ERROR = re.compile(r"model '.{1,1024}' not found", re.DOTALL)


def _backend_http_diagnostics(status: Any, raw: bytes = b"", *, path: str = "",
                              model: str | None = None) -> dict[str, Any]:
    """Classify a bounded native error without retaining backend-controlled text."""
    if type(status) is not int or not 100 <= status <= 599:
        return {}
    code = "backend_http_error"
    if status == 413:
        code = "request_too_large"
    message = ""
    if len(raw) <= _MAX_BACKEND_ERROR_BYTES:
        try:
            document = _strict_json(raw.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            pass
        else:
            candidate = document.get("error") if isinstance(document, dict) else None
            if isinstance(candidate, str) and len(candidate) <= _MAX_BACKEND_ERROR_BYTES:
                message = candidate
    if (status == 404 and path in {"/api/show", "/v1/systemone"}
            and _MODEL_NOT_FOUND_ERROR.fullmatch(message)):
        code = "model_unavailable"
    elif status == 400 and path == "/v1/systemone" and message == _SYSTEM_ONE_PROMPT_TOO_LONG:
        code = "context_limit_exceeded"
    elif (status == 400 and path == "/api/chat" and isinstance(model, str) and model
          and message == f'{json.dumps(model, ensure_ascii=False)} does not support thinking'):
        code = "thinking_unsupported"
    return safe_backend_diagnostics({"backend_http_status": status, "backend_error_code": code})


def _runtime_options(model: str, options: dict[str, Any]) -> dict[str, Any]:
    """Apply backend compatibility options without mutating caller state.

    Gemma 4 Ollama packages include an MTP draft model for speculative
    decoding.  On the production Windows/CUDA host that auxiliary model can
    fail during load even though the primary model fits and runs correctly.
    Disabling the draft path preserves the requested model, context, sampling,
    and thinking behavior; it only gives up the speculative speed-up.
    """
    effective = dict(options)
    if model.partition(":")[0].casefold() == "gemma4":
        effective.setdefault("draft_num_predict", 0)
    return effective


def _strict_json(raw: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"Non-finite JSON number: {value}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON object key")
            result[key] = value
        return result

    def finite(value: Any) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Non-finite JSON number")
        if isinstance(value, str):
            value.encode("utf-8")
        if isinstance(value, dict):
            for key, item in value.items():
                key.encode("utf-8")
                finite(item)
        elif isinstance(value, list):
            for item in value:
                finite(item)

    value = json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_object)
    finite(value)
    return value


def _context_window(messages: list[dict[str, Any]], max_output_tokens: int, max_context: int = 131072) -> int:
    """Choose enough context for text plus output without charging for image base64."""
    content_bytes = sum(
        len(message.get("content", "").encode("utf-8"))
        for message in messages
        if isinstance(message.get("content", ""), str)
    )
    # Size conservatively at one UTF-8 byte per token plus output/framing room.
    # This is a sizing heuristic, not the model tokenizer or proof of its exact
    # token count. Reject overflow rather than silently clamp the chosen context.
    required = content_bytes + max_output_tokens + 2048
    limit = min(262144, max_context)
    if required > limit:
        error = ValidationError("Input and output budget exceed the supported model context; shorten the input")
        error.diagnostics = {"failure_category": "context_limit_exceeded", "context_window": limit,
                             "max_output_tokens": max_output_tokens, "required_context": required,
                             "input_utf8_bytes": content_bytes, "framing_reserve": 2048,
                             "sizing_policy": "conservative_utf8_bytes_not_tokenizer"}
        raise error
    for window in (8192, 16384, 32768, 65536, 98304, 131072, 163840, 196608, 229376, 262144):
        if required <= window:
            return min(window, limit)
    raise AssertionError("Context limit checked above")


def _stream_object(raw: bytes) -> dict[str, Any]:
    invalid = False
    try:
        frame = _strict_json(raw.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        invalid = True
        frame = None
    if invalid:
        raise OllamaError("Ollama returned invalid streaming JSON")
    if not isinstance(frame, dict):
        raise OllamaError("Ollama returned a non-object streaming frame")
    if "error" in frame:
        raise OllamaError("Ollama reported a streaming generation error")
    if type(frame.get("done")) is not bool:
        raise OllamaError("Ollama returned a streaming frame without a valid done flag")
    message = frame.get("message")
    if message is not None:
        if not isinstance(message, dict) or any(
            name in message and not isinstance(message[name], str) for name in ("content", "thinking", "role")
        ):
            raise OllamaError("Ollama returned an invalid streaming message")
    return frame


class OllamaClient:
    def __init__(self, base_url: str, timeout: int = 900, *, max_response_bytes: int = 32_000_000) -> None:
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 128_000_000:
            raise ValueError("Invalid Ollama response limit")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self._context_limits: dict[str, int] = {}
        self._metadata_failed_at: dict[str, float] = {}
        self._metadata_lock = threading.Lock()

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None, *, follow_redirects: bool = False,
        timeout: float | None = None, deadline: float | None = None,
    ) -> dict[str, Any]:
        inherited = _REQUEST_DEADLINE.get()
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise OllamaError("Invalid Ollama request deadline")
        if deadline is not None and time.monotonic() >= deadline:
            raise deadline_error(dispatched=False)
        effective = inherited if deadline is None else deadline if inherited is None else min(inherited, deadline)
        if effective is not None:
            return self._deadline_request(method, path, payload, effective, timeout=timeout)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        http_failure = None
        try:
            # Local inference must never inherit environment/system proxies or
            # redirect prompts to another service, including metadata requests.
            open_request = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _NoRedirect(),
            ).open
            with open_request(request, timeout=self.timeout if timeout is None else timeout) as response:
                data = response.read(self.max_response_bytes + 1)
                if len(data) > self.max_response_bytes:
                    raise OllamaError("Ollama returned an oversized response")
        except urllib.error.HTTPError as error:
            try:
                raw = error.read(_MAX_BACKEND_ERROR_BYTES + 1)
            except (OSError, http.client.HTTPException):
                raw = b""
            finally:
                try:
                    error.close()
                except OSError:
                    pass
            # Backend bodies can contain prompt or generated text. Retain only
            # a numeric status and a fixed classification enum.
            http_failure = OllamaError(
                f"Ollama returned HTTP {error.code}",
                diagnostics=_backend_http_diagnostics(error.code, raw, path=path),
            )
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            raise OllamaError("Ollama request failed") from error
        if http_failure is not None:
            raise http_failure
        invalid = False
        try:
            return _strict_json(data.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            invalid = True
        if invalid:
            raise OllamaError("Ollama returned invalid response JSON",
                              diagnostics={"failure_category": "invalid_response"})

    def _deadline_request(self, method: str, path: str, payload: dict[str, Any] | None,
                          deadline: float, *, timeout: float | None = None) -> dict[str, Any]:
        """Bound this transport's total lifetime, including dribbling replies.

        The watchdog owns only this connection; configured/shared timeouts are
        unchanged. Socket closure cannot prove the native runner has stopped.
        """
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise OllamaError("Invalid Ollama request deadline")
        started = time.monotonic()
        cap = self.timeout if timeout is None else min(self.timeout, timeout)
        end = min(deadline, started + cap)
        if end <= started:
            raise deadline_error(dispatched=False)
        parsed = urllib.parse.urlsplit(self.base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            raise OllamaError("Invalid Ollama backend address")
        connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(parsed.hostname, parsed.port, timeout=min(1.0, end - started))
        data = json.dumps(payload, allow_nan=False).encode("utf-8") if payload is not None else None
        finished, expired = threading.Event(), threading.Event()
        active_socket: list[socket.socket | None] = [None]
        response, dispatched = None, False
        generation_request = method == "POST" and path in {"/api/chat", "/api/generate", "/v1/systemone"}

        def connect_resolved(address, timeout=None, source_address=None):
            # A connect timeout does not bound getaddrinfo. A late resolver
            # owns no HTTP client and can never dispatch a model request.
            host, port = address
            try:
                numeric = ipaddress.ip_address(host)
            except ValueError:
                answers = queue.Queue(maxsize=1)
                def resolve():
                    try:
                        answers.put((True, socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)))
                    except OSError as error:
                        answers.put((False, error))
                threading.Thread(target=resolve, name="ollama-deadline-dns", daemon=True).start()
                try:
                    success, resolved = answers.get(timeout=max(0.0, end - time.monotonic()))
                except queue.Empty:
                    raise TimeoutError("Backend resolution deadline exceeded") from None
                if not success:
                    raise resolved
            else:
                family = socket.AF_INET6 if numeric.version == 6 else socket.AF_INET
                target = (host, port, 0, 0) if numeric.version == 6 else (host, port)
                resolved = [(family, socket.SOCK_STREAM, 0, "", target)]
            last_error = None
            for family, kind, protocol, _, target in resolved:
                if expired.is_set() or time.monotonic() >= end:
                    raise TimeoutError("Backend connection deadline exceeded")
                transport = socket.socket(family, kind, protocol)
                active_socket[0] = transport
                try:
                    transport.settimeout(min(1.0, max(.001, end - time.monotonic())))
                    if source_address is not None:
                        transport.bind(source_address)
                    transport.connect(target)
                    # Retain a handle even while HTTPS wraps/transfers the
                    # original socket during its blocking handshake.
                    active_socket[0] = transport.dup()
                    return transport
                except OSError as error:
                    transport.close()
                    active_socket[0] = None
                    last_error = error
            if last_error is not None:
                raise last_error
            raise OSError("Backend resolution returned no address")

        connection._create_connection = connect_resolved

        def expire_transport():
            if finished.wait(max(0.0, end - time.monotonic())):
                return
            expired.set()
            while not finished.is_set():
                transport = active_socket[0] or connection.sock
                if transport is not None:
                    try:
                        transport.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    try:
                        transport.close()
                    except OSError:
                        pass
                finished.wait(.01)

        watcher = threading.Thread(target=expire_transport, name="ollama-request-deadline", daemon=True)
        watcher.start()
        try:
            connection.connect()
            if expired.is_set() or time.monotonic() >= end:
                raise deadline_error(dispatched=False)
            if connection.sock is not None:
                connection.sock.settimeout(max(.001, end - time.monotonic()))
            dispatched = True
            connection.request(method, f"{parsed.path.rstrip('/')}{path}", body=data,
                               headers={"Content-Type": "application/json", "Accept": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                try:
                    raw = response.read(_MAX_BACKEND_ERROR_BYTES + 1)
                except (OSError, http.client.HTTPException) as error:
                    if expired.is_set() or time.monotonic() >= end:
                        raise deadline_error(dispatched=dispatched and generation_request) from None
                    raw = b""
                if expired.is_set() or time.monotonic() >= end:
                    raise deadline_error(dispatched=dispatched and generation_request)
                raise OllamaError(
                    f"Ollama returned HTTP {response.status}",
                    diagnostics=_backend_http_diagnostics(response.status, raw, path=path),
                )
            raw = response.read(self.max_response_bytes + 1)
            if expired.is_set() or time.monotonic() >= end:
                raise deadline_error(dispatched=dispatched and generation_request)
            if len(raw) > self.max_response_bytes:
                raise OllamaError("Ollama returned an oversized response")
            invalid = False
            try:
                result = _strict_json(raw.decode("utf-8"))
            except (ValueError, UnicodeError, RecursionError):
                invalid = True
                result = None
            if invalid:
                raise OllamaError("Ollama returned invalid response JSON",
                                  diagnostics={"failure_category": "invalid_response"})
            if expired.is_set() or time.monotonic() >= end:
                raise deadline_error(dispatched=dispatched and generation_request)
            return result
        except (OSError, http.client.HTTPException) as error:
            if expired.is_set() or time.monotonic() >= end or dispatched and isinstance(error, TimeoutError):
                raise deadline_error(dispatched=dispatched and generation_request) from error
            raise OllamaError("Ollama request failed") from error
        finally:
            try:
                try:
                    if response is not None:
                        response.close()
                finally:
                    connection.close()
            finally:
                finished.set()
                watcher.join(timeout=.2)
                if active_socket[0] is not None:
                    active_socket[0].close()

    def health(self, *, timeout: float | None = None) -> dict[str, Any]:
        return self._request("GET", "/api/version", timeout=timeout)

    def models(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/tags", timeout=2.0).get("models", [])

    def loaded_models(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/ps", timeout=2.0).get("models", [])

    def show(self, model: str) -> dict[str, Any]:
        metadata = self._request("POST", "/api/show", {"model": model}, follow_redirects=False, timeout=2.0)
        info = metadata.get("model_info") if isinstance(metadata, dict) else None
        if isinstance(info, dict):
            limits = [value for key, value in info.items() if isinstance(key, str)
                      and key.endswith(".context_length") and type(value) is int and value > 0]
            if limits:
                architecture = info.get("general.architecture")
                preferred = info.get(f"{architecture}.context_length") if isinstance(architecture, str) else None
                with self._metadata_lock:
                    self._context_limits[model] = preferred if type(preferred) is int and preferred > 0 else min(limits)
        return metadata

    def context_window(self, model: str, messages: list[dict[str, Any]], budget: int) -> int:
        # Typed fake transports may omit the base initializer. Metadata is
        # fetched lazily only for the model actually about to generate.
        if not hasattr(self, "_metadata_lock"):
            self._metadata_lock = threading.Lock()
            self._context_limits = {}
            self._metadata_failed_at = {}
        with self._metadata_lock:
            limit = self._context_limits.get(model)
            failed_at = self._metadata_failed_at.get(model, -10.0)
        if limit is None and time.monotonic() - failed_at >= 5.0:
            try:
                self.show(model)
            except OllamaError:
                pass
            with self._metadata_lock:
                limit = self._context_limits.get(model)
                if limit is None:
                    self._metadata_failed_at[model] = time.monotonic()
        limit = limit if limit is not None else 8192
        try:
            window = _context_window(messages, budget, limit)
            return window
        except ValidationError as error:
            error.diagnostics = {"failure_category": "context_limit_exceeded", "model": model,
                                 "context_window": min(131072, limit), "max_output_tokens": budget}
            raise

    def system_one(
        self,
        model: str,
        state: Any,
        questions: dict[str, Any],
        *,
        images: list[str] | None = None,
        deadline: float | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Run Ollama's bounded System One decision endpoint.

        The service currently uses only categorical questions. Validate both
        the request and response here so a model cannot introduce an unknown
        choice or malformed probability data into a public API result.
        """
        if not isinstance(model, str) or not model.strip():
            raise OllamaError("Invalid System One model")
        if not isinstance(questions, dict) or not 1 <= len(questions) <= 64:
            raise OllamaError("Invalid System One questions")
        expected: dict[str, set[str]] = {}
        for question_id, question in questions.items():
            if not isinstance(question_id, str) or not question_id or len(question_id) > 100:
                raise OllamaError("Invalid System One question identifier")
            if not isinstance(question, dict) or question.get("type") != "choice":
                raise OllamaError("System One questions must use choice type")
            instructions = question.get("instructions")
            criteria = question.get("criteria")
            if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 4_000:
                raise OllamaError("Invalid System One instructions")
            if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 26:
                raise OllamaError("Invalid System One choice criteria")
            if any(not isinstance(key, str) or not key or len(key) > 100
                   or not isinstance(description, str) or not description.strip()
                   or len(description) > 4_000 for key, description in criteria.items()):
                raise OllamaError("Invalid System One choice criteria")
            expected[question_id] = set(criteria)
        if images is not None and (not isinstance(images, list) or not 1 <= len(images) <= 8
                                   or any(not isinstance(image, str) or not image for image in images)):
            raise OllamaError("Invalid System One images")
        payload: dict[str, Any] = {
            "model": model,
            "state": state,
            "questions": questions,
            "keep_alive": "10m",
        }
        if images:
            payload["images"] = images
        response = self._request(
            "POST", "/v1/systemone", payload,
            **({"deadline": deadline} if deadline is not None else {}),
        )
        answers = response.get("answers") if isinstance(response, dict) else None
        if not isinstance(answers, dict) or set(answers) != set(expected):
            raise OllamaError("Ollama returned invalid System One answers",
                              diagnostics={"failure_category": "invalid_response"})
        for question_id, answer in answers.items():
            choices = expected[question_id]
            probabilities = answer.get("probabilities") if isinstance(answer, dict) else None
            confidence = answer.get("confidence") if isinstance(answer, dict) else None
            if (not isinstance(answer, dict) or answer.get("type") != "choice"
                    or answer.get("choice") not in choices
                    or not isinstance(probabilities, dict) or set(probabilities) != choices
                    or any(type(value) not in (int, float) or not math.isfinite(value)
                           or not 0 <= value <= 1 for value in probabilities.values())
                    or type(confidence) not in (int, float) or not math.isfinite(confidence)
                    or not 0 <= confidence <= 1):
                raise OllamaError("Ollama returned invalid System One answer data",
                                  diagnostics={"failure_category": "invalid_response"})
        return answers, response

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
        """Yield validated Ollama frames, retaining the socket until fully closed.

        This transport never follows redirects. A cancellation watcher shuts down
        the physical socket, including while waiting for response headers or a
        streaming line, so the caller can safely release its inference lease only
        after the iterator exits (or is closed).
        """
        guard = ThinkingRepetitionGuard() if repetition_guard else None
        cancel = cancel if cancel is not None else threading.Event()
        if cancel.is_set():
            raise OllamaCancelled("Generation stopped")
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise OllamaError("Invalid Ollama request deadline")
        if deadline is not None and time.monotonic() >= deadline:
            raise deadline_error(dispatched=False)
        parsed = urllib.parse.urlsplit(self.base_url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise OllamaError("Ollama URL must be an HTTP address without credentials, query, or fragment")
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "options": _runtime_options(model, resolve_sampling(model, think, options)),
            "stream": True,
            "keep_alive": "10m",
        }
        if think is not None:
            body["think"] = think
        if format_schema is not None:
            body["format"] = format_schema
        try:
            data = json.dumps(body, allow_nan=False).encode("utf-8")
            port = parsed.port
        except (TypeError, ValueError) as error:
            raise OllamaError("Invalid Ollama chat request") from error

        connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        # Ollama is PC-local. Bound the connect phase separately; the longer
        # inference timeout applies once the socket has connected.
        connection = connection_type(parsed.hostname, port, timeout=min(float(self.timeout), 1.0))
        response: http.client.HTTPResponse | None = None
        active_socket: list[socket.socket | None] = [None]
        finished = threading.Event()
        expired = threading.Event()
        dispatched = False

        def abort_when_stopped() -> None:
            while not finished.wait(0.025):
                deadline_reached = deadline is not None and time.monotonic() >= deadline
                if not cancel.is_set() and not deadline_reached:
                    continue
                if deadline_reached:
                    expired.set()
                transport = active_socket[0] or connection.sock
                if transport is not None:
                    try:
                        transport.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    try:
                        transport.close()
                    except OSError:
                        pass

        watcher = threading.Thread(target=abort_when_stopped, name="ollama-chat-cancellation", daemon=True)
        watcher.start()
        try:
            if cancel.is_set():
                raise OllamaCancelled("Generation stopped")
            connection.connect()
            active_socket[0] = connection.sock
            if deadline is not None and time.monotonic() >= deadline:
                expired.set()
                raise deadline_error(dispatched=False)
            if cancel.is_set():
                raise OllamaCancelled("Generation stopped")
            if connection.sock is not None:
                remaining = self.timeout if deadline is None else max(0.001, deadline - time.monotonic())
                connection.sock.settimeout(min(self.timeout, remaining))
            connection.request(
                "POST",
                f"{parsed.path.rstrip('/')}/api/chat",
                body=data,
                headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
            )
            dispatched = True
            response = connection.getresponse()
            if cancel.is_set():
                raise OllamaCancelled("Generation stopped")
            if response.status != 200:
                # Retain bounded classifications, never backend-controlled text.
                # A rejection is useful evidence but does not itself relax the
                # orchestrator's unknown-completion/no-replay policy.
                diagnostics = _backend_http_diagnostics(response.status, path="/api/chat")
                try:
                    raw_error = response.read(_MAX_BACKEND_ERROR_BYTES + 1)
                    diagnostics = _backend_http_diagnostics(
                        response.status, raw_error, path="/api/chat", model=model)
                except (OSError, http.client.HTTPException):
                    pass
                raise OllamaError(f"Ollama returned HTTP {response.status}",
                                  diagnostics=diagnostics) from None
            while True:
                if cancel.is_set():
                    raise OllamaCancelled("Generation stopped")
                line = response.readline(_MAX_STREAM_LINE + 1)
                if cancel.is_set():
                    raise OllamaCancelled("Generation stopped")
                if len(line) > _MAX_STREAM_LINE:
                    raise OllamaError("Ollama returned an oversized streaming frame")
                if not line:
                    raise OllamaError("Ollama stream ended before its final done frame")
                if not line.strip():
                    continue
                frame = _stream_object(line)
                if guard is not None:
                    if frame["done"]:
                        frame = {**frame, "repetition": guard.summary()}
                    elif repetition := guard.observe(frame.get("message", {}).get("thinking", "")):
                        raise OllamaError("Generation stopped after sustained repeated thinking", diagnostics={
                            "failure_category": "repetition_detected", "native_completion": "unknown",
                            "automatic_retry": False, "repetition": repetition,
                        })
                yield frame
                if frame["done"]:
                    return
        except OllamaCancelled:
            raise
        except OllamaError as error:
            if expired.is_set():
                raise deadline_error(dispatched=dispatched) from error
            raise
        except (OSError, http.client.HTTPException, ValueError) as error:
            if expired.is_set() or deadline is not None and time.monotonic() >= deadline:
                raise deadline_error(dispatched=dispatched) from error
            if cancel.is_set():
                raise OllamaCancelled("Generation stopped") from error
            raise OllamaError("Ollama streaming request failed") from error
        finally:
            # Close before returning control to the shared inference scheduler.
            if response is not None:
                response.close()
            connection.close()
            finished.set()
            watcher.join(timeout=0.2)
