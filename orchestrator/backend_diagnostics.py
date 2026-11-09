"""Small allowlist for diagnostics received from a private backend."""

from __future__ import annotations

from typing import Any


BACKEND_ERROR_CODES = frozenset({
    "admission_full",
    "backend_unavailable",
    "backend_http_error",
    "context_limit_exceeded",
    "invalid_request",
    "model_unavailable",
    "out_of_memory",
    "request_too_large",
    "thinking_unsupported",
    "queue_wait_timeout",
    "server_draining",
    "service_busy",
})


def safe_backend_diagnostics(value: Any, token: str | None = None) -> dict[str, Any]:
    """Return bounded backend HTTP metadata, never a backend error message."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    status = value.get("backend_http_status")
    if type(status) is int and 100 <= status <= 599:
        result["backend_http_status"] = status
    code = value.get("backend_error_code")
    if (isinstance(code, str) and code in BACKEND_ERROR_CODES
            and not (isinstance(token, str) and token and token in code)):
        result["backend_error_code"] = code
    completion = value.get("native_completion")
    if completion in {"not_started", "unknown", "terminal"}:
        result["native_completion"] = completion
    retry = value.get("automatic_retry")
    if type(retry) is bool:
        result["automatic_retry"] = retry
    attempts = value.get("native_attempts")
    if type(attempts) is int and 0 <= attempts <= 100:
        result["native_attempts"] = attempts
    return result
