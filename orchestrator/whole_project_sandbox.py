"""Validate and execute a whole-project implementer's separate test suite."""

import ast
from collections import Counter

from .build_coverage import correct_test_references, parse_test_files, validate_coverage_references
from .build_discovery import validate_discovery_suite
from .build_workflow import actionable_failure, assemble, validate_baseline, validate_paths
from .project_build import (ProjectTestSuite, execution_settled, failure_feedback,
                            matching_image_checks, sandbox_stage_results)
from .store import digest


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


def validate_self_suite(workflow, raw, candidate):
    """Check a self-authored suite without scheduling the independent test author."""
    suite = ProjectTestSuite.model_validate(raw)
    if set(suite.implementation_files) != {item.path for item in candidate.files}:
        raise ValueError("Self-tests must declare the exact whole-project implementation paths")
    validate_paths(suite, workflow.artifact_type, workflow.project_type)
    suite = _split_self_test_references(suite)
    suite, _ = correct_test_references(suite, qualify_classes=True)
    criterion_count = len(workflow.output["plan"]["acceptance_criteria"])
    if any(entry.criterion > criterion_count for entry in suite.coverage):
        raise ValueError("Self-tests must reference criteria from the approved plan")
    validate_coverage_references(suite)
    validate_discovery_suite(suite)
    workflow.validate_suite(suite)
    problems = workflow.fixture_problems(suite)
    if problems:
        raise ValueError(f"Self-test fixture failed validation: {problems[0]['message']}")
    return suite


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
        result["status"] = "failed"
        result["feedback"] = failure_feedback(receipt, include_stage_results=True)
    return result
