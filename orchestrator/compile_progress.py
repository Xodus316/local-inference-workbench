"""Bound recovery for compiler errors exposed only after earlier failures clear."""
import hashlib

from .store import digest
from .whole_project_selection import compiler_diagnostic_selection


KEY = "whole_project_compile_progress_policy"
POLICY = {"policy": "unchanged-source-compiler-frontier-v1", "max_additional_repairs": 2}


def additional_repairs(bundle):
    value = bundle.get(KEY)
    if value is None:
        return 0
    if (value != POLICY or type(value.get("max_additional_repairs")) is not int
            or bundle.get("whole_project_repair_policy") != "verified-scoped-repair-v1"
            or bundle.get("whole_project_workflow") != "opencode-whole-project-v1"):
        raise ValueError("Invalid approved compiler progress recovery policy")
    return value["max_additional_repairs"]


def progress_proof(records, files, checkpoint, policy, image_id):
    """Require settled progress and a new diagnostic frontier in unchanged files.

    This deliberately declines incomplete locations, recurring failed files,
    regressed checks and errors in edited files. It never predicts a compiler
    pass or grants acceptance credit; the next identical checkpoint is required.
    """
    from .project_build import compile_checkpoint_status, failure_feedback
    if len(records) < 2:
        return None
    current_hashes = {f.path: hashlib.sha256(f.content.encode("utf-8")).hexdigest() for f in files}
    frontiers, hashes, checks = [], [], []
    for record in records:
        receipt, summary = record["execution"], record["stage_results"]
        rows = receipt.get("commands") or []
        if len(rows) != 1:
            return None
        row, sandbox = rows[0], receipt.get("sandbox") or {}
        source = row.get("source_sha256")
        if (record["status"] != "failed" or not isinstance(source, dict) or set(source) != set(current_hashes)
                or row.get("source_unchanged") is not True or sandbox.get("image_id") != image_id
                or sandbox.get("network") != "disabled" or sandbox.get("read_only_root") is not True
                or sandbox.get("read_only_project") is not True
                or compile_checkpoint_status(receipt, summary, checkpoint, policy,
                                             source_hashes=source, image_id=image_id) != "failed"):
            return None
        # No byte or file truncation: every located failure must fit the current
        # component. Selection remains bounded separately at actual dispatch.
        selected = compiler_diagnostic_selection(files, {"failed_execution": failure_feedback(
            receipt, include_stage_results=True)}, byte_limit=sum(len(f.content.encode("utf-8")) for f in files),
            file_limit=len(files))
        failures = summary["failures"]
        if (selected is None or selected["pending_paths"]
                or {f["stage"] for f in failures} != {m["compiler_stage"] for m in selected["matches"]}):
            return None
        frontiers.append(set(selected["paths"]))
        hashes.append(source)
        checks.append({name for name, result in summary["checks"].items() if result.get("status") == "passed"})
    previous_paths = set().union(*frontiers[:-1])
    newly_exposed = frontiers[-1]
    if (not newly_exposed or newly_exposed & previous_paths or hashes[-1] != current_hashes
            or hashes[-1] == hashes[-2] or not set().union(*checks[:-1]) <= checks[-1]
            or any(source[path] != current_hashes[path] for source in hashes for path in newly_exposed)):
        return None
    return {"policy": POLICY["policy"], "previous_failed_paths": sorted(previous_paths),
            "newly_exposed_paths": sorted(newly_exposed),
            "unchanged_source_sha256": {p: current_hashes[p] for p in sorted(newly_exposed)},
            "cleared_previous_failures": True,
            "receipt_digests": [digest(r["execution"]) for r in records]}
