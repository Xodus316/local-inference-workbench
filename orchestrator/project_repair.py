"""Retain the last observed failures from verified image checks."""

import re

from .store import digest


def _excerpt(value, limit=1800):
    return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _failed_cases(rows):
    if not isinstance(rows, list) or len(rows) > 12:
        return []
    failed, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            return []
        identifier, summary = row.get("id"), row.get("summary")
        diagnostics = row.get("diagnostics", [])
        paths = row.get("source_paths", [])
        guidance = row.get("repair_guidance", "")
        if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", identifier)
                or identifier in seen or row.get("status") not in ("failed", "passed")
                or not isinstance(summary, str) or not summary.strip() or not isinstance(diagnostics, list)
                or len(diagnostics) > 8 or any(not isinstance(item, str) for item in diagnostics)
                or not isinstance(paths, list) or len(paths) > 6
                or any(not isinstance(path, str) or not 0 < len(path) <= 240 for path in paths)
                or not isinstance(guidance, str)):
            return []
        seen.add(identifier)
        if row["status"] == "failed":
            detail = {"id": identifier, "summary": _excerpt(summary, 400),
                      "diagnostics": [_excerpt(item, 300) for item in diagnostics]}
            if paths:
                detail["source_paths"] = paths
            if guidance.strip():
                detail["repair_guidance"] = _excerpt(guidance, 1200)
            if row.get("control_match") is True:
                detail["control_match"] = True
            failed.append(detail)
    return failed


class VerificationFailures:
    """Distinguish current repair targets from failures awaiting recheck."""

    def __init__(self, checks):
        self.checks = checks
        self.open = {}

    def observe(self, receipt, summary, stage, cases):
        current = []
        previous_observations = {}
        receipt_digest = digest(receipt)
        failures = summary.get("failures")
        if not isinstance(failures, list):
            failures = []
        for check in self.checks:
            observed = summary["checks"].get(check)
            status = observed.get("status") if isinstance(observed, dict) else None
            if status == "passed":
                self.open = {key: issue for key, issue in self.open.items() if key[0] != check}
            if status != "failed":
                continue
            details = _failed_cases(cases.get(check))
            if details:
                self.open.pop((check, None), None)
            else:
                notes = [row.get("diagnostic_excerpt") or row.get("message") for row in failures
                         if isinstance(row, dict) and row.get("stage") == check]
                details = [{"diagnostics": [_excerpt(note) for note in notes if isinstance(note, str)][:2]}]
            for detail in details:
                key = (check, detail.get("id"))
                previous = self.open.get(key)
                if previous and previous["diagnostics"] != detail["diagnostics"]:
                    previous_observations[key] = {
                        "verification_stage": previous["verification_stage"],
                        "receipt_digest": previous["receipt_digest"],
                        "diagnostics": previous["diagnostics"][:2],
                    }
                self.open[key] = {**detail, "check": check, "verification_stage": stage,
                                  "receipt_digest": receipt_digest}
                current.append(key)
        current_set = set(current)
        for key, issue in self.open.items():
            issue["status"] = "failed" if key in current_set else "awaiting_recheck"
        if not current:
            return None
        target = current[0]
        focus = {"repair_target": self.open[target].copy(), "pending_verification_failures": [
            {**{name: value for name, value in issue.items()
                if name not in {"control_match", "repair_guidance", "source_paths"}},
             "diagnostics": [_excerpt(note, 450) for note in issue["diagnostics"][:1]]}
            for key, issue in self.open.items() if key != target
        ]}
        if target in previous_observations:
            focus["previous_target_observation"] = previous_observations[target]
        return focus
