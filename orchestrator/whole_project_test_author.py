"""Public-contract author context and lossless correction of coverage indices."""

from .build_coverage import validate_coverage_references
from .build_discovery import discovery_case
from typing import Literal

from pydantic import Field, create_model

from .build_models import BuildTestCoverage, BuildTestCoverageRepair, BuildTestFile
from .repository import relative
from .schemas import Record
from .store import digest


COVERAGE_PATCH_POLICY = "whole-independent-coverage-patch-v1"
AUTHOR_RESTART_STAGE = "build_tests_restart"
INLINE_AUTHOR_RESTART_POLICY = "inline-independent-author-restart-v1"


class IndependentCoverageRemap(Record):
    coverage_index: int = Field(ge=0, le=79, description="Zero-based row index in existing_coverage.")
    from_criterion: int = Field(ge=1, le=20, description="The unknown criterion currently on that exact row.")
    to_criterion: int = Field(ge=1, le=20, description="An already-covered approved criterion this row actually tests.")


class IndependentCoveragePatch(Record):
    files: list[BuildTestFile] = Field(max_length=15, description="New executable test files only; never return an existing path.")
    coverage: list[BuildTestCoverage] = Field(max_length=80, description="New coverage for the missing criteria, backed by the new files.")
    remaps: list[IndependentCoverageRemap] = Field(max_length=80, description="Exactly one explicit remap for each unknown existing row; no other row may change.")


def coverage_patch_type(diagnostics):
    """Expose only actual unknown row indexes and already-covered target criteria."""
    rows = diagnostics["unknown_entries"]
    if not rows:
        return create_model("BoundIndependentCoveragePatch", __base__=IndependentCoveragePatch,
                            remaps=(list[IndependentCoverageRemap], Field(max_length=0)))
    targets = sorted(set(diagnostics["expected_criteria"]) - set(diagnostics["missing_criteria"]))
    if not targets:
        raise ValueError("Unknown coverage has no already-covered approved criterion to remap to")
    remap_type = create_model(
        "BoundIndependentCoverageRemap", __base__=IndependentCoverageRemap,
        coverage_index=(Literal[tuple(sorted(row["coverage_index"] for row in rows))],
                        Field(description="Zero-based original coverage row index, not a criterion number.")),
        from_criterion=(Literal[tuple(sorted({row["criterion"] for row in rows}))],
                        Field(description="One-based unknown criterion currently on that original row.")),
        to_criterion=(Literal[tuple(targets)],
                      Field(description="One-based already-covered approved criterion this existing test actually checks.")),
    )
    return create_model("BoundIndependentCoveragePatch", __base__=IndependentCoveragePatch,
                        remaps=(list[remap_type], Field(min_length=len(rows), max_length=len(rows))))


def coverage_patch_inputs(previous, criteria, *, byte_limit, file_limit):
    value = previous.model_dump()
    return {
        "coverage_patch_policy": COVERAGE_PATCH_POLICY,
        "previous_suite_digest": digest(value),
        "preserved_manifest_digest": digest({key: item for key, item in value.items()
                                             if key not in {"files", "coverage"}}),
        "existing_files": value["files"], "existing_coverage": value["coverage"],
        "coverage_validation": coverage_index_diagnostics(previous, criteria),
        "max_added_utf8_bytes": byte_limit - sum(len(file.content.encode("utf-8")) for file in previous.files),
        "max_added_files": file_limit - len(previous.files),
    }


def compose_coverage_patch(previous, value, criteria):
    """Apply only declared additions/remaps; never accept or ignore a replacement manifest."""
    patch = IndependentCoveragePatch.model_validate(value)
    before = previous.model_dump()
    expected = set(range(1, len(criteria) + 1))
    covered = {row.criterion for row in previous.coverage} & expected
    unknown = {index: row.criterion for index, row in enumerate(previous.coverage)
               if row.criterion not in expected}
    if (len(patch.remaps) != len(unknown)
            or {remap.coverage_index for remap in patch.remaps} != set(unknown)):
        raise ValueError("Coverage patch must remap each unknown original row exactly once")
    for remap in patch.remaps:
        if remap.from_criterion != unknown[remap.coverage_index] or remap.to_criterion not in covered:
            raise ValueError("Coverage remap must match its unknown row and an already-covered approved criterion")
        before["coverage"][remap.coverage_index]["criterion"] = remap.to_criterion
    occupied = {file.path.casefold() for file in previous.files} | {
        path.casefold() for path in previous.implementation_files}
    for file in patch.files:
        if relative(file.path) != file.path or file.path.casefold() in occupied:
            raise ValueError("Coverage patch files must have new canonical test paths")
        occupied.add(file.path.casefold())
    before["files"].extend(file.model_dump() for file in patch.files)
    before["coverage"].extend(row.model_dump() for row in patch.coverage)
    revised = type(previous).model_validate(before)
    return validate_coverage_correction(previous, revised, criteria)


