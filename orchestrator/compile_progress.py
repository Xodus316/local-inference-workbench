"""Bound recovery for compiler errors exposed only after earlier failures clear."""
import hashlib
import re

from .store import digest
from .whole_project_selection import compiler_diagnostic_selection, _LOCATION


KEY = "whole_project_compile_progress_policy"
LEGACY_POLICY = {"policy": "unchanged-source-compiler-frontier-v1", "max_additional_repairs": 2}
POLICY = {"policy": "unmasked-compiler-frontier-v2", "max_additional_repairs": 2}


def additional_repairs(bundle):
    value = bundle.get(KEY)
    if value is None:
        return 0
    if (value not in (POLICY, LEGACY_POLICY) or type(value.get("max_additional_repairs")) is not int
            or bundle.get("whole_project_repair_policy") != "verified-scoped-repair-v1"
            or bundle.get("whole_project_workflow") != "opencode-whole-project-v1"):
        raise ValueError("Invalid approved compiler progress recovery policy")
    return value["max_additional_repairs"]


def _diagnostic_keys(summary, selected):
    locations = {(m['compiler_stage'], m['reported_path'], m['line'], m['column']): m['path']
                 for m in selected['matches']}
    result = set()
    for failure in summary['failures']:
        text = failure.get('diagnostic_excerpt') or failure.get('message', '')
        for location in _LOCATION.finditer(text):
            line = int(location['line'] or location['ts_line'])
            column = int(location['column'] or location['ts_column'] or 0) or None
            path = locations.get((failure['stage'], location['path'], line, column))
            if path is None:
                continue
            # Line/column shifts alone must not make an old issue look new.
            message = re.sub(r'\s+', ' ', text[location.end():].split('\n', 1)[0].strip(': '))
            if not message:
                continue  # Vite also emits bare `file: /path:line:column` references.
            result.add((failure['stage'], path, digest(message)))
    return result if {item[1] for item in result} == set(selected['paths']) else None


def progress_proof(records, files, checkpoint, policy, image_id, *, recovery_policy=None):
    """Require settled progress and a new diagnostic frontier in unchanged files.

    The legacy policy requires never-failed files. V2 also allows files repaired
    before an intervening blocker, provided the latest repair left them unchanged
    and their diagnostics have never been reported. Neither grants passing credit.
    """
    from .project_build import compile_checkpoint_status, failure_feedback
    if len(records) < 2:
        return None
    current_hashes = {f.path: hashlib.sha256(f.content.encode("utf-8")).hexdigest() for f in files}
    frontiers, hashes, checks, selections, observed = [], [], [], [], []
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
        passing = {name for name, result in summary["checks"].items() if result.get("status") == "passed"}
        if hashes and source == hashes[-1]:
            # Resume re-verifies the saved attempt before dispatch. Retain both
            # receipts, but do not mistake that identical checkpoint for a repair.
            if (record['stage'] != observed[-1]['stage'] or passing != checks[-1]
                    or _diagnostic_keys(summary, selected) is None
                    or _diagnostic_keys(summary, selected) != _diagnostic_keys(observed[-1]['stage_results'], selections[-1])):
                return None
            observed[-1], selections[-1] = record, selected
            continue
        observed.append(record)
        frontiers.append(set(selected["paths"]))
        selections.append(selected)
        hashes.append(source)
        checks.append(passing)
    if len(hashes) < 2:
        return None
    previous_paths = set().union(*frontiers[:-1])
    newly_exposed = frontiers[-1]
    if (not newly_exposed or hashes[-1] != current_hashes or hashes[-1] == hashes[-2]
            or not set().union(*checks[:-1]) <= checks[-1]):
        return None
    if (not newly_exposed & previous_paths
            and all(source[path] == current_hashes[path] for source in hashes for path in newly_exposed)):
        return {"policy": LEGACY_POLICY["policy"], "previous_failed_paths": sorted(previous_paths),
                "newly_exposed_paths": sorted(newly_exposed),
                "unchanged_source_sha256": {p: current_hashes[p] for p in sorted(newly_exposed)},
                "cleared_previous_failures": True,
                "receipt_digests": [digest(r["execution"]) for r in records]}
    if recovery_policy != POLICY:
        return None
    # A syntax/configuration blocker can hide errors in files repaired earlier.
    # Require a different latest frontier whose files were untouched by the
    # immediately preceding repair, plus globally new diagnostics and no lost passes.
    if (newly_exposed & frontiers[-2]
            or any(hashes[-2][path] != current_hashes[path] for path in newly_exposed)):
        return None
    diagnostics = [_diagnostic_keys(record['stage_results'], selected)
                   for record, selected in zip(observed, selections)]
    if any(value is None for value in diagnostics) or diagnostics[-1] & set().union(*diagnostics[:-1]):
        return None
    return {'policy': POLICY['policy'], 'previous_failed_paths': sorted(frontiers[-2]),
            'newly_exposed_paths': sorted(newly_exposed),
            'unchanged_since_previous_checkpoint_sha256': {p: current_hashes[p] for p in sorted(newly_exposed)},
            'diagnostic_frontier_digest': digest(sorted(diagnostics[-1])),
            'passing_checks_preserved': sorted(set().union(*checks[:-1])),
            'previous_frontier_no_longer_reported': True, 'acceptance_credit': False,
            'receipt_digests': [digest(record['execution']) for record in records]}
