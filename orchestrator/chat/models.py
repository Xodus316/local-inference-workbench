"""Advertised local model capabilities and separate generation-policy defaults."""

from __future__ import annotations

import copy
import threading
import time
from typing import Any

from ..generation_policy import EFFICIENT_COMPLETION, sampling_defaults


class ModelCatalog:
    def __init__(self, ollama: Any, cache_seconds: float = 60.0, *, allowed_models=None, default_model=None, decision_model=None) -> None:
        self.ollama = ollama
        self.allowed_models = allowed_models
        self.default_model = default_model
        self.decision_model = decision_model
        self._cache_seconds = min(60.0, max(0.0, cache_seconds))
        self._lock = threading.RLock()
        self._cached: list[dict[str, Any]] | None = None
        self._cached_at = 0.0

    def list(self, refresh: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            now = time.monotonic()
            if not refresh and self._cached is not None and now - self._cached_at < self._cache_seconds:
                return copy.deepcopy(self._cached)
            installed = self.ollama.models()
            running = self.ollama.loaded_models()
            if not isinstance(installed, list) or not isinstance(running, list):
                raise ValueError("Ollama returned an invalid model catalog")
            loaded = {
                item.get("name", item.get("model"))
                for item in running
                if isinstance(item, dict) and isinstance(item.get("name", item.get("model")), str)
            }
            entries: list[dict[str, Any]] = []
            seen: set[str] = set()
            for item in installed:
                if not isinstance(item, dict):
                    continue
                name = item.get("name", item.get("model"))
                if not isinstance(name, str) or not name or name in seen:
                    continue
                if self.allowed_models is not None and name not in self.allowed_models:
                    continue
                seen.add(name)
                entry: dict[str, Any] = {
                    "name": name,
                    "size": item.get("size") if type(item.get("size")) is int and item["size"] >= 0 else None,
                    "loaded": name in loaded,
                    "capabilities": [],
                    "context_length": None,
                    "thinking": None,
                    "local": not _remote(item, name),
                    "recommended_default": name == self.default_model,
                    "recommended_thinking": None,
                    "recommended_context": 16384,
                    "recommended_sampling": sampling_defaults(name, None),
                    "recommended_system_prompt": EFFICIENT_COMPLETION.strip(),
                }
                # Cloud entries remain visible, but are never sent to /api/show.
                if not entry["local"]:
                    entries.append(entry)
                    continue
                try:
                    metadata = self.ollama.show(name)
                    if not isinstance(metadata, dict):
                        raise ValueError("Invalid model metadata")
                    entry["local"] = not _remote(metadata, name)
                    capabilities = metadata.get("capabilities")
                    if capabilities is not None:
                        if not isinstance(capabilities, list) or not all(isinstance(value, str) for value in capabilities):
                            raise ValueError("Invalid model capabilities")
                        entry["capabilities"] = list(dict.fromkeys(capabilities))
                        entry["usable"] = "completion" in capabilities
                    else:
                        entry["usable"] = True
                    entry["context_length"] = _context_length(metadata.get("model_info"))
                    entry["thinking"] = _thinking(metadata.get("thinking"))
                    if "decision" in entry["capabilities"]:
                        entry["usable"] = False
                except Exception:
                    entry["metadata_error"] = "Could not read this model's Ollama metadata"
                    entry["usable"] = False
                if name == self.decision_model or name.startswith("pplx-decider:"):
                    entry["usable"] = False
                entries.append(entry)
            self._cached = entries
            self._cached_at = time.monotonic()
            return copy.deepcopy(entries)

    def get(self, name: str) -> dict[str, Any]:
        if not isinstance(name, str) or not name:
            raise ValueError("Select an installed local model")
        for entry in self.list():
            if entry["name"] != name:
                continue
            if not entry["local"]:
                raise ValueError("Cloud models are not available in local chat")
            if entry.get("metadata_error"):
                raise ValueError("Model metadata is unavailable; refresh models and try again")
            if not entry.get("usable", True):
                raise ValueError("This model does not support conversational completion")
            return entry
        raise ValueError("The selected model is not installed")


def _remote(metadata: dict[str, Any], name: str) -> bool:
    return bool(metadata.get("remote_host") or metadata.get("remote_model") or name.lower().endswith(":cloud"))


def _context_length(model_info: Any) -> int | None:
    if not isinstance(model_info, dict):
        return None
    architecture = model_info.get("general.architecture")
    if isinstance(architecture, str):
        direct = model_info.get(f"{architecture}.context_length")
        if type(direct) is int and direct > 0:
            return direct
    for key, value in model_info.items():
        if isinstance(key, str) and key.endswith(".context_length") and type(value) is int and value > 0:
            return value
    return None


def _thinking(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    values = value.get("values")
    if not isinstance(values, list) or not values:
        return None
    if not all(type(item) is bool or isinstance(item, str) and 0 < len(item) <= 64 for item in values):
        return None
    if len({(type(item), item) for item in values}) != len(values):
        return None
    default = value.get("default")
    if "default" not in value or not any(type(default) is type(item) and default == item for item in values):
        return None
    return {"values": copy.deepcopy(values), "default": default}
