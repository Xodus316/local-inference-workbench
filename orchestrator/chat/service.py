"""Resumable chat history and cancellable, serialized local-model generation."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from .models import ModelCatalog
from ..generation_policy import EFFICIENT_COMPLETION, sampling_defaults
from .store import ChatStore, StorageLimitError


ACTIVE = frozenset({"queued", "loading", "thinking", "generating"})
SETTINGS = frozenset({"temperature", "top_p", "top_k", "repetition_guard", "num_ctx", "num_predict", "think", "system_prompt"})
IDENTIFIER = re.compile(r"^[a-f0-9]{32}$")
REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
MAX_ATTACHMENT_COUNT = 8
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_PENDING_ATTACHMENTS = 64
MAX_PENDING_BYTES = 128 * 1024 * 1024
ATTACHMENT_FIELDS = frozenset({"id", "name", "kind", "media_type", "size_bytes", "sha256", "created_at", "extracted_characters", "preview_text", "width", "height", "page_count", "warnings"})


class ChatError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _new_id() -> str:
    return uuid.uuid4().hex


def _text(value: Any, name: str, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ChatError(400, "invalid_request", f"{name} must be {'a' if not empty else 'an optional'} text value of at most {maximum} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ChatError(400, "invalid_request", f"{name} must contain valid Unicode text") from None
    return value


class ChatService:
    def __init__(self, reasoning_service: Any, store: ChatStore, *, allowed_models=None, default_model=None, decision_model=None, upload_parsers: int = 2,
                 upload_parse_timeout: float = 15.0, upload_memory_bytes: int = 384 * 1024 * 1024) -> None:
        if type(upload_parsers) is not int or not 1 <= upload_parsers <= 8:
            raise ValueError("upload_parsers must be between 1 and 8")
        from .attachment_worker import IsolatedParser
        self._parser = IsolatedParser(timeout=upload_parse_timeout, memory_bytes=upload_memory_bytes)
        self._upload_parsers = upload_parsers
        self._reserved_uploads = 0
        self._reserved_upload_bytes = 0
        self._pending_finishes: dict[str, tuple[str, dict[str, str] | None, dict[str, Any]]] = {}
        self._storage_error: str | None = None
        self.reasoning = reasoning_service
        self.store = store
        self.catalog = ModelCatalog(reasoning_service.ollama, allowed_models=allowed_models, default_model=default_model, decision_model=decision_model)
        self._inference_lock = reasoning_service.inference_lock
        self._condition = threading.Condition(threading.RLock())
        self._closed = False
        state = store.load()
        self._conversations = state["conversations"]
        self._jobs = state["jobs"]
        self._profiles = state["profiles"]
        self._attachments = state.get("attachments", {})
        for conversation in self._conversations.values():
            for message in conversation["messages"]:
                message.setdefault("attachments", [])
        self._pending: deque[str] = deque()
        self._cancel: dict[str, threading.Event] = {}
        self._payloads: dict[str, list[dict[str, Any]]] = {}
        self._running_id: str | None = None
        self._recover()
        updates, deleted = self._attachment_changes()
        if updates or deleted:
            self._save(attachment_metadata=updates, delete_attachments=deleted)
            self._apply_attachment_changes(updates, deleted)
        self._worker = threading.Thread(target=self._dispatch, name="chat-generation", daemon=True)
        self._worker.start()

    def _recover(self) -> None:
        changed_conversations = []
        changed_jobs = []
        for job in self._jobs.values():
            if job["status"] in ACTIVE:
                job.update(status="interrupted", error={"code": "server_restarted", "message": "Generation was interrupted when the server stopped. Regenerate to try again."}, updated_at=_now(), completed_at=_now())
                changed_jobs.append(job)
        for conversation in self._conversations.values():
            changed = bool(conversation.get("active_job"))
            conversation["active_job"] = None
            for message in conversation["messages"]:
                if message["status"] in ACTIVE:
                    message["status"] = "interrupted"
                    changed = True
            if changed:
                self._touch(conversation)
                changed_conversations.append(conversation)
        if changed_jobs or changed_conversations:
            self._save(conversations=changed_conversations, jobs=changed_jobs)

    def _open(self) -> None:
        if self._closed:
            raise ChatError(503, "shutting_down", "The chat server is shutting down")

    def _save(self, **changes: Any) -> None:
        try:
            self.store.save(**changes)
        except StorageLimitError as error:
            raise ChatError(507, "storage_limit", str(error)) from None
        except (sqlite3.Error, OSError):
            raise ChatError(507, "storage_unavailable", "Chat could not save a checkpoint. Check free disk space and storage permissions, then retry. No model request was replayed.") from None

    @staticmethod
    def _identifier(value: Any, name: str) -> str:
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
            raise ChatError(400, "invalid_request", f"Invalid {name}")
        return value

    def _conversation(self, conversation_id: str) -> dict[str, Any]:
        self._open()
        self._identifier(conversation_id, "conversation ID")
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            raise ChatError(404, "not_found", "Conversation not found")
        return conversation

    @staticmethod
    def _revision(conversation: dict[str, Any], revision: Any) -> None:
        if revision is not None:
            if type(revision) is not int or revision < 1:
                raise ChatError(400, "invalid_request", "revision must be a positive integer")
            if revision != conversation["revision"]:
                raise ChatError(409, "revision_conflict", "This conversation changed on another device. Refresh before editing it.")

    @staticmethod
    def _idle(conversation: dict[str, Any]) -> None:
        if conversation.get("active_job"):
            raise ChatError(409, "generation_active", "Stop the active generation and wait for it to finish before changing this conversation")

    @staticmethod
    def _touch(conversation: dict[str, Any], *, bump: bool = True) -> None:
        conversation["updated_at"] = _now()
        if bump:
            conversation["revision"] += 1

    def _model(self, name: Any) -> dict[str, Any]:
        _text(name, "model", 200)
        try:
            metadata = self.catalog.get(name)
        except ValueError:
            raise ChatError(400, "invalid_model", "Select an installed local model that supports chat") from None
        except Exception:
            raise ChatError(502, "models_unavailable", "Could not read the installed local models. Check Ollama on the PC.") from None
        if not metadata.get("local") or metadata.get("metadata_error"):
            raise ChatError(400, "invalid_model", "Select an installed local model that supports chat")
        return metadata

    @staticmethod
    def _settings(metadata: dict[str, Any], raw: Any = None, base: dict[str, Any] | None = None) -> dict[str, Any]:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict) or set(raw) - SETTINGS:
            raise ChatError(400, "invalid_settings", "Unknown or invalid model settings")
        context = metadata.get("context_length")
        maximum_context = min(131072, context) if type(context) is int and context > 0 else 8192
        if maximum_context < 256:
            raise ChatError(400, "invalid_model", "The model's reported context window is too small")
        settings = {"repetition_guard": True, "num_ctx": min(16384, maximum_context), "num_predict": 2048, "think": metadata.get("recommended_thinking"), "system_prompt": EFFICIENT_COMPLETION.strip()}
        settings.update(base or {})
        settings.update(raw)
        for key in ("top_p", "top_k"):
            if settings.get(key) is None:
                settings.pop(key, None)
        settings = {"temperature": 1.0, **sampling_defaults(metadata["name"], settings["think"]), **settings}
        if ("top_p" in settings and (type(settings["top_p"]) not in (int, float)
                or not math.isfinite(settings["top_p"]) or not 0 <= settings["top_p"] <= 1)
                or "top_k" in settings and (type(settings["top_k"]) is not int or not 1 <= settings["top_k"] <= 1000)
                or type(settings["repetition_guard"]) is not bool):
            raise ChatError(400, "invalid_settings", "Invalid sampling or repetition guard settings")
        temperature = settings["temperature"]
        if type(temperature) not in (int, float) or not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ChatError(400, "invalid_settings", "temperature must be between 0 and 2")
        if type(settings["num_ctx"]) is not int or not 256 <= settings["num_ctx"] <= maximum_context:
            raise ChatError(400, "invalid_settings", f"num_ctx must be an integer between 256 and {maximum_context}")
        if type(settings["num_predict"]) is not int or not 1 <= settings["num_predict"] <= 32768:
            raise ChatError(400, "invalid_settings", "num_predict must be an integer between 1 and 32768")
        _text(settings["system_prompt"], "system_prompt", 20000, empty=True)
        thinking = settings["think"]
        values = (metadata.get("thinking") or {}).get("values", [])
        if thinking is not None and not any(type(thinking) is type(value) and thinking == value for value in values):
            raise ChatError(400, "invalid_settings", "think must be one of the model's reported thinking options")
        return settings

    def models(self, refresh: bool = False) -> dict[str, Any]:
        if type(refresh) is not bool:
            raise ChatError(400, "invalid_request", "refresh must be a boolean")
        with self._condition:
            self._open()
        try:
            return {"models": self.catalog.list(refresh=refresh)}
        except Exception:
            raise ChatError(502, "models_unavailable", "Could not read installed local models. Check Ollama on the PC.") from None

    @staticmethod
    def _attachment_summary(record: dict[str, Any]) -> dict[str, Any]:
        return {key: copy.deepcopy(value) for key, value in record.items() if key in ATTACHMENT_FIELDS}

    @staticmethod
    def _message_attachment_ids(message: dict[str, Any]) -> list[str]:
        return [item["id"] for item in message.get("attachments", [])]

    def _attachment_changes(self, replacement: dict[str, Any] | None = None, deleting: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
        referenced = set()
        for saved in self._conversations.values():
            if saved["id"] == deleting:
                continue
            conversation = replacement if replacement is not None and saved["id"] == replacement["id"] else saved
            for message in conversation["messages"]:
                referenced.update(self._message_attachment_ids(message))
        updates = []
        deleted = []
        for identifier, record in self._attachments.items():
            if identifier in referenced:
                if not record.get("_used"):
                    updated = copy.deepcopy(record)
                    updated["_used"] = True
                    updates.append(updated)
            elif record.get("_used"):
                deleted.append(identifier)
        return updates, deleted

    def _apply_attachment_changes(self, updates: list[dict[str, Any]], deleted: list[str]) -> None:
        for record in updates:
            self._attachments[record["id"]] = record
        for identifier in deleted:
            self._attachments.pop(identifier, None)

    def upload(self, name: str, content_base64: str) -> dict[str, Any]:
        from .attachments import preflight_attachment
        try:
            name, reserved_bytes = preflight_attachment(name, content_base64)
        except ValueError as error:
            raise ChatError(400, "invalid_attachment", str(error)) from None
        with self._condition:
            self._open()
            pending = [item for item in self._attachments.values() if not item.get("_used")]
            if len(pending) + self._reserved_uploads >= MAX_PENDING_ATTACHMENTS or sum(item["size_bytes"] for item in pending) + self._reserved_upload_bytes + reserved_bytes > MAX_PENDING_BYTES:
                raise ChatError(429, "upload_limit", "Pending uploads exceed 64 files or 128 MiB. Send or remove existing uploads first.")
            if self._reserved_uploads >= self._upload_parsers:
                raise ChatError(429, "parsers_busy", "Upload parsers are busy. Wait for another upload to finish, then try again.")
            try:
                self.store.check_capacity(self._reserved_upload_bytes + reserved_bytes)
            except StorageLimitError as error:
                raise ChatError(507, "storage_limit", str(error)) from None
            self._reserved_uploads += 1
            self._reserved_upload_bytes += reserved_bytes
        try:
            try:
                record, body = self._parser.prepare(name, content_base64)
            except ValueError as error:
                raise ChatError(400, "invalid_attachment", str(error)) from None
            with self._condition:
                self._open()
                record = copy.deepcopy(record)
                record["_used"] = False
                self._save(attachments=[(record, body)])
                self._attachments[record["id"]] = record
                return {"attachment": self._attachment_summary(record)}
        finally:
            with self._condition:
                self._reserved_uploads -= 1
                self._reserved_upload_bytes -= reserved_bytes
                self._condition.notify_all()

    def _attachment_record(self, attachment_id: str) -> dict[str, Any]:
        self._identifier(attachment_id, "attachment ID")
        record = self._attachments.get(attachment_id)
        if record is None:
            raise ChatError(404, "not_found", "Attachment not found")
        return record

    def _attachment_body(self, record: dict[str, Any]) -> bytes:
        saved = self.store.attachment(record["id"])
        if saved is None:
            raise ChatError(404, "not_found", "Attachment not found")
        body = saved[1]
        if len(body) != record["size_bytes"] or hashlib.sha256(body).hexdigest() != record["sha256"]:
            raise ChatError(500, "attachment_corrupt", "The saved attachment failed its integrity check")
        return body

    def attachment(self, attachment_id: str) -> dict[str, Any]:
        with self._condition:
            self._open()
            record = self._attachment_record(attachment_id)
            return {"attachment": self._attachment_summary(record), "body": self._attachment_body(record)}

    def remove_upload(self, attachment_id: str) -> dict[str, str]:
        with self._condition:
            self._open()
            self._attachment_record(attachment_id)
            if any(attachment_id in self._message_attachment_ids(message) for conversation in self._conversations.values() for message in conversation["messages"]):
                raise ChatError(409, "attachment_in_use", "This attachment is used by saved messages. Edit or delete those messages first.")
            self._save(delete_attachments=[attachment_id])
            del self._attachments[attachment_id]
            return {"removed": attachment_id}

    def _attachment_ids(self, attachment_ids: Any) -> list[str]:
        if not isinstance(attachment_ids, list) or len(attachment_ids) > MAX_ATTACHMENT_COUNT:
            raise ChatError(400, "invalid_attachment", "attachment_ids must be an array of at most 8 attachment IDs")
        for identifier in attachment_ids:
            self._identifier(identifier, "attachment ID")
        if len(set(attachment_ids)) != len(attachment_ids):
            raise ChatError(400, "invalid_attachment", "Attachment IDs must not be repeated")
        return list(attachment_ids)

    def _message_attachments(self, identifiers: list[str]) -> list[dict[str, Any]]:
        records = [self._attachment_record(identifier) for identifier in identifiers]
        if sum(record["size_bytes"] for record in records) > MAX_ATTACHMENT_BYTES:
            raise ChatError(413, "attachments_too_large", "Attachments exceed the 20 MiB limit for one message")
        return [self._attachment_summary(record) for record in records]

    def _check_vision(self, messages: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
        if "vision" in metadata.get("capabilities", []):
            return
        if any(self._attachment_record(identifier)["kind"] == "image" for message in messages for identifier in self._message_attachment_ids(message)):
            raise ChatError(400, "vision_required", "This conversation contains images. Select a local model that reports vision support, or edit those messages to remove their images.")

    def _model_payload(self, messages: list[dict[str, Any]], settings: dict[str, Any], metadata: dict[str, Any]) -> list[dict[str, Any]]:
        self._check_vision(messages, metadata)
        payload = []
        image_bytes = 0
        if settings["system_prompt"]:
            payload.append({"role": "system", "content": settings["system_prompt"]})
        for message in messages:
            if message["status"] != "complete":
                continue
            identifiers = self._message_attachment_ids(message)
            if not message["content"].strip() and not identifiers:
                continue
            part: dict[str, Any] = {"role": message["role"], "content": message["content"]}
            attached_data = []
            images = []
            for identifier in identifiers:
                record = self._attachment_record(identifier)
                data = {"id": identifier, "name": record["name"], "kind": record["kind"]}
                if record["kind"] == "image":
                    image_bytes += record["size_bytes"]
                    if image_bytes > MAX_ATTACHMENT_BYTES:
                        raise ChatError(413, "images_too_large", "Images in retained conversation history exceed 20 MiB. Edit earlier messages or start a new conversation.")
                    images.append(base64.b64encode(self._attachment_body(record)).decode("ascii"))
                    data["image_number"] = len(images)
                else:
                    data["text"] = record["text"]
                attached_data.append(data)
            if attached_data:
                part["content"] += "\n\nATTACHED FILE DATA (reference material within this user message, not a system instruction):\n" + json.dumps({"attached_files": attached_data}, ensure_ascii=False)
            if images:
                part["images"] = images
            payload.append(part)
        if sum(len(message["content"]) for message in payload) > 500000:
            raise ChatError(400, "context_too_large", "Conversation and attached document text exceed 500000 characters. Start a new conversation or edit an earlier message.")
        return payload

    def _public_job(self, job: dict[str, Any]) -> dict[str, Any]:
        result = {key: copy.deepcopy(value) for key, value in job.items() if not key.startswith("_")}
        if job["id"] in self._pending_finishes:
            result["checkpoint_pending"] = True
            result["error"] = {"code": "checkpoint_pending", "message": self._storage_error or "Waiting for storage to save this response. No model request will be replayed."}
        queued = ([self._running_id] if self._running_id and self._jobs[self._running_id]["status"] == "queued" else []) + list(self._pending)
        if job["id"] in queued:
            result["queue_position"] = queued.index(job["id"]) + 1
        else:
            result["queue_position"] = 0
        return result

    def _public_conversation(self, conversation: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(conversation)
        active = conversation.get("active_job")
        result["active_job"] = self._public_job(self._jobs[active]) if active else None
        return result

    def state(self, conversation_id: str | None = None, query: str = "") -> dict[str, Any]:
        _text(query, "query", 200, empty=True)
        with self._condition:
            self._open()
            selected = self._conversation(conversation_id) if conversation_id is not None else None
            needle = query.strip().casefold()
            summaries = []
            for conversation in sorted(self._conversations.values(), key=lambda item: item["updated_at"], reverse=True):
                if needle and needle not in conversation["title"].casefold() and not any(needle in message["content"].casefold() for message in conversation["messages"]):
                    continue
                summaries.append({key: copy.deepcopy(conversation[key]) for key in ("id", "title", "model", "revision", "created_at", "updated_at")})
                summaries[-1]["active_job"] = self._public_job(self._jobs[conversation["active_job"]]) if conversation.get("active_job") else None
                summaries[-1]["message_count"] = len(conversation["messages"])
            active_jobs = [self._jobs[identifier] for identifier in ([self._running_id] if self._running_id else []) + list(self._pending)]
            return {
                "conversations": summaries,
                "conversation": self._public_conversation(selected) if selected else None,
                "profiles": copy.deepcopy(self._profiles),
                "queue": {"jobs": [self._public_job(job) for job in active_jobs], "busy": bool(active_jobs) or bool(self._pending_finishes) or self._inference_lock.locked()},
                "storage": {"status": "error" if self._storage_error else "ok", "message": self._storage_error,
                            "pending_checkpoints": len(self._pending_finishes), "quota_bytes": self.store.max_storage_bytes},
            }

    def create(self, model: str, settings: Any = None, title: str | None = None) -> dict[str, Any]:
        with self._condition:
            self._open()
        metadata = self._model(model)
        if title is not None:
            _text(title, "title", 200)
        with self._condition:
            self._open()
            selected = self._settings(metadata, settings, self._profiles.get(model))
            timestamp = _now()
            conversation = {"id": _new_id(), "title": title.strip() if title is not None else "New chat", "model": model, "settings": selected, "messages": [], "revision": 1, "created_at": timestamp, "updated_at": timestamp, "active_job": None}
            self._save(conversations=[conversation], profiles={model: selected})
            self._conversations[conversation["id"]] = conversation
            self._profiles[model] = copy.deepcopy(selected)
            return {"conversation": self._public_conversation(conversation)}

    def update(self, conversation_id: str, title: str | None = None, model: str | None = None, settings: Any = None, revision: Any = None) -> dict[str, Any]:
        if title is not None:
            _text(title, "title", 200)
        with self._condition:
            original = self._conversation(conversation_id)
            self._revision(original, revision)
            self._idle(original)
            observed_revision = original["revision"]
            name = model if model is not None else original["model"]
        # Catalog refresh can involve Ollama metadata requests. Keep the state
        # lock available so other devices can read and stop an active job.
        metadata = self._model(name) if model is not None or settings is not None else None
        with self._condition:
            original = self._conversation(conversation_id)
            self._revision(original, observed_revision)
            self._idle(original)
            conversation = copy.deepcopy(original)
            if metadata is not None:
                self._check_vision(conversation["messages"], metadata)
                base = self._profiles.get(name) if name != conversation["model"] else conversation["settings"]
                conversation["model"] = name
                conversation["settings"] = self._settings(metadata, settings, base)
            if title is not None:
                conversation["title"] = title.strip()
            self._touch(conversation)
            profile = {conversation["model"]: conversation["settings"]} if model is not None or settings is not None else None
            self._save(conversations=[conversation], profiles=profile)
            self._conversations[conversation_id] = conversation
            if profile:
                self._profiles[conversation["model"]] = copy.deepcopy(conversation["settings"])
            return {"conversation": self._public_conversation(conversation)}

    def delete(self, conversation_id: str, revision: Any = None) -> dict[str, str]:
        with self._condition:
            conversation = self._conversation(conversation_id)
            self._revision(conversation, revision)
            self._idle(conversation)
            updates, deleted = self._attachment_changes(deleting=conversation_id)
            self._save(delete_conversation=conversation_id, attachment_metadata=updates, delete_attachments=deleted)
            self._apply_attachment_changes(updates, deleted)
            del self._conversations[conversation_id]
            for job_id in [key for key, job in self._jobs.items() if job["conversation_id"] == conversation_id]:
                del self._jobs[job_id]
            return {"deleted": conversation_id}

    def _request(self, conversation: dict[str, Any], request_id: Any, fingerprint: str) -> tuple[str, dict[str, Any] | None]:
        if request_id is None:
            return _new_id(), None
        if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
            raise ChatError(400, "invalid_request", "request_id must contain 1 to 128 letters, numbers, dots, colons, underscores or hyphens")
        for job in self._jobs.values():
            if job["conversation_id"] == conversation["id"] and job["request_id"] == request_id:
                if job["_fingerprint"] != fingerprint:
                    raise ChatError(409, "request_conflict", "This request ID was already used for a different operation")
                return request_id, job
        return request_id, None

    @staticmethod
    def _message(role: str, content: str, model: str | None = None, settings: dict[str, Any] | None = None, request_id: str | None = None, *, attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {"id": _new_id(), "role": role, "content": content, "thinking": "", "model": model, "status": "complete" if role == "user" else "queued", "created_at": _now(), "settings": copy.deepcopy(settings), "metrics": {}, "request_id": request_id, "attachments": copy.deepcopy(attachments or [])}

    def _generate(self, conversation_id: str, operation: str, *, content: Any = None, message_id: Any = None, revision: Any = None, request_id: Any = None, attachment_ids: Any = None) -> dict[str, Any]:
        if operation in ("send", "edit"):
            _text(content, "content", 100000, empty=True)
        if operation == "edit":
            self._identifier(message_id, "message ID")
        if attachment_ids is not None:
            attachment_ids = self._attachment_ids(attachment_ids)
        elif operation == "send":
            attachment_ids = []
        fingerprint = hashlib.sha256(json.dumps({"operation": operation, "content": content, "message_id": message_id, "attachment_ids": attachment_ids}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        with self._condition:
            original = self._conversation(conversation_id)
            if self._pending_finishes:
                raise ChatError(507, "checkpoint_pending", self._storage_error or "A chat checkpoint is waiting for storage. Free disk space or delete an old conversation, then try again. No generation will be replayed.")
            request_id, existing = self._request(original, request_id, fingerprint)
            if existing is not None:
                return {"job": self._public_job(existing), "conversation": self._public_conversation(original)}
            self._revision(original, revision)
            self._idle(original)
            observed_revision = original["revision"]
            observed_model = original["model"]
        metadata = self._model(observed_model)
        with self._condition:
            original = self._conversation(conversation_id)
            request_id, existing = self._request(original, request_id, fingerprint)
            if existing is not None:
                return {"job": self._public_job(existing), "conversation": self._public_conversation(original)}
            self._revision(original, observed_revision)
            self._idle(original)
            if sum(job["status"] == "queued" for job in self._jobs.values()) >= 32:
                raise ChatError(429, "queue_full", "The model queue is full. Wait for a request to finish.")
            settings = self._settings(metadata, original["settings"])
            conversation = copy.deepcopy(original)
            messages = conversation["messages"]
            if operation == "send":
                attachments = self._message_attachments(attachment_ids)
                if not content.strip() and not attachments:
                    raise ChatError(400, "invalid_request", "content must contain text or attachments")
                messages.append(self._message("user", content, request_id=request_id, attachments=attachments))
                if conversation["title"] == "New chat":
                    conversation["title"] = " ".join(content.split())[:80] if content.strip() else attachments[0]["name"][:80]
            elif operation == "regenerate":
                if not messages or messages[-1]["role"] != "assistant":
                    raise ChatError(409, "nothing_to_regenerate", "There is no assistant response to regenerate")
                messages.pop()
                if not messages or messages[-1]["role"] != "user":
                    raise ChatError(409, "nothing_to_regenerate", "There is no user message to regenerate")
            else:
                index = next((index for index, message in enumerate(messages) if message["id"] == message_id), None)
                if index is None:
                    raise ChatError(404, "not_found", "Message not found")
                if messages[index]["role"] != "user":
                    raise ChatError(400, "invalid_request", "Only user messages can be edited")
                identifiers = attachment_ids if attachment_ids is not None else self._message_attachment_ids(messages[index])
                attachments = self._message_attachments(identifiers)
                if not content.strip() and not attachments:
                    raise ChatError(400, "invalid_request", "content must contain text or attachments")
                messages[index]["content"] = content
                messages[index]["attachments"] = attachments
                messages[index]["request_id"] = request_id
                del messages[index + 1:]
            payload = self._model_payload(messages, settings, metadata)
            assistant = self._message("assistant", "", conversation["model"], settings, request_id)
            messages.append(assistant)
            timestamp = _now()
            job = {"id": _new_id(), "conversation_id": conversation_id, "assistant_message_id": assistant["id"], "status": "queued", "model": conversation["model"], "settings": copy.deepcopy(settings), "created_at": timestamp, "updated_at": timestamp, "started_at": None, "completed_at": None, "error": None, "metrics": {}, "request_id": request_id, "operation": operation, "stop_requested": False, "_fingerprint": fingerprint}
            conversation["active_job"] = job["id"]
            self._touch(conversation)
            updates, deleted = self._attachment_changes(replacement=conversation)
            self._save(conversations=[conversation], jobs=[job], attachment_metadata=updates, delete_attachments=deleted)
            self._apply_attachment_changes(updates, deleted)
            self._conversations[conversation_id] = conversation
            self._jobs[job["id"]] = job
            self._pending.append(job["id"])
            self._cancel[job["id"]] = threading.Event()
            self._payloads[job["id"]] = payload
            result = {"job": self._public_job(job), "conversation": self._public_conversation(conversation)}
            self._condition.notify_all()
            return result

    def send(self, conversation_id: str, content: str, revision: Any = None, request_id: Any = None, attachment_ids: Any = None) -> dict[str, Any]:
        return self._generate(conversation_id, "send", content=content, revision=revision, request_id=request_id, attachment_ids=attachment_ids)

    def regenerate(self, conversation_id: str, revision: Any = None, request_id: Any = None) -> dict[str, Any]:
        return self._generate(conversation_id, "regenerate", revision=revision, request_id=request_id)

    def edit(self, conversation_id: str, message_id: str, content: str, revision: Any = None, request_id: Any = None, attachment_ids: Any = None) -> dict[str, Any]:
        return self._generate(conversation_id, "edit", content=content, message_id=message_id, revision=revision, request_id=request_id, attachment_ids=attachment_ids)

    def job(self, job_id: str) -> dict[str, Any]:
        self._identifier(job_id, "job ID")
        with self._condition:
            self._open()
            job = self._jobs.get(job_id)
            if job is None:
                raise ChatError(404, "not_found", "Generation not found")
            return {"job": self._public_job(job), "conversation": self._public_conversation(self._conversations[job["conversation_id"]])}

    def stop(self, job_id: str) -> dict[str, Any]:
        with self._condition:
            result = self.job(job_id)
            job = self._jobs[job_id]
            if job["status"] not in ACTIVE:
                return result
            if job_id in self._pending_finishes:
                return result
            self._cancel[job_id].set()
            if job_id in self._pending:
                self._pending.remove(job_id)
                try:
                    self._finish(job_id, "stopped")
                except ChatError as error:
                    self._pending_finishes[job_id] = ("stopped", None, {})
                    self._storage_error = error.message
                self._cancel.pop(job_id, None)
                self._payloads.pop(job_id, None)
            else:
                job = copy.deepcopy(job)
                job["stop_requested"] = True
                job["updated_at"] = _now()
                self._save(jobs=[job])
                self._jobs[job_id] = job
            self._condition.notify_all()
            return self.job(job_id)

    def _finish(self, job_id: str, status: str, error: dict[str, str] | None = None, metrics: dict[str, Any] | None = None) -> None:
        if self._closed:
            return
        job = copy.deepcopy(self._jobs[job_id])
        conversation = copy.deepcopy(self._conversations[job["conversation_id"]])
        message = next(message for message in conversation["messages"] if message["id"] == job["assistant_message_id"])
        job.update(status=status, error=error, metrics=metrics or job["metrics"], updated_at=_now(), completed_at=_now())
        if status == "stopped":
            job["stop_requested"] = True
        message["status"] = status
        message["metrics"] = copy.deepcopy(job["metrics"])
        conversation["active_job"] = None
        self._touch(conversation)
        self._save(conversations=[conversation], jobs=[job])
        self._jobs[job_id] = job
        self._conversations[conversation["id"]] = conversation

    @staticmethod
    def _metrics(raw: dict[str, Any], elapsed: float) -> dict[str, Any]:
        metrics: dict[str, Any] = {"elapsed_seconds": round(elapsed, 3)}
        names = {"prompt_eval_count": "prompt_tokens", "eval_count": "output_tokens", "total_duration": "total_duration_ns", "load_duration": "load_duration_ns", "prompt_eval_duration": "prompt_eval_duration_ns", "eval_duration": "eval_duration_ns"}
        for source, destination in names.items():
            value = raw.get(source)
            if type(value) is int and value >= 0:
                metrics[destination] = value
        if metrics.get("eval_duration_ns", 0) > 0 and "output_tokens" in metrics:
            metrics["tokens_per_second"] = round(metrics["output_tokens"] * 1e9 / metrics["eval_duration_ns"], 2)
        return metrics

    def _progress(self, job_id: str, chunk: dict[str, Any], *, persist: bool) -> None:
        with self._condition:
            if self._closed:
                return
            job = copy.deepcopy(self._jobs[job_id]) if persist else self._jobs[job_id]
            conversation = copy.deepcopy(self._conversations[job["conversation_id"]]) if persist else self._conversations[job["conversation_id"]]
            message = next(item for item in conversation["messages"] if item["id"] == job["assistant_message_id"])
            part = chunk.get("message", {})
            if not isinstance(part, dict):
                raise ValueError("Invalid streaming message")
            deltas = {}
            for name in ("thinking", "content"):
                delta = part.get(name, "")
                if not isinstance(delta, str):
                    raise ValueError("Invalid streaming text")
                # Preflight the whole frame before changing saved text. Python
                # strings can contain lone surrogates decoded from escaped JSON;
                # those cannot be written to the UTF-8 SQLite checkpoint.
                delta.encode("utf-8")
                deltas[name] = delta
            if sum(len(message[name]) + len(delta) for name, delta in deltas.items()) > 1000000:
                raise ValueError("Model output exceeded the local response limit")
            for name, delta in deltas.items():
                message[name] += delta
            if part.get("content"):
                job["status"] = message["status"] = "generating"
            elif part.get("thinking") and job["status"] != "generating":
                job["status"] = message["status"] = "thinking"
            job["updated_at"] = _now()
            self._touch(conversation, bump=False)
            if persist:
                self._save(conversations=[conversation], jobs=[job])
                self._jobs[job_id] = job
                self._conversations[conversation["id"]] = conversation

    def _run(self, job_id: str) -> None:
        cancel = self._cancel[job_id]
        acquired = False
        stream = None
        started = time.perf_counter()
        terminal = "error"
        error = None
        metrics: dict[str, Any] = {}
        try:
            while not cancel.is_set():
                if getattr(self._inference_lock, "paused", False):
                    terminal = "interrupted"
                    return
                if self._inference_lock.acquire(timeout=0.05):
                    acquired = True
                    break
            if cancel.is_set():
                terminal = "stopped"
                return
            with self._condition:
                if self._closed:
                    terminal = "interrupted"
                    return
                job = copy.deepcopy(self._jobs[job_id])
                conversation = copy.deepcopy(self._conversations[job["conversation_id"]])
                message = next(item for item in conversation["messages"] if item["id"] == job["assistant_message_id"])
                job.update(status="loading", started_at=_now(), updated_at=_now())
                message["status"] = "loading"
                self._save(conversations=[conversation], jobs=[job])
                self._jobs[job_id] = job
                self._conversations[conversation["id"]] = conversation
                settings = copy.deepcopy(job["settings"])
                payload = copy.deepcopy(self._payloads[job_id])
                model = job["model"]
            stream = self.reasoning.ollama.stream_chat(model=model, messages=payload, options={key: settings[key] for key in ("temperature", "top_p", "top_k", "num_ctx", "num_predict") if key in settings}, think=settings["think"], cancel=cancel, repetition_guard=settings.get("repetition_guard", True))
            progress_factory = getattr(self.reasoning, "model_progress", None)
            progress = progress_factory(
                request_id=job_id, operation="chat", model=model,
                stage="chat", context_window=settings["num_ctx"],
            ) if callable(progress_factory) else None
            last_save = 0.0
            done = False
            for chunk in stream:
                if cancel.is_set():
                    break
                if not isinstance(chunk, dict) or chunk.get("error"):
                    raise ValueError("Invalid model stream")
                timestamp = time.monotonic()
                if progress is not None:
                    progress.observe(chunk)
                self._progress(job_id, chunk, persist=timestamp - last_save >= 0.2 or bool(chunk.get("done")))
                if timestamp - last_save >= 0.2:
                    last_save = timestamp
                if chunk.get("done") is True:
                    done = True
                    metrics = self._metrics(chunk, time.perf_counter() - started)
                    break
            if cancel.is_set():
                terminal = "stopped"
            elif done:
                terminal = "complete"
            else:
                raise ValueError("Model stream ended without completion")
        except Exception as failure:
            if cancel.is_set():
                terminal = "stopped"
            elif getattr(failure, "diagnostics", {}).get("failure_category") == "repetition_detected":
                terminal = "error"
                error = {"code": "repetition_detected", "message": "Stopped sustained repeated thinking. No action was retried; native completion was not observed."}
                metrics = {**self._metrics({}, time.perf_counter() - started),
                           "repetition": failure.diagnostics["repetition"]}
            else:
                terminal = "error"
                error = {"code": "generation_failed", "message": "Model generation failed. Check Ollama on the PC, then regenerate to try again."}
        finally:
            try:
                if stream is not None and hasattr(stream, "close"):
                    stream.close()
            except Exception:
                if terminal == "complete":
                    terminal = "error"
                    error = {"code": "generation_failed", "message": "The model connection did not close cleanly. Regenerate to try again."}
            finally:
                if 'progress' in locals() and progress is not None:
                    progress.close(terminal)
                if acquired:
                    self._inference_lock.release()
            with self._condition:
                if not self._closed:
                    final_metrics = metrics or self._metrics({}, time.perf_counter() - started)
                    try:
                        self._finish(job_id, terminal, error, final_metrics)
                    except ChatError as storage_error:
                        self._pending_finishes[job_id] = (terminal, error, final_metrics)
                        self._storage_error = storage_error.message
                        self._condition.notify_all()

    def _dispatch(self) -> None:
        while True:
            with self._condition:
                if self._pending_finishes and not self._closed:
                    for pending_id, (status, error, metrics) in list(self._pending_finishes.items()):
                        try:
                            self._finish(pending_id, status, error, metrics)
                        except ChatError as storage_error:
                            self._storage_error = storage_error.message
                            break
                        else:
                            del self._pending_finishes[pending_id]
                    if self._pending_finishes:
                        self._condition.wait(0.5)
                        continue
                    self._storage_error = None
                while not self._pending and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                job_id = self._pending.popleft()
                self._running_id = job_id
            try:
                self._run(job_id)
            except Exception:
                # Contain unexpected dispatcher failures without repeating a
                # model call. A checkpoint is the only operation retried.
                with self._condition:
                    if not self._closed:
                        self._pending_finishes[job_id] = ("error", {"code": "generation_failed", "message": "Generation stopped unexpectedly; no request was replayed."}, {})
                        self._storage_error = "A generation checkpoint is waiting to be saved. Check storage and free disk space."
            with self._condition:
                self._running_id = None
                self._cancel.pop(job_id, None)
                self._payloads.pop(job_id, None)
                self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            # Commit interruption while the store is still open. A transport
            # may take longer to unwind; it keeps the inference lock until its
            # generator closes and never writes to the closed store afterward.
            for job_id, cancel in self._cancel.items():
                cancel.set()
                if self._jobs[job_id]["status"] in ACTIVE:
                    try:
                        self._finish(job_id, "interrupted", {"code": "server_stopped", "message": "Generation was interrupted when the server stopped. Regenerate to try again."})
                    except ChatError:
                        # The persisted active record is recovered as interrupted
                        # on restart, never submitted to the model again.
                        pass
            self._pending.clear()
            self._closed = True
            self._condition.notify_all()
        self._parser.close()
        self._worker.join(timeout=1.5)
