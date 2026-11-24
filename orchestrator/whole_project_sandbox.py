"""Validate and execute a whole-project implementer's separate test suite."""

import ast
from collections import Counter
from functools import lru_cache
import re
from typing import Literal

from pydantic import Field

from .build_coverage import (TestSyntaxError, correct_test_references, parse_test_files, python_test_definitions,
                             python_test_names, validate_coverage_references)
from .build_discovery import validate_discovery_suite
from .build_models import BuildTestFile
from .build_workflow import actionable_failure, assemble, validate_baseline, validate_paths
from .project_build import (ProjectTestSuite, WholeProjectTestSuite, execution_settled, failure_feedback,
                            test_syntax_repair_inputs,
                            matching_image_checks, sandbox_stage_results)
from .schemas import Record
from .store import digest
from .unittest_fixture_lint import class_assertion_problems
from .whole_project_limits import WHOLE_PROJECT_TEST_BYTES
from .whole_project_mongo import mongo_increment_target


_MISSING_SPRING_ROUTE = re.compile(
    r"(?:org\.springframework\.web\.servlet\.resource\.)?NoResourceFoundException: "
    r"No static resource (?P<path>/?[A-Za-z0-9._~/-]{1,240})\.")
_SPRING_BASE = re.compile(r'^\s*@RequestMapping\(\s*(?:(?:path|value)\s*=\s*)?"(/[^"\r\n]*)"\s*\)', re.M)
_SPRING_ENDPOINT = re.compile(
    r'^\s*@(Get|Post|Put|Patch|Delete)Mapping\(\s*(?:(?:path|value)\s*=\s*)?"(/[^"\r\n]*)"\s*\)', re.M)


class SelfTestFixtureError(ValueError):
    """A proven fixture defect may be corrected before the smoke suite freezes."""

    def __init__(self, suite, diagnostics):
        self.suite = suite
        self.diagnostics = diagnostics
        super().__init__(diagnostics[0]["message"])


class SelfTestSyntaxError(TestSyntaxError):
    """Retain a structurally valid suite when its Python source cannot compile."""

    def __init__(self, suite, diagnostics):
        self.suite = suite
        super().__init__(diagnostics)


def smoke_syntax_repair_inputs(suite, diagnostics):
    inputs = test_syntax_repair_inputs(suite, diagnostics)
    if inputs is None or not inputs['snippets']:
        raise ValueError('Smoke syntax correction requires located diagnostics in retained test source')
    paths = {item['path'] for item in inputs['snippets']}
    # A parser reports only its first error per file. Related uses can occur
    # elsewhere (for example a malformed fixture identifier used by tests).
    # Keep the complete diagnosed files, within the already validated suite
    # byte limit, while retaining bounded exact-edit output and file scope.
    inputs['snippets'] = [{'path':file.path, 'start_line':1, 'start_column':1, 'source':file.content}
                          for file in suite.files if file.path in paths]
    return {**inputs, 'policy':'smoke-syntax-source-only-v1',
            'retained_suite_digest':digest(suite.model_dump()),
            'retained_manifest_digest':digest(suite.model_dump(exclude={'files'}))}


