"""Single-owner durable run and inference journal, separate from graph checkpoints."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from .backend_diagnostics import safe_backend_diagnostics


def _private_directory(path: Path):
    # Passing a POSIX mode to mkdir on Windows replaces the inherited user ACE
    # with an OWNER RIGHTS ACE. That is private, but it no longer matches the
    # managed runtime's explicit user/SYSTEM/Administrators DACL and forces the
    # lifecycle hardener to rewrite every generated artifact on restart.
    if os.name == "nt":
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)


def encoded(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    )


def digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


STAGES = {"primary", "secondary", "synthesis", "deep", "decider", "plan", "patch", "review",
          "research_queries", "research", "web_search_1", "web_search_2"}

STAGES.update({"build_plan", "build_tests", "implement", "build_frozen", "build_strength"})
for number in range(16):
    for prefix in ("build_tests_syntax", "build_tests_coverage", "build_tests_fixture", "repair", "build_verify", "build_control", "build_strengthen", "build_strength"):
        STAGES.add(f"{prefix}_{number}")


class Store:
    def __init__(self, directory: Path, max_bytes=512000000):
        _private_directory(directory)
        self.directory = directory.resolve()
        self.max_bytes = max_bytes
        self.lock = threading.RLock()
        self.owner = open(self.directory / "owner.lock", "a+b")  # noqa: SIM115 -- lock remains held until close()
        try:
            if os.name == "nt":
                import msvcrt

                self.owner.seek(0)
                if not self.owner.read(1):
                    self.owner.write(b"0")
                    self.owner.flush()
                self.owner.seek(0)
                msvcrt.locking(self.owner.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.owner.close()
            raise ValueError("Agent data directory already has an owner") from None
        self.db = sqlite3.connect(
            self.directory / "runs.sqlite3", check_same_thread=False
        )
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, bundle TEXT NOT NULL, digest TEXT NOT NULL,
                    status TEXT NOT NULL, error TEXT, result TEXT, approved INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS calls (
                    run_id TEXT NOT NULL, stage TEXT NOT NULL, request_id TEXT NOT NULL,
                    role TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL,
                    started REAL NOT NULL, finished REAL, PRIMARY KEY(run_id, stage));
                CREATE TABLE IF NOT EXISTS approvals (
                    run_id TEXT PRIMARY KEY, digest TEXT NOT NULL, approved_at REAL NOT NULL,
                    deadline REAL NOT NULL);
            """)
            # A crash can persist a final response just before the SQLite status update.
            # Reconcile that receipt before reporting interrupted runs to the operator.
            pending = self.db.execute("SELECT run_id, stage FROM calls WHERE status='dispatched'").fetchall()
            for run_id, stage in pending:
                path = self.artifact(run_id, stage, "raw")
                if path.is_file():
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    status = "not_dispatched" if raw.get("dispatch_status") == "not_dispatched" else "terminal"
                    self.db.execute("UPDATE calls SET status=?, finished=? WHERE run_id=? AND stage=?",
                                    (status, path.stat().st_mtime, run_id, stage))
            self.db.execute("UPDATE runs SET status='apply_failed',error='Inspect the isolated workspace' WHERE status='applying'")
            self.db.execute(
                "UPDATE runs SET status='interrupted' WHERE status IN ('running','queued','cancelling')"
            )

    def capacity(self):
        size = sum(p.stat().st_size for p in self.directory.rglob("*") if p.is_file())
        if size + 40000000 > self.max_bytes:
            raise ValueError(
                "Agent storage quota reached; delete completed runs before continuing"
            )

    def create(self, bundle):
        with self.lock, self.db:
            self.capacity()
            if self.db.execute("SELECT count(*) FROM runs").fetchone()[0] >= 1000:
                raise ValueError("Maximum saved run count reached")
            run_id = str(uuid.uuid4())
            now = time.time()
            self.db.execute(
                "INSERT INTO runs(id,bundle,digest,status,created,updated) VALUES(?,?,?,?,?,?)",
                (run_id, encoded(bundle), digest(bundle), "prepared", now, now),
            )
        return run_id

    def get(self, run_id):
        with self.lock:
            row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown run ID")
            result = dict(row)
            result["bundle"] = json.loads(result["bundle"])
            result["result"] = (
                json.loads(result["result"]) if result["result"] else None
            )
            result["calls"] = [
                dict(r)
                for r in self.db.execute(
                    "SELECT * FROM calls WHERE run_id=? ORDER BY started", (run_id,)
                )
            ]
            for call in result["calls"]:
                failure = self.artifact(run_id, call["stage"], "failure")
                if failure.exists():
                    call["backend_diagnostics"] = safe_backend_diagnostics(
                        json.loads(failure.read_text(encoding="utf-8")))
            approval = self.db.execute(
                "SELECT * FROM approvals WHERE run_id=?", (run_id,)
            ).fetchone()
            result["approval"] = dict(approval) if approval else None
            return result

    def approve(self, run_id, digest_value, timeout):
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO approvals VALUES(?,?,?,?)",
                (run_id, digest_value, time.time(), time.time() + timeout),
            )

    def list(self):
        with self.lock:
            return [
                dict(r)
                for r in self.db.execute(
                    "SELECT id,status,error,created,updated FROM runs ORDER BY created DESC LIMIT 100"
                )
            ]

    def update(self, run_id, status, *, error=None, result=None, approved=None):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE runs SET status=?,error=?,result=coalesce(?,result),updated=? WHERE id=?",
                (
                    status,
                    error,
                    encoded(result) if result is not None else None,
                    time.time(),
                    run_id,
                ),
            )
            if approved is not None:
                self.db.execute(
                    "UPDATE runs SET approved=? WHERE id=?", (int(approved), run_id)
                )

    def artifact(self, run_id, stage, suffix):
        # All components are generated by the runtime, never accepted from models.
        uuid.UUID(run_id)
        if stage not in STAGES or suffix not in {"request", "raw", "failure", "transport"}:
            raise ValueError("Invalid artifact identity")
        folder = self.directory / "artifacts" / run_id
        _private_directory(folder)
        return folder / f"{stage}.{suffix}.json"

    def save_artifact(self, run_id, stage, suffix, value):
        target = self.artifact(run_id, stage, suffix)
        temporary = target.with_suffix(".tmp")
        data = encoded(value).encode("utf-8")
        if len(data) > 33000000:
            raise ValueError("Artifact exceeds size bound")
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)

    def begin_call(self, run_id, stage, role, model, request):
        with self.lock, self.db:
            self.capacity()
            self.save_artifact(run_id, stage, "request", request)
            request_id = str(uuid.uuid4())
            self.db.execute(
                "INSERT INTO calls VALUES(?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    stage,
                    request_id,
                    role,
                    model,
                    "dispatched",
                    time.time(),
                    None,
                ),
            )
            return request_id

    def finish_call(self, run_id, stage, raw):
        with self.lock, self.db:
            self.save_artifact(run_id, stage, "raw", raw)
            status = (
                "not_dispatched"
                if isinstance(raw, dict)
                and raw.get("dispatch_status") == "not_dispatched"
                else "terminal"
            )
            self.db.execute(
                "UPDATE calls SET status=?,finished=? WHERE run_id=? AND stage=?",
                (status, time.time(), run_id, stage),
            )

    def record_call_failure(self, run_id, stage, diagnostics):
        """Diagnostic evidence is not a terminal raw response or retry permit."""
        safe = safe_backend_diagnostics(diagnostics)
        if safe:
            with self.lock:
                self.save_artifact(run_id, stage, "failure", safe)

    def cached(self, run_id, stage, *, request=None, role=None, model=None):
        with self.lock:
            row = self.db.execute(
                "SELECT status,role,model FROM calls WHERE run_id=? AND stage=?", (run_id, stage)
            ).fetchone()
            if row is None:
                return None
            path = self.artifact(run_id, stage, "raw")
            if path.exists():
                if request is not None:
                    saved = self.artifact(run_id, stage, "request")
                    if (row["role"] != role or row["model"] != model or not saved.is_file()
                            or digest(json.loads(saved.read_text(encoding="utf-8"))) != digest(request)):
                        raise ValueError("Saved model response belongs to different inputs")
                return json.loads(path.read_text(encoding="utf-8"))
            raise RuntimeError("native_completion_unknown")

    def has_unknown(self, run_id):
        return any(
            not self.artifact(run_id, c["stage"], "raw").exists()
            for c in self.get(run_id)["calls"]
        )

    def has_unknown_calls(self):
        """Unresolved dispatches quarantine physical inference across runs/restarts."""
        with self.lock:
            rows = self.db.execute("SELECT run_id,stage FROM calls WHERE status='dispatched'").fetchall()
            return any(not self.artifact(row["run_id"], row["stage"], "raw").exists() for row in rows)

    def delete(self, run_id):
        import shutil

        with self.lock, self.db:
            self.db.execute("DELETE FROM calls WHERE run_id=?", (run_id,))
            self.db.execute("DELETE FROM approvals WHERE run_id=?", (run_id,))
            self.db.execute("DELETE FROM runs WHERE id=?", (run_id,))
            shutil.rmtree(self.directory / "artifacts" / run_id, ignore_errors=True)
            shutil.rmtree(self.directory / "build-workspaces" / run_id, ignore_errors=True)

    def close(self):
        self.db.close()
        self.owner.close()