AUTHOR_GUIDANCE = (
    " Use numbered_acceptance_criteria as the exact coverage index inventory: its numbers are one-based "
    "positions in the approved plan, not numbers for your test cases or files. Cover every supplied criterion "
    "and never invent another index; several tests may cover the same criterion. Derive observable expectations "
    "from the public brief and pinned harness contract, not omitted implementation advice. Create unique, "
    "independent fixtures; use credentials actually registered by the case, and retain successful credentials "
    "separately from expected-error response bodies. A protected success-path request must authenticate and "
    "must require the success status, not accept authorization errors as success. Each isolated DOM mount "
    "must establish its own browser session through the supported UI flow; API authentication in Python does "
    "not sign in the browser. Follow the declared DOM harness invocation and one-boot lifecycle exactly. "
    "Assert observable state changes, exact fixture rows/counts and values after actions, rather than headings "
    "alone. Do not assume a fixture's absolute position in shared results. Describe only operations your test "
    "actually performs: an ordinary create/read-back or timestamp assertion does not prove an API restart. "
    "If a pinned trusted-persistence check is supplied, its restart receipt is separate evidence; do not claim "
    "that your authored test restarted the API or proved more than the declared check establishes."
)


def public_test_author_plan(plan):
    projected = {key: value for key, value in plan.items()
                 if key in {"goal", "acceptance_criteria", "assumptions", "implementation_files"}}
    return projected, {
        "policy": "whole-public-contract-test-author-v1", "plan_digest": digest(plan),
        "omitted_fields": sorted(plan.keys() - projected.keys()),
        "reason": "Implementation and test-strategy narrative is advisory; derive independent tests from the public contract and pinned harness.",
    }


def numbered_criteria(criteria):
    return [{"criterion": index, "requirement": text} for index, text in enumerate(criteria, 1)]


def coverage_index_diagnostics(suite, criteria):
    expected = set(range(1, len(criteria) + 1))
    observed = {entry.criterion for entry in suite.coverage}
    return {
        "expected_criteria": sorted(expected), "observed_criteria": sorted(observed),
        "missing_criteria": sorted(expected - observed), "unknown_criteria": sorted(observed - expected),
        "unknown_entries": [{"coverage_index": index, **entry.model_dump()}
                            for index, entry in enumerate(suite.coverage) if entry.criterion not in expected],
    }


class IndependentCoverageError(ValueError):
    def __init__(self, suite, criteria):
        self.suite = suite
        self.diagnostics = coverage_index_diagnostics(suite, criteria)
        super().__init__(
            "Independent coverage must use every approved criterion index; "
            f"missing={self.diagnostics['missing_criteria']}, unknown={self.diagnostics['unknown_criteria']}")


def validate_coverage_indices(suite, criteria):
    diagnostics = coverage_index_diagnostics(suite, criteria)
    if diagnostics["missing_criteria"] or diagnostics["unknown_criteria"]:
        raise IndependentCoverageError(suite, criteria)
    return suite


def validate_coverage_correction(previous, revised, criteria):
    """Correct numbering/add absent tests without rewriting or silently dropping old obligations."""
    validate_coverage_indices(revised, criteria)
    before, after = previous.model_dump(), revised.model_dump()
    if ({key: value for key, value in before.items() if key not in {"files", "coverage"}}
            != {key: value for key, value in after.items() if key not in {"files", "coverage"}}):
        raise ValueError("Coverage correction changed the existing test manifest")
    originals = {file.path: file.content for file in previous.files}
    replacements = {file.path: file.content for file in revised.files}
    if any(replacements.get(path) != content for path, content in originals.items()):
        raise ValueError("Coverage correction must preserve every existing test file byte-for-byte")
    expected = set(range(1, len(criteria) + 1))
    missing = expected - {entry.criterion for entry in previous.coverage}
    remaining = list(after["coverage"])
    for entry in before["coverage"]:
        if entry["criterion"] in expected:
            match = entry if entry in remaining else None
        else:
            # An unknown index may be remapped only to already-covered behavior.
            # Missing behavior requires newly authored executable tests, not relabelling.
            match = next((row for row in remaining if row["criterion"] not in missing
                          and {key: value for key, value in row.items() if key != "criterion"}
                          == {key: value for key, value in entry.items() if key != "criterion"}), None)
        if match is None:
            raise ValueError("Coverage correction dropped or changed an existing coverage obligation")
        remaining.remove(match)
    new_files = [file for file in revised.files if file.path not in originals]
    new_paths = {file.path for file in new_files}
    if (any(row["criterion"] not in missing or row["test_file"] not in new_paths for row in remaining)
            or new_paths != {row["test_file"] for row in remaining}
            or {row["criterion"] for row in remaining} != missing):
        raise ValueError("Missing criteria require new executable test files with truthful coverage references")
    if remaining:
        addition = BuildTestCoverageRepair(files=new_files, coverage=remaining)
        validate_coverage_references(addition)
        if any(discovery_case(entry) is None for entry in addition.coverage):
            raise ValueError("New coverage must name discoverable Python Class.test_method tests")
    return revised
