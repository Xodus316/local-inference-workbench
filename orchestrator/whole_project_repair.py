"""Evidence-based promotion for the opt-in whole-project repair policy."""

from .store import digest


POLICY = "verified-scoped-repair-v1"


def public_repair_plan(plan):
    """Keep required behavior and frozen paths without stale implementation advice."""
    projected = {key: value for key, value in plan.items()
                 if key in {"goal", "acceptance_criteria", "assumptions", "implementation_files"}}
    proof = {
        "policy": "whole-public-contract-repair-v1", "plan_digest": digest(plan),
        "omitted_fields": sorted(plan.keys() - projected.keys()),
        "reason": "Implementation narrative is advisory and omitted; use current compiler diagnostics and pinned public API signatures for dependency usage.",
    }
    return projected, proof


def verification_observation(report, required_checks):
    """Only infer individual passes from a complete, finished frozen-suite result."""
    summaries = report.get("sandbox_stage_results", [])
    if len(summaries) != 1:
        return None
    summary = summaries[0]
    checks = summary.get("checks", {})
    passed = sorted(name for name in required_checks if checks.get(name, {}).get("status") == "passed")
    tests = None
    for failure in summary.get("failures", []):
        if failure.get("stage") != "python-unittest":
            continue
        rows = failure.get("test_failures")
        count = failure.get("test_failure_count")
        total = failure.get("tests_run")
        if (failure.get("test_failures_complete") is True and failure.get("omitted_test_failures") == 0
                and isinstance(rows, list) and type(count) is int and count == len(rows)
                and type(total) is int and total >= count > 0
                and all(isinstance(row, dict) and isinstance(row.get("test"), str)
                        and row.get("status") in {"failed", "error", "FAIL", "ERROR"} for row in rows)):
            identities = sorted({row["test"] for row in rows})
            if len(identities) == count:
                tests = {"tests_run": total, "failed": identities}
        break
    return {"passed_checks": passed, "tests": tests, "receipt_digest": digest(report)}


def verification_regressions(previous, current):
    """Keep previously passing checks; incomplete diagnostics never imply a test passed."""
    lost = sorted(set(previous["passed_checks"]) - set(current["passed_checks"]))
    newly_failed = []
    before, after = previous.get("tests"), current.get("tests")
    if before is not None and after is not None and before["tests_run"] == after["tests_run"]:
        newly_failed = sorted(set(after["failed"]) - set(before["failed"]))
    return {"lost_passing_checks": lost, "newly_failed_tests": newly_failed} if lost or newly_failed else None


def focused_feedback(feedback):
    """Select one observed test failure while retaining the bounded complete receipt."""
    value = dict(feedback)
    target = dict(value.get("repair_target") or {})
    if not target:
        for command in value.get("failed_execution", {}).get("commands", []):
            failures = (command.get("sandbox_stage_results") or {}).get("failures", [])
            if failures:
                failure = failures[0]
                target = {"check": failure["stage"], "diagnostics": [failure.get("diagnostic_excerpt")
                           or failure.get("message", "Observed execution failure")]}
                value["repair_target"] = target
                break
    if target.get("check") == "python-unittest":
        for command in value.get("failed_execution", {}).get("commands", []):
            summary = command.get("sandbox_stage_results") or {}
            for failure in summary.get("failures", []):
                if failure.get("stage") == "python-unittest" and failure.get("test_failures"):
                    target["test_failure"] = failure["test_failures"][0]
                    target["root_warnings"] = failure.get("root_warnings", [])
                    target["api_context"] = failure.get("api_context", [])
                    value["repair_target"] = target
                    return value
    return value
