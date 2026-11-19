"""Validate smoke tests against the pinned reference before candidate repair."""

import ast
import hashlib
import re

from .build_coverage import parse_test_files, python_test_definitions
from .build_workflow import actionable_failure, public_error, validate_baseline
from .project_build import (ProjectTestSuite, execution_settled, matching_image_checks,
                            sandbox_stage_results, validate_reference_test_replacement, ERROR_HEADLINE)
from .project_fixture_checks import _bypasses
from .store import digest
from .whole_project_sandbox import validate_self_suite
from .whole_project_smoke import INLINE_SMOKE_POLICY


POLICY = "reference-validated-smoke-v1"
MAX_REPAIRS = 2
_TEST = re.compile(r"^(\w+) \(([\w.]+)\)")
_FAILURE_BLOCK = re.compile(
    r"(?ms)^={20,}\n(FAIL|ERROR): ([^\n]+)\n"
    r"(?:(?![-=]{20,}\n)[^\n]*\n)*-{20,}\n"
    r"(.*?)(?=^={20,}\n|^-{20,}\nRan \d+ tests?|\Z)"
)
_FAILED_SUMMARY = re.compile(r"(?m)^-{20,}\nRan (\d+) tests? in [^\n]+\n\nFAILED \(([^\n]+)\)\n*\Z")


def _complete_test_log_failure(summary, receipt):
    """Recover multiline unittest descriptions only from the full hashed test log."""
    records = [item for item in summary.get("stage_receipts", []) if item.get("stage") == "python-unittest"]
    if len(records) != 1:
        return None
    record = records[0]
    if (record.get("log_complete") is not True or record.get("stream_relay_complete") is not True
            or record.get("exit_code") != 1):
        return None
    for row in (receipt or {}).get("commands", []):
        if row.get("stdout") != "" or sandbox_stage_results(row) != summary:
            continue
        log, marker, _tail = row.get("stderr", "").rpartition("OFFLINE_PROJECT_RESULT ")
        encoded = log.encode("utf-8")
        if (not marker or len(encoded) > 32768 or len(encoded) != record.get("log_bytes")
                or hashlib.sha256(encoded).hexdigest() != record.get("log_sha256")):
            continue
        log = log.replace("\r\n", "\n")
        terminal = _FAILED_SUMMARY.search(log)
        blocks = _FAILURE_BLOCK.findall(log)
        if terminal is None or not blocks or len(blocks) > 8:
            return None
        counts = {}
        for term in terminal[2].split(", "):
            key, equals, value = term.partition("=")
            if equals != "=" or key not in {"failures", "errors"} or not value.isdecimal() or key in counts:
                return None
            counts[key] = int(value)
        if (sum(kind == "FAIL" for kind, _name, _body in blocks) != counts.get("failures", 0)
                or sum(kind == "ERROR" for kind, _name, _body in blocks) != counts.get("errors", 0)):
            return None
        rows = []
        for kind, name, body in blocks:
            headlines = ERROR_HEADLINE.findall(body)
            if not headlines:
                return None
            rows.append({"test": name, "status": "failed" if kind == "FAIL" else "error",
                         "assertion": headlines[-1].strip()[:1200]})
        return {"test_failures": rows, "test_failure_count": len(rows), "omitted_test_failures": 0,
                "test_failures_complete": True, "tests_run": int(terminal[1])}
    return None


def reference_smoke_diagnostics(suite, summaries, *, receipt=None):
    """Expose only complete test-side failures; never reference app source/logs."""
    definitions = {path: python_test_definitions(tree) for path, tree in parse_test_files(
        {file.path: file.content for file in suite.files}).items()}
    diagnostics = []
    for summary in summaries:
        for failure in summary.get("failures", []):
            if failure.get("stage") != "python-unittest" or failure.get("status") != "failed":
                continue
            if "test_failures" not in failure:
                recovered = _complete_test_log_failure(summary, receipt)
                if recovered is None:
                    return []
                failure = {**failure, **recovered}
            failures = failure.get("test_failures")
            if (failure.get("test_failures_complete") is not True or failure.get("omitted_test_failures") != 0
                    or not isinstance(failures, list) or not failures or len(failures) > 8
                    or failure.get("test_failure_count") != len(failures)):
                return []
            for row in failures:
                match = _TEST.match(row.get("test", ""))
                if match is None or row.get("status") not in {"failed", "error"}:
                    return []
                method, owner = match.groups()
                candidates = []
                for path, names in definitions.items():
                    module = path.rsplit("/", 1)[-1][:-3]
                    for name in names:
                        cls, _, leaf = name.rpartition(".")
                        owners = {module + ("." + cls if cls else ""), name,
                                  module + "." + name}
                        if method == leaf and owner in owners:
                            candidates.append((path, name))
                        elif method == "setUpClass" and cls and owner == module + "." + cls:
                            candidates.append((path, name))
                        elif method == "setUpModule" and owner == module:
                            candidates.append((path, name))
                if not candidates or (method.startswith("test") and len(candidates) != 1):
                    return []
                paths = {path for path, _name in candidates}
                if len(paths) != 1:
                    return []
                path = next(iter(paths))
                diagnostics.append({"path": path, "names": sorted(name for _path, name in candidates),
                    "test": row["test"], "status": row["status"], "assertion": str(row.get("assertion", ""))[:1200]})
    return diagnostics


