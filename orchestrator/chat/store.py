"""Private SQLite storage for shared conversation history and model preferences."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable


DEFAULT_CHAT_PATH = Path.home() / ".local-inference-workbench" / "chat.sqlite3"
DEFAULT_STORAGE_BYTES = 512 * 1024 * 1024


class StorageLimitError(RuntimeError):
    """The retained logical content would exceed the configured quota."""


class ChatStore:
    def __init__(self, path: str | Path = DEFAULT_CHAT_PATH, *, max_storage_bytes: int = DEFAULT_STORAGE_BYTES) -> None:
        if type(max_storage_bytes) is not int or max_storage_bytes < 1024:
            raise ValueError("Chat storage quota must be at least 1024 bytes")
        self.max_storage_bytes = max_storage_bytes
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._closed = False
        if self.path.is_symlink():
            raise ValueError("Chat database must not be a symbolic link")
        self._private_file(self.path)
        lease_path = self.path.with_name(self.path.name + ".lock")
        if lease_path.is_symlink():
            raise ValueError("Chat database lock must not be a symbolic link")
        self._private_file(lease_path)
        self._lease = sqlite3.connect(lease_path, timeout=0, check_same_thread=False)
        try:
            self._lease.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError:
            self._lease.close()
            raise ValueError("This chat database is already open in another server") from None
        try:
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported chat database version")
            with self._db:
                self._db.execute("PRAGMA synchronous = FULL")
                self._db.execute("CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, state TEXT NOT NULL)")
                self._db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, state TEXT NOT NULL)")
                self._db.execute("CREATE TABLE IF NOT EXISTS profiles (model TEXT PRIMARY KEY, settings TEXT NOT NULL)")
                # Additive to version 1: existing chat databases gain uploads
                # without rewriting their conversation history.
                self._db.execute("CREATE TABLE IF NOT EXISTS attachments (id TEXT PRIMARY KEY, metadata TEXT NOT NULL, body BLOB NOT NULL)")
                self._db.execute("PRAGMA user_version = 1")
        except Exception:
            if hasattr(self, "_db"):
                self._db.close()
            self._lease.close()
            raise

    @staticmethod
    def _private_file(path: Path) -> None:
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        path.chmod(0o600)

    def load(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                "conversations": {row[0]: json.loads(row[1]) for row in self._db.execute("SELECT id, state FROM conversations")},
                "jobs": {row[0]: json.loads(row[1]) for row in self._db.execute("SELECT id, state FROM jobs")},
                "profiles": {row[0]: json.loads(row[1]) for row in self._db.execute("SELECT model, settings FROM profiles")},
                "attachments": {row[0]: json.loads(row[1]) for row in self._db.execute("SELECT id, metadata FROM attachments")},
            }

    def attachment(self, attachment_id: str) -> tuple[dict[str, Any], bytes] | None:
        with self._lock:
            row = self._db.execute("SELECT metadata, body FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
            return (json.loads(row[0]), bytes(row[1])) if row else None

    def _retained_bytes(self) -> int:
        total = 0
        for table, expression in (("conversations", "length(CAST(state AS BLOB))"),
                                  ("jobs", "length(CAST(state AS BLOB))"),
                                  ("profiles", "length(CAST(settings AS BLOB))"),
                                  ("attachments", "length(CAST(metadata AS BLOB)) + length(body)")):
            total += self._db.execute(f"SELECT COALESCE(SUM({expression}), 0) FROM {table}").fetchone()[0]
        return total

    def check_capacity(self, additional_bytes: int = 0) -> None:
        with self._lock:
            if self._retained_bytes() + additional_bytes > self.max_storage_bytes:
                raise StorageLimitError("Chat storage quota reached. Delete old conversations or remove unused uploads, then try again. Saved history was not deleted.")

    def save(
        self,
        *,
        conversations: Iterable[dict[str, Any]] = (),
        jobs: Iterable[dict[str, Any]] = (),
        profiles: dict[str, dict[str, Any]] | None = None,
        delete_conversation: str | None = None,
        attachments: Iterable[tuple[dict[str, Any], bytes]] = (),
        attachment_metadata: Iterable[dict[str, Any]] = (),
        delete_attachments: Iterable[str] = (),
    ) -> None:
        # Serialize before opening the transaction; serialization failures cannot
        # leave a partially saved edit or generation checkpoint.
        encode = lambda item: json.dumps(item, ensure_ascii=False, allow_nan=False)
        conversation_rows = [(item["id"], encode(item)) for item in conversations]
        job_rows = [(item["id"], item["conversation_id"], encode(item)) for item in jobs]
        profile_rows = [(model, encode(settings)) for model, settings in (profiles or {}).items()]
        attachment_rows = [(item["id"], encode(item), sqlite3.Binary(body)) for item, body in attachments]
        attachment_updates = [(encode(item), item["id"]) for item in attachment_metadata]
        attachment_deletes = [(identifier,) for identifier in delete_attachments]
        with self._lock, self._db:
            before = self._retained_bytes()
            if delete_conversation is not None:
                self._db.execute("DELETE FROM jobs WHERE conversation_id = ?", (delete_conversation,))
                self._db.execute("DELETE FROM conversations WHERE id = ?", (delete_conversation,))
            self._db.executemany("INSERT OR REPLACE INTO conversations VALUES (?, ?)", conversation_rows)
            self._db.executemany("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?)", job_rows)
            self._db.executemany("INSERT OR REPLACE INTO profiles VALUES (?, ?)", profile_rows)
            self._db.executemany("INSERT OR REPLACE INTO attachments VALUES (?, ?, ?)", attachment_rows)
            self._db.executemany("UPDATE attachments SET metadata = ? WHERE id = ?", attachment_updates)
            self._db.executemany("DELETE FROM attachments WHERE id = ?", attachment_deletes)
            # Check inside the same transaction so concurrent uploads cannot
            # overrun the quota and rejected edits roll back completely. Allow
            # deletions to reduce an existing database above a newly lowered cap.
            after = self._retained_bytes()
            if after > self.max_storage_bytes and after > before:
                raise StorageLimitError("Chat storage quota reached. Delete old conversations or remove unused uploads, then try again. Saved history was not deleted.")

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._db.close()
                self._lease.close()
