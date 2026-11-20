"""Single-owner durable run and inference journal, separate from graph checkpoints."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from .backend_diagnostics import safe_backend_diagnostics


MAX_ARTIFACT_BYTES = 33_000_000


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

STAGES.update({"build_plan", "build_plan_retry", "build_tests", "implement", "implement_inventory_retry",
               "build_frozen", "build_strength"})
STAGES.update({"build_project_plan", "build_project_frozen", "build_project_acceptance"})
STAGES.update({"build_project_plan_repair_1", "build_project_plan_repair_2"})
STAGES.update({"build_tests_restart", "build_tests_retry", "whole_self_tests", "whole_self_tests_retry",
               "whole_self_reference_repair_1", "whole_self_reference_repair_2", "whole_self_verify_0", "whole_self_verify_1",
               "whole_self_verify_final", "whole_self_repair", "whole_self_repair_select", "whole_self_repair_edit_retry"})
STAGES.update(f"whole_self_verify_final_{number}" for number in range(1, 9))
STAGES.update(f"whole_regression_verify_{number}" for number in range(9))
STAGES.update(f"whole_regression_early_{number}" for number in range(9))
STAGES.update(f"whole_self_candidate_{number}" for number in range(4))
for number in (2, 3):
    STAGES.update({f"whole_self_repair_{number}", f"whole_self_repair_{number}_select",
                   f"whole_self_repair_{number}_edit_retry", f"whole_self_verify_{number}"})
# One frozen-suite checkpoint plus each bounded verification promotion. Leave
# room for a final self-check cycle while retaining a finite artifact namespace.
STAGES.update(f"whole_checkpoint_{number}" for number in range(1, 17))
for number in range(1, 33):
    STAGES.update({f"implement_group_{number}", f"implement_group_{number}_scope_retry", f"project_after_{number}"})
for number in range(1, 9):
    STAGES.update({f"repair_{number}_select", f"repair_{number}_edit_retry"})
for number in range(16):
    for prefix in ("build_tests_syntax", "build_tests_coverage", "build_tests_fixture", "repair", "build_verify", "build_control", "build_strengthen", "build_strength"):
        STAGES.add(f"{prefix}_{number}")
    STAGES.add(f"build_test_preflight_{number}")
for number in range(16):
    STAGES.add(f"build_code_review_{number}")
    STAGES.update(f"build_code_review_{number}_chunk_{chunk}" for chunk in range(1, 17))
    STAGES.update(f"build_code_review_{number}_chunk_{chunk}_citation_retry" for chunk in range(1, 17))
for slot in range(1, 5):
    STAGES.update(f"build_compile_checkpoint_{slot}_{attempt}" for attempt in range(5))
    for attempt in range(1, 5):
        stage = f"build_compile_repair_{slot}_{attempt}"
        STAGES.update({stage, stage + "_select", stage + "_edit_retry"})
    for repair in range(9):
        STAGES.update(f"build_repair_compile_checkpoint_{repair}_{slot}_{attempt}" for attempt in range(5))
        for attempt in range(1, 5):
            stage = f"build_repair_compile_repair_{repair}_{slot}_{attempt}"
            STAGES.update({stage, stage + "_select", stage + "_edit_retry"})
    for repair in range(1, 4):
        STAGES.update(f"build_self_compile_checkpoint_{repair}_{slot}_{attempt}" for attempt in range(5))
        for attempt in range(1, 5):
            stage = f"build_self_compile_repair_{repair}_{slot}_{attempt}"
            STAGES.update({stage, stage + "_select", stage + "_edit_retry"})
# The native selection policy requires every selection stage to end in _select.
STAGES.update(stage.removesuffix("_select") + "_retry_select" for stage in tuple(STAGES)
              if stage.endswith("_select") and stage.startswith(("repair_", "whole_self_repair",
                  "build_compile_repair_", "build_repair_compile_repair_", "build_self_compile_repair_")))
# Generated-test defect rewrites, and lower-tier repairs escalated after reaching their output cap.
STAGES.update(f"build_tests_defect_{number}" for number in range(1, 4))
STAGES.update(f"build_tests_defect_{number}_file_{file}" for number in range(1, 4) for file in range(1, 16))
STAGES.update(f"build_tests_diagnose_{number}_file_{file}" for number in range(1, 4) for file in range(1, 16))
STAGES.update([stage + "_escalated" for stage in list(STAGES)
               if stage.startswith("repair_") or "compile_repair_" in stage])


# Evidence sidecars are finite, literal companions of approved review stages.
STAGES.update(f'whole_recovery_{index}_select' for index in range(1, 17))
STAGES.update(f'whole_recovery_request_{index}' for index in range(1, 65))
STAGES.update(stage + suffix for stage in list(STAGES)
              if stage.startswith('build_code_review_') and '_chunk_' in stage
              for suffix in ('_source_anchors', '_expanded_review'))
for profile in range(8):
    for attempt in range(9):
        stage = f'whole_profile_{profile}_{attempt}'
        STAGES.update(stage + suffix for suffix in (
            '', '_reference_ordinary', '_reference_profile', '_candidate'))


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
                    deadline REAL);
            """)
            columns = self.db.execute("PRAGMA table_info(approvals)").fetchall()
            if any(column[1] == "deadline" and column[3] for column in columns):
                # SQLite cannot remove NOT NULL in place. Migrate atomically,
                # preserving every existing approval and its original deadline.
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute("CREATE TABLE approvals_nullable (run_id TEXT PRIMARY KEY, "
                                "digest TEXT NOT NULL, approved_at REAL NOT NULL, deadline REAL)")
                self.db.execute("INSERT INTO approvals_nullable SELECT * FROM approvals")
                self.db.execute("DROP TABLE approvals")
                self.db.execute("ALTER TABLE approvals_nullable RENAME TO approvals")
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
            if timeout is None:
                row = self.get(run_id)
                if (row["digest"] != digest_value or "run_timeout_seconds" not in row["bundle"]
                        or row["bundle"]["run_timeout_seconds"] is not None):
                    raise ValueError("No overall deadline requires an explicitly approved unlimited scope")
            elif type(timeout) not in (int, float) or not math.isfinite(timeout):
                raise ValueError("Approval timeout must be finite, or explicitly null")
            now = time.time()
            self.db.execute(
                "INSERT OR IGNORE INTO approvals VALUES(?,?,?,?)",
                (run_id, digest_value, now, None if timeout is None else now + timeout),
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

    def snapshot_result(self, run_id, result):
        """Preserve workflow evidence without settling inference or changing status."""
        with self.lock, self.db:
            cursor = self.db.execute(
                "UPDATE runs SET result=?,updated=? WHERE id=?",
                (encoded(result), time.time(), run_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Cannot snapshot an unknown run")

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
        if len(data) > MAX_ARTIFACT_BYTES:
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