def validate_smoke_reference_correction(before, after, diagnostics):
    """Use the independent reference-repair boundary for named smoke failures."""
    if before.model_dump(exclude={"files"}) != after.model_dump(exclude={"files"}):
        raise ValueError("Smoke reference correction changed the manifest or coverage")
    prior = parse_test_files({file.path: file.content for file in before.files})
    revised = parse_test_files({file.path: file.content for file in after.files})
    if prior.keys() != revised.keys():
        raise ValueError("Smoke reference correction changed test files")
    changed = False
    prior_files, revised_files = ({file.path: file for file in suite.files} for suite in (before, after))
    for path, tree in prior.items():
        edited = revised[path]
        original_tests, edited_tests = python_test_definitions(tree), python_test_definitions(edited)
        if original_tests.keys() != edited_tests.keys():
            raise ValueError("Smoke reference correction changed test class or method identities")
        named = {name for finding in diagnostics if finding["path"] == path for name in finding["names"]}
        if _bypasses(edited) - _bypasses(tree):
            raise ValueError("Smoke reference correction introduced a skip or expected-failure bypass")
        if ast.dump(tree) != ast.dump(edited):
            if not named:
                raise ValueError("Smoke reference correction changed a file without a diagnosed failure")
            validate_reference_test_replacement(prior_files[path], revised_files[path], named,
                                                ProjectTestSuite.manifest_type.byte_limit)
            changed = True
    if not changed:
        raise ValueError("Smoke reference correction did not change the failed fixture or request")
    return after


def validate_smoke_reference(workflow, candidate, suite, *, allow_repair=True):
    """Return a reference-passing suite, or stop with review_required evidence."""
    if not workflow.scoped_repairs:
        return suite
    if workflow.reference is None:
        workflow.evidence["self_reference_validation"] = {"policy": POLICY, "status": "not_supplied"}
        return suite
    workflow.evidence.pop("self_reference_validation", None)
    checks = workflow.evidence.setdefault("self_reference_checks", [])
    while True:
        suite_digest = digest(suite.model_dump())
        workflow.evidence.update(self_suite=suite.model_dump(), self_suite_digest=suite_digest)
        result = workflow.control(suite)
        summaries = [summary for row in result.get("commands", [])
                     if (summary := sandbox_stage_results(row)) is not None]
        sandbox = result.get("sandbox", {})
        record = {"policy": POLICY, "stage": workflow.stage, "reference_digest": workflow.reference["digest"],
                  "suite_digest": suite_digest, "receipt_digest": digest(result), "status": "review_required"}
        checks.append(record)
        workflow.stages[workflow.stage] = {**record, "purpose": "smoke-reference"}
        trusted = (sandbox.get("image_id") == workflow.bundle["sandbox"]["image_id"]
                   and sandbox.get("network") == "disabled" and sandbox.get("read_only_project") is True
                   and sandbox.get("read_only_root") is True and execution_settled(result)
                   and matching_image_checks(result, summaries, (workflow.environment or {}).get("verification")))
        if trusted and validate_baseline(result, suite):
            record["status"] = "passed"
            workflow.stages[workflow.stage]["status"] = "passed"
            workflow.evidence["self_reference_validation"] = dict(record)
            return suite
        diagnostics = (reference_smoke_diagnostics(suite, summaries, receipt=result)
                       if trusted and actionable_failure(result) else [])
        record["status"] = "failed" if diagnostics else "review_required"
        workflow.stages[workflow.stage]["status"] = record["status"]
        attempts = workflow.counters["whole_self_reference_repair"]
        if not diagnostics or not allow_repair or attempts >= MAX_REPAIRS:
            workflow.output["error"] = {"stage": workflow.stage, "message": (
                "Smoke tests did not pass the pinned reference; no candidate repair is authorized"
                + (" (smoke reference correction budget exhausted)" if attempts >= MAX_REPAIRS else ""))}
            return None
        workflow.counters["whole_self_reference_repair"] += 1
        workflow.evidence["budget_counters"] = dict(workflow.counters)
        stage = f"whole_self_reference_repair_{attempts + 1}"
        raw = workflow._generate(stage, "implementer", ProjectTestSuite,
            "Correct the supplied smoke suite's named failures against a hidden trusted implementation of the "
            "public contract. The reference source and logs are not available. Repair request authentication, "
            "fixtures or shared setup using only the public brief and test-side failures. Preserve every test "
            "file/class/method identity, coverage entry, command and assumption. For the named failing methods only, "
            "correct invalid expectations using the public brief; preserve every covered behavior with meaningful "
            "discriminating assertions. Never skip a case, add an expected-failure bypass, make a required action "
            "optional or replace a check with a placeholder. For concurrent operations, intermediate responses can "
            "represent different instants: check guaranteed response properties and assert the final persisted state "
            "after all operations complete, rather than requiring every intermediate response to equal the final state. "
            "Keep unaffected test methods unchanged. Change only named failing methods' request/setup code and "
            "invalid expectations, and the helpers they depend on. Return the complete corrected suite with implementation_files copied "
            "exactly. No candidate source or tools are available. The full suite will run against the reference again.",
            {"implementation_files": suite.implementation_files,
             "previous_suite": suite.model_dump(exclude={"implementation_files"}),
             "reference_test_failure": {"policy": POLICY, "diagnostics": diagnostics}},
            {}, tool_policy=INLINE_SMOKE_POLICY)
        try:
            revised = validate_self_suite(workflow, raw, candidate)
            validate_smoke_reference_correction(suite, revised, diagnostics)
            defects = workflow.test_defects(revised)
            if defects:
                raise ValueError("Smoke reference correction contains an invalid test: " + defects[0]["message"])
        except ValueError as error:
            workflow.stages[stage]["replacement_rejected"] = public_error(error)
            workflow.output["error"] = {"stage": stage, "message": public_error(error)}
            return None
        suite = revised