def _split_self_test_references(suite):
    paths = {entry.test_file for entry in suite.coverage if "/" in entry.test_name}
    if not paths:
        return suite
    trees = parse_test_files({file.path: file.content for file in suite.files if file.path in paths})
    methods = {
        path: Counter(f"{cls.name}.{method.name}"
                      for cls in tree.body if isinstance(cls, ast.ClassDef)
                      for method in cls.body if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and method.name.startswith("test_"))
        for path, tree in trees.items()
    }
    functions = {path: {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
                 for path, tree in trees.items()}
    value = suite.model_dump()
    coverage = []
    for entry in value["coverage"]:
        reference = entry["test_name"]
        if "/" not in reference:
            coverage.append(entry)
            continue
        names = methods.get(entry["test_file"], {})
        resolved = set()
        for part in reference.split("/"):
            name = part.strip()
            if name.isidentifier():
                matches = [qualified for qualified in names if qualified.rsplit(".", 1)[1] == name]
                if name in functions.get(entry["test_file"], set()):
                    matches = []
            else:
                matches = [name] if name in names else []
            if len(matches) != 1 or names[matches[0]] != 1 or matches[0] in resolved:
                raise ValueError("Slash-delimited self-test coverage must name distinct, unambiguous methods in its file")
            resolved.add(matches[0])
            coverage.append({**entry, "test_name": matches[0]})
    return type(suite).model_validate({**value, "coverage": coverage})


def smoke_test_type(workflow):
    if getattr(workflow, "bundle", {}).get("test_suite_bytes") == WHOLE_PROJECT_TEST_BYTES:
        return WholeProjectTestSuite
    return ProjectTestSuite


@lru_cache(maxsize=32)
def smoke_source_patch_type(paths):
    if not paths:
        raise ValueError("A smoke correction requires at least one diagnosed test file")

    class ScopedSmokeFile(BuildTestFile):
        path: Literal[paths] = Field(json_schema_extra={"enum": list(paths)})

    class SmokeSourcePatch(Record):
        files: list[ScopedSmokeFile] = Field(min_length=1, max_length=len(paths))

    return SmokeSourcePatch


def compose_smoke_source_patch(suite, patch_type, raw):
    """Replace authorized test source on the retained suite; metadata never comes from the repair."""
    edits = patch_type.model_validate(raw).files
    replacements = {file.path: file.content for file in edits}
    if len(replacements) != len(edits) or replacements.keys() - {file.path for file in suite.files}:
        raise ValueError("Smoke correction paths must be distinct files in the retained suite")
    value = suite.model_dump()
    for file in value["files"]:
        if file["path"] in replacements:
            file["content"] = replacements[file["path"]]
    return value


def validate_self_suite(workflow, raw, candidate):
    """Check a self-authored suite without scheduling the independent test author."""
    suite = smoke_test_type(workflow).model_validate(raw)
    if set(suite.implementation_files) != {item.path for item in candidate.files}:
        raise ValueError("Self-tests must declare the exact whole-project implementation paths")
    validate_paths(suite, workflow.artifact_type, workflow.project_type)
    try:
        return _validate_self_suite(workflow, suite)
    except TestSyntaxError as error:
        if not getattr(workflow, 'scoped_repairs', False):
            raise
        raise SelfTestSyntaxError(suite, error.diagnostics) from error


def _validate_self_suite(workflow, suite):
    suite = _split_self_test_references(suite)
    suite, _ = correct_test_references(suite, qualify_classes=True)
    criterion_count = len(workflow.output["plan"]["acceptance_criteria"])
    if any(entry.criterion > criterion_count for entry in suite.coverage):
        raise ValueError("Self-tests must reference criteria from the approved plan")
    validate_coverage_references(suite)
    validate_discovery_suite(suite)
    if getattr(workflow, "scoped_repairs", False):
        diagnostics = [problem for path, tree in parse_test_files(
            {file.path: file.content for file in suite.files}).items()
            for problem in class_assertion_problems(path, tree)]
        if diagnostics:
            raise SelfTestFixtureError(suite, diagnostics)
    workflow.validate_suite(suite)
    problems = workflow.fixture_problems(suite)
    if problems:
        raise ValueError(f"Self-test fixture failed validation: {problems[0]['message']}")
    return suite


def zero_test_fixture_failure(summaries):
    """Identify a zero-test fixture error that needs independent application evidence."""
    for summary in summaries:
        for failure in summary.get("failures", []):
            if (failure.get("stage") != "python-unittest" or failure.get("status") != "failed"
                    or type(failure.get("tests_run")) is not int or failure["tests_run"] != 0
                    or failure.get("test_failures_complete") is not True
                    or failure.get("omitted_test_failures") != 0):
                continue
            errors = [item for item in failure.get("test_failures", []) if item.get("status") == "error"
                      and (item.get("test", "").startswith(("setUpClass (", "setUpModule ("))
                           or "unittest.loader._FailedTest" in item.get("test", ""))]
            if errors:
                return {"policy": "zero-test-fixture-stop-v1", "tests_run": 0, "errors": errors,
                        "message": "Smoke fixture failed before any test ran; application repair is not authorized by this result"}
    return None


def _passing_smoke_reference(workflow, suite_digest):
    reference_digest = (getattr(workflow, "reference", None) or {}).get("digest")
    proof = getattr(workflow, "evidence", {}).get("self_reference_validation", {})
    proof_receipt = proof.get("receipt_digest")
    if (reference_digest and proof.get("status") == "passed" and proof.get("reference_digest") == reference_digest
            and proof.get("suite_digest") == suite_digest
            and isinstance(proof_receipt, str) and re.fullmatch(r"[a-f0-9]{64}", proof_receipt)):
        return proof_receipt
    return None


def _missing_api_prefix_target(workflow, candidate, summaries, receipt, proof_receipt):
    environment = workflow.environment or {}
    api = environment.get("api") or {}
    prefix = api.get("path_prefix")
    if (not proof_receipt or not prefix or api.get("base_url_kind") != "api_root"
            or "trusted-api" not in (environment.get("verification") or {}).get("required_checks", [])):
        return None
    for summary in summaries:
        checks = summary.get("checks", {})
        if (checks.get("trusted-api-startup", {}).get("status") != "passed"
                or checks.get("trusted-api", {}).get("status") != "failed"):
            continue
        for failure in summary.get("failures", []):
            if failure.get("stage") != "trusted-api" or failure.get("status") != "failed":
                continue
            context_rows = failure.get("api_context")
            if not isinstance(context_rows, list):
                continue
            for context in context_rows:
                match = _MISSING_SPRING_ROUTE.fullmatch(context) if isinstance(context, str) else None
                if match is None:
                    continue
                requested = "/" + match["path"].lstrip("/")
                if not requested.startswith(prefix + "/"):
                    continue
                unprefixed = requested[len(prefix):]
                if unprefixed == prefix or unprefixed.startswith(prefix + "/"):
                    continue
                routes = sorted(file.path for file in candidate.files
                                if file.path.endswith(".java") and "@RestController" in file.content
                                and any(base.rstrip("/") + endpoint == unprefixed
                                        for base in _SPRING_BASE.findall(file.content)
                                        for _verb, endpoint in _SPRING_ENDPOINT.findall(file.content)))
                if not routes:
                    continue
                return {
                    "id": "missing-api-prefix", "check": "trusted-api", "control_match": True,
                    "summary": f"The candidate API does not mount the required {prefix} prefix.",
                    "diagnostics": [f"The running candidate reported no route for {requested}.",
                                    f"The current controller maps the same endpoint at {unprefixed}."],
                    "source_paths": routes, "expected_prefix": prefix, "requested_path": requested,
                    "unprefixed_path": unprefixed, "receipt_digest": digest(receipt),
                    "reference_receipt_digest": proof_receipt,
                    "repair_guidance": (
                        f"Mount the declared API routes under {prefix} exactly once. Check server configuration, "
                        "controller mappings, and security/JWT and CORS rules together. Preserve the public routes, "
                        "authentication requirements, frontend contract, and frozen smoke tests. Fix the application "
                        "rather than sending requests to the unprefixed endpoint."
                    ),
                }
    return None


def validate_self_fixture_correction(before, after):
    """A fixture correction cannot drop coverage or rename/remove test methods."""
    if before.model_dump(exclude={"files"}) != after.model_dump(exclude={"files"}):
        raise ValueError("Smoke fixture correction changed the test manifest or coverage")
    before_names = python_test_names({file.path: file.content for file in before.files})
    after_names = python_test_names({file.path: file.content for file in after.files})
    if before_names != after_names:
        raise ValueError("Smoke fixture correction changed test file, class or method identities")
    def methods(suite):
        return {path: {name: ast.dump(node, include_attributes=False)
                       for name, node in python_test_definitions(tree).items()}
                for path, tree in parse_test_files({file.path: file.content for file in suite.files}).items()}
    if methods(before) != methods(after):
        raise ValueError("Smoke fixture correction changed an existing test method body or assertion")
    return after


def execute_self_test(workflow, candidate, suite, stage, index):
    """Bind the candidate and self-tests to one read-only pinned sandbox execution."""
    workflow.stage = stage
    project = assemble(candidate, suite, workflow.project_type)
    receipt = workflow.runtime._build_execution(workflow.state, stage, index, project, suite)
    summaries = [result for row in receipt.get("commands", [])
                 if (result := sandbox_stage_results(row)) is not None]
    result = {"status": "review_required", "project_digest": digest(project.model_dump()),
              "suite_digest": digest(suite.model_dump()), "execution": receipt,
              "sandbox_stage_results": summaries}
    sandbox = receipt.get("sandbox", {})
    if (sandbox.get("image_id") != workflow.bundle["sandbox"]["image_id"]
            or sandbox.get("network") != "disabled" or sandbox.get("read_only_project") is not True
            or sandbox.get("read_only_root") is not True or not execution_settled(receipt)
            or not matching_image_checks(receipt, summaries, (workflow.environment or {}).get("verification"))):
        return result
    if validate_baseline(receipt, suite):
        result["status"] = "passed"
    elif actionable_failure(receipt):
        result["feedback"] = failure_feedback(receipt, include_stage_results=True)
        scoped = getattr(workflow, "scoped_repairs", False)
        fixture_failure = zero_test_fixture_failure(summaries) if scoped else None
        repair_target = None
        if scoped:
            proof = _passing_smoke_reference(workflow, result["suite_digest"])
            repair_target = _missing_api_prefix_target(workflow, candidate, summaries, receipt, proof)
            if repair_target is None and fixture_failure is None:
                repair_target = mongo_increment_target(candidate, summaries, receipt, proof)
        if repair_target is not None:
            result.update(status="failed", repair_target=repair_target)
        elif fixture_failure is not None:
            result["fixture_failure"] = fixture_failure
        else:
            result["status"] = "failed"
    return result


def execute_verification_profile(workflow, candidate, suite, stage, index, profile):
    """Qualify a verifier mode on fresh reference executions before a candidate.

    A command override is a trusted companion protocol, not evidence that the
    frozen acceptance cases ran. Its explicit image result contract must pass.
    Reference source and diagnostics never appear in returned repair feedback.
    """
    import hashlib
    from .build_sandbox import final_json_record, validate_artifact
    from .build_sandbox_lifecycle import complete_stdout_capture
    from .sandbox_profiles import normalize_verification_profile, verification_profile_identity

    if type(index) is not int or index < 0:
        raise ValueError("Verification profile requires a nonnegative attempt index")
    profile = normalize_verification_profile(profile)
    approved = workflow.bundle.get("build", {}).get("verification_profiles", [])
    if profile not in approved:
        raise ValueError("Verification profile is not in the current approved bundle")
    image_id = workflow.bundle["sandbox"]["image_id"]
    effective = verification_profile_identity(profile, image_id)
    reference = workflow.bundle.get("project_reference") or {}
    if reference.get("digest") != digest(reference.get("files")):
        raise ValueError("Verification profile requires exact approved reference source")
    reference_artifact = workflow.artifact_type.model_validate(
        {"summary": "Approved verification reference", "files": reference["files"]})
    artifact_digest = digest(candidate.model_dump())
    output = {"policy": "reference-first-verification-profile-v1", "status": "review_required",
              "run_id": workflow.state["run_id"], "artifact_digest": artifact_digest,
              "implementation_digest": artifact_digest, "suite_digest": digest(suite.model_dump()),
              "image_id": image_id, "profile_digest": digest(profile), "profile_name": profile["name"],
              "effective_runtime": effective, "execution_settled": False, "index": index,
              "reference_ordinary": None, "reference_profile": None, "candidate": None,
              "feedback": None, "acceptance_cases_executed_in_profile": profile["commands"] is None}

    def check(artifact, suffix, offset, selected):
        workflow.stage = stage + suffix
        if artifact is reference_artifact:
            # Test implementation paths describe the candidate's interface;
            # the hidden reference can use a different internal source layout.
            project = workflow.project_type(summary=artifact.summary, files=[*artifact.files, *suite.files])
            validate_artifact(project)
        else:
            project = assemble(artifact, suite, workflow.project_type)
        options = {"verification_profile": profile} if selected else {}
        receipt = workflow.runtime._build_execution(workflow.state, workflow.stage,
            index * 3 + offset, project, suite, **options)
        requirements = (profile["verification"] if selected and profile["verification"] is not None
                        else (workflow.environment or {}).get("verification"))
        marker = requirements.get("marker", "OFFLINE_PROJECT_RESULT") if requirements else "OFFLINE_PROJECT_RESULT"
        summaries = [value for row in receipt.get("commands", [])
                     if (value := final_json_record(row.get("stderr", ""), marker)) is not None]
        source_hashes = {item.path: hashlib.sha256(item.content.encode("utf-8")).hexdigest()
                         for item in project.files}
        sandbox = receipt.get("sandbox", {})
        settled = (execution_settled(receipt) and sandbox.get("image_id") == image_id
            and sandbox.get("network") == "disabled" and sandbox.get("read_only_root") is True
            and sandbox.get("read_only_project") is True
            and all(row.get("source_unchanged") is True and row.get("source_sha256") == source_hashes
                    for row in receipt.get("commands", [])))
        if selected:
            settled = (settled and sandbox.get("effective_runtime") == effective
                and all(row.get("effective_runtime") == effective and row.get("profile_sources_unchanged") is True
                        for row in receipt.get("commands", [])))
            if profile.get("require_complete_stdout", False):
                settled = settled and all(complete_stdout_capture(row, profile.get("stdout_capture_bytes", 4000))
                                          for row in receipt.get("commands", []))
        else:
            settled = settled and sandbox.get("effective_runtime") is None
        qualified = settled and matching_image_checks(receipt, summaries, requirements)
        companion = selected and profile["commands"] is not None
        passed = qualified and (receipt.get("status") == "passed" if companion else validate_baseline(receipt, suite))
        status = "passed" if passed else "failed" if qualified and actionable_failure(receipt) else "review_required"
        return {"status": status, "execution_settled": bool(settled),
                "project_digest": digest(project.model_dump()), "source_sha256": source_hashes,
                "execution": receipt, "sandbox_stage_results": summaries}

    output["reference_ordinary"] = check(reference_artifact, "_reference_ordinary", 0, False)
    if output["reference_ordinary"]["status"] != "passed":
        output["execution_settled"] = output["reference_ordinary"]["execution_settled"]
        return output
    output["reference_profile"] = check(reference_artifact, "_reference_profile", 1, True)
    if output["reference_profile"]["status"] != "passed":
        output["execution_settled"] = output["reference_profile"]["execution_settled"]
        return output
    output["candidate"] = check(candidate, "_candidate", 2, True)
    output["status"] = output["candidate"]["status"]
    output["execution_settled"] = output["candidate"]["execution_settled"]
    if output["status"] == "failed":
        output["feedback"] = {"kind": "verification-profile-failure", "profile": profile["name"],
            "artifact_digest": artifact_digest, "effective_runtime": effective,
            "reference_source_or_logs_included": False,
            "failure": failure_feedback(output["candidate"]["execution"], include_stage_results=True)}
    return output
