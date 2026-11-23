"""Opt-in bounded, incremental offline projects; the ordinary Python Build is unchanged."""
from __future__ import annotations

import ast
import copy
from difflib import unified_diff
from functools import lru_cache
import hashlib
import json
import math
import re
import time
from types import SimpleNamespace
from typing import ClassVar, Literal

from pydantic import Field, ValidationError, model_validator

from .build_discovery import normalize_authored_test_paths
from .build_models import (BuildArtifact, BuildFile, BuildPlan, BuildTestCoverageRepair, BuildTestFile,
                           BuildTestSyntaxRepair, ReviewedBuildTestSuite, RepairAdvice, UnittestDiscoveryCommand)
from .build_sandbox import DockerBuildExecutor, validate_artifact
from .build_workflow import (BuildRun, TEST_BRIEF, actionable_failure, assemble, build_model_payload, compact_suite,
                             normalized_manifest, public_error, validate_baseline)
from .ollama import _strict_json
from .project_repair import VerificationFailures
from .repository import relative
from .schemas import Record
from .store import digest, encoded
from .whole_project_limits import WHOLE_PROJECT_TEST_BYTES

POLICY = "offline-project-v1"
REVIEWED_POLICY = "offline-project-reviewed-v1"
CODE_REVIEW_POLICY = "independent-project-code-review-v1"
TEST_PREFLIGHT_POLICY = "image-python-test-compile-v1"
EXACT_REPAIR_FORMAT = "scoped-text-edits-v1"
OPENCODE_REPAIR_FORMAT = "opencode-worktree-v1"
SCOPED_REFERENCE_TEST_REPAIR = "scoped-reference-test-repair-v1"
PATH_BOUND_REFERENCE_TEST_REPAIR = "scoped-reference-test-repair-v2"
REFERENCE_TEST_REPAIR = "scoped-reference-test-repair-v3"
DEFAULT_TEST_BYTES = 16 * 1024
EXPANDED_TEST_BYTES = 32 * 1024
WHOLE_PROJECT_REPAIR_SELECTED_BYTES = 64 * 1024


def offline_project_contract(profile=POLICY, *, test_bytes=DEFAULT_TEST_BYTES):
    """Return a fresh approval-bound contract with a supported test-suite budget."""
    if profile not in (POLICY, REVIEWED_POLICY):
        raise ValueError("Unknown offline project profile")
    if type(test_bytes) is not int or (test_bytes != DEFAULT_TEST_BYTES
            and (profile != REVIEWED_POLICY or test_bytes not in (EXPANDED_TEST_BYTES, WHOLE_PROJECT_TEST_BYTES))):
        raise ValueError("Unsupported offline project test-suite budget")
    contract = {
        "profile": profile,
        "implementation_bytes": 131072, "implementation_files": 128,
        "test_bytes": test_bytes, "test_files": 15,
        "project_bytes": 163840, "project_files": 143,
        "group_bytes": 24576, "group_files": 16, "group_count": 32,
        "group_plan_format_repairs": 2,
        "test_fixture_policy": "unittest-http-fixtures-v1", "max_test_fixture_repairs": 2,
        "context_bytes": 49152, "repair_selected_bytes": 32768, "repair_files": 16,
        "test_lint_policy": "generated-test-lint-v1", "max_test_defect_repairs": 2,
        "max_quarantined_tests": 2,
        "verification_policy": "unittest-acceptance-v1", "mutation_verification": "not_run",
        "failure_feedback_policy": "utf8-tails-with-full-receipt-digest-v1",
        "sandbox_tmpfs_mb": 2048, "sandbox_memory_mb": 16384,
        "sandbox_tmpfs_noexec": True,
        "sandbox_nofile": 1024,
        "sandbox_pids_limit": 256,
        "sandbox_protocol": {
            "application_directory_env": "APP_PROJECT_DIR",
            "http_base_url_env": "API_BASE_URL",
            "readonly_tests": "/workspace/tests",
            "image_bootstraps_application": True,
        },
    }
    if profile == REVIEWED_POLICY:
        contract.update(
            code_review_policy=CODE_REVIEW_POLICY,
            test_preflight_policy=TEST_PREFLIGHT_POLICY,
            mechanical_test_repair_generation={"think": False},
            max_review_chunks=16,
            max_review_repair_findings=6,
            review_output_reserve=4096,
            review_blocking_severities=["P0", "P1"],
            sandbox_failure_stderr_bytes=32768,
            include_direct_dependency_source=True,
            max_compile_checkpoints=4,
            max_compile_repairs=2,
            # Repair attempt N uses tier N (the last tier repeats). A tier-1 call that ends at its
            # output cap is retried once at the last tier in the same attempt.
            # Caps count thinking tokens. At about 52 tokens/s on an RTX 3090 they allow roughly
            # 15 and 21 minutes, above the 50,301-token xhigh repair seen on Nov 12, and leave room
            # for repair prompts of 25-30K tokens inside the 98,304-token context.
            repair_generation=[{"think": "medium", "output_tokens": 49152},
                               {"think": "xhigh", "output_tokens": 65536}],
            # A repair starts only when the run has time for it and the check after it: the longest
            # duration observed in this run for that repair tier and check, never below these floors.
            repair_time_floor_seconds={"medium": 600, "xhigh": 900},
            check_time_floor_seconds={"verification": 300, "compile": 120},
        )
    return contract


def project_enabled(bundle):
    profile = bundle.get("request", {}).get("build_profile")
    repair_format = bundle.get("project_repair_format")
    if repair_format is not None and (profile != REVIEWED_POLICY or repair_format != EXACT_REPAIR_FORMAT):
        raise ValueError("The scoped repair format requires the reviewed project profile")
    if profile is None:
        if bundle.get("build_contract") is not None or "implementation_group_bytes" in bundle or "test_suite_bytes" in bundle:
            raise ValueError("A project contract requires its explicit Build profile")
        return False
    if profile not in (POLICY, REVIEWED_POLICY):
        raise ValueError("Offline projects require operator enablement and the exact approved contract")
    approved = bundle.get("build_contract")
    expected = offline_project_contract(profile, test_bytes=bundle.get("test_suite_bytes", DEFAULT_TEST_BYTES))
    if profile == REVIEWED_POLICY and isinstance(approved, dict) and (
            approved.get("repair_selected_bytes") == WHOLE_PROJECT_REPAIR_SELECTED_BYTES
            or expected["test_bytes"] == WHOLE_PROJECT_TEST_BYTES):
        from .whole_project import POLICY as WHOLE_PROJECT_POLICY
        whole_project = bundle.get("whole_project_workflow") == WHOLE_PROJECT_POLICY
        if expected["test_bytes"] == WHOLE_PROJECT_TEST_BYTES and (not whole_project or "project_worker" in bundle):
            raise ValueError("64 KiB of test source requires the whole-project workflow")
        if whole_project and approved.get("repair_selected_bytes") == WHOLE_PROJECT_REPAIR_SELECTED_BYTES:
            expected["repair_selected_bytes"] = WHOLE_PROJECT_REPAIR_SELECTED_BYTES
    if isinstance(approved, dict) and "mechanical_test_repair_generation" not in approved:
        # Older approvals keep their original settings when a checkpoint is reconstructed.
        expected.pop("mechanical_test_repair_generation", None)
    if (bundle.get("mode", "build") != "build" or approved != expected
            or bundle.get("build", {}).get("offline_project_enabled") is not True):
        raise ValueError("Offline projects require operator enablement and the exact approved contract")
    if bundle["build_contract"]["test_bytes"] == EXPANDED_TEST_BYTES and "project_worker" in bundle:
        raise ValueError("The expanded test-suite budget requires a native or whole-project workflow")
    group_artifact_type(bundle)
    roles = bundle.get("roles", {})
    from .role_isolation import validate_role_isolation
    isolated_same_model = validate_role_isolation(bundle)
    shared_author = bundle["build"].get("allow_shared_test_author") is True
    if shared_author and (profile != REVIEWED_POLICY or bundle.get("project_reference") is None
                          or (bundle.get("backend", {}).get("name") != "ollama" and not isolated_same_model)):
        raise ValueError("A shared test author requires a reviewed Ollama project with a trusted reference")
    if (not roles.get("repairer") or not roles.get("implementer") or not roles.get("test_author")
            or (roles["test_author"] in {roles["implementer"], roles["repairer"]}
                and not (shared_author or isolated_same_model))):
        raise ValueError("Offline projects require an independent test author and configured repair role")
    if bundle.get("backend", {}).get("name") in {"strata", "llama-cpp"}:
        raise ValueError("Offline projects need a backend that can serve independent models for testing and implementation")
    if bundle.get("request", {}).get("control_files"):
        raise ValueError("Offline projects do not accept reference implementations")
    if profile == REVIEWED_POLICY:
        settings = (bundle.get("role_profiles") or bundle.get("generation") or {}).get("reviewer", {})
        if (not roles.get("reviewer")
                or (roles["reviewer"] in {roles["implementer"], roles["repairer"]} and not isolated_same_model)
                or type(settings.get("context")) is not int or settings["context"] <= 0):
            raise ValueError("Reviewed projects require an independent reviewer and frozen reviewer context")
    from .project_reference import validate_reference
    validate_reference(bundle.get("project_reference"))
    from .project_environment import validate_project_environment
    environment = validate_project_environment(bundle)
    if (environment or {}).get("verification") and profile != REVIEWED_POLICY:
        raise ValueError("Required image checks need the reviewed project profile")
    return True


def project_executor(runtime, state):
    executor = runtime.build_executor
    bundle = runtime.bundle(state)
    if project_enabled(bundle) and type(executor) is DockerBuildExecutor:
        contract = bundle["build_contract"]
        settings = {**bundle["build"], "temporary_mb": contract["sandbox_tmpfs_mb"],
                    "memory_mb": contract["sandbox_memory_mb"],
                    "temporary_noexec": contract["sandbox_tmpfs_noexec"],
                    "open_files": contract["sandbox_nofile"],
                    "process_limit": contract["sandbox_pids_limit"]}
        if "sandbox_failure_stderr_bytes" in contract:
            settings["failure_stderr_bytes"] = contract["sandbox_failure_stderr_bytes"]
        return DockerBuildExecutor(runtime.store.directory, SimpleNamespace(**settings))
    return executor


def validate_project_prompt(payload, bundle, role, *, tokenize=None):
    """Check the complete prompt and reserve space for provider framing.

    A pinned model tokenizer counts the supplied messages and schema. Backends
    without a tokenizer retain conservative UTF-8 admission.
    """
    settings = (bundle.get("role_profiles") or bundle.get("generation") or {}).get(role, {})
    context = settings.get("context")
    if type(context) is not int or context <= 0:
        raise ValueError("Offline projects require an explicit frozen context for every model role")
    if tokenize is None:
        prompt_units = (sum(len(m["content"].encode("utf-8")) for m in payload["messages"])
                        + len(encoded(payload["schema"]).encode("utf-8")))
    else:
        prompt_units = tokenize(payload)
        if type(prompt_units) is not int or prompt_units < 0:
            raise ValueError("The approved tokenizer did not return a valid token count")
    required = prompt_units + 2048
    if required > context:
        raise ValueError("Complete project prompt, schema and framing exceed the frozen role context; inference was not dispatched")
    return required


class ProjectArtifact(BuildArtifact):
    byte_limit: ClassVar[int] = 131072
    files: list[BuildFile] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def implementation_only(self):
        validate_artifact(self)
        if any(f.path.casefold().split("/")[0] == "tests" for f in self.files):
            raise ValueError("Implementation cannot occupy the independent tests directory")
        return self


class TestManifest(BuildArtifact):
    byte_limit: ClassVar[int] = DEFAULT_TEST_BYTES
    files: list[BuildFile] = Field(min_length=1, max_length=15)


class ProjectTestSuite(ReviewedBuildTestSuite):
    manifest_type: ClassVar[type[BuildArtifact]] = TestManifest
    allow_python_helpers: ClassVar[bool] = True
    implementation_files: list[str] = Field(min_length=1, max_length=128)
    commands: list[UnittestDiscoveryCommand] = Field(min_length=1, max_length=1)


class ProjectProject(BuildArtifact):
    byte_limit: ClassVar[int] = 163840
    implementation_type: ClassVar[type[ProjectArtifact]] = ProjectArtifact
    test_manifest_type: ClassVar[type[TestManifest]] = TestManifest
    files: list[BuildFile] = Field(min_length=1, max_length=143)

    @model_validator(mode="after")
    def component_limits(self):
        validate_artifact(self)
        tests = [f for f in self.files if f.path.startswith("tests/")]
        implementation = [f for f in self.files if not f.path.startswith("tests/")]
        self.implementation_type(summary=self.summary, files=implementation)
        self.test_manifest_type(summary="Frozen independent tests", files=tests)
        return self


class ExpandedTestManifest(TestManifest):
    byte_limit: ClassVar[int] = EXPANDED_TEST_BYTES


class ExpandedProjectTestSuite(ProjectTestSuite):
    manifest_type: ClassVar[type[BuildArtifact]] = ExpandedTestManifest


class ExpandedProjectProject(ProjectProject):
    test_manifest_type: ClassVar[type[TestManifest]] = ExpandedTestManifest


class WholeProjectTestManifest(TestManifest):
    byte_limit = WHOLE_PROJECT_TEST_BYTES


class WholeProjectTestSuite(ProjectTestSuite):
    manifest_type = WholeProjectTestManifest


class WholeProjectAssembly(ProjectProject):
    byte_limit = ProjectArtifact.byte_limit + WHOLE_PROJECT_TEST_BYTES
    test_manifest_type = WholeProjectTestManifest


def project_test_types(bundle):
    if bundle.get("test_suite_bytes") == WHOLE_PROJECT_TEST_BYTES:
        return WholeProjectTestSuite, WholeProjectAssembly
    if bundle.get("test_suite_bytes") == EXPANDED_TEST_BYTES:
        return ExpandedProjectTestSuite, ExpandedProjectProject
    return ProjectTestSuite, ProjectProject


def validate_project_artifact(value, bundle=None):
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    _, project_type = project_test_types(bundle or {})
    return project_type.model_validate(value)


class GroupArtifact(ProjectArtifact):
    byte_limit: ClassVar[int] = 24576
    files: list[BuildFile] = Field(min_length=1, max_length=16)


@lru_cache(maxsize=16)
def _budgeted_group_artifact(limit):
    class BudgetedGroupArtifact(GroupArtifact):
        byte_limit: ClassVar[int] = limit

    return BudgetedGroupArtifact


def group_artifact_type(bundle):
    limit = bundle.get("implementation_group_bytes", GroupArtifact.byte_limit)
    if type(limit) is not int or not 1 <= limit <= ProjectArtifact.byte_limit:
        raise ValueError("Implementation group bytes must fit within the project source budget")
    return GroupArtifact if limit == GroupArtifact.byte_limit else _budgeted_group_artifact(limit)


@lru_cache(maxsize=64)
def exact_group_artifact(base, paths):
    """Constrain a group response to exactly its frozen paths, so the output schema rejects others.

    The JSON schema lists the allowed paths as an enum and fixes the file count,
    which grammar-constrained backends enforce while generating.
    """
    choices = tuple(paths)

    class ExactGroupFile(BuildFile):
        path: Literal[choices] = Field(json_schema_extra=lambda schema: schema.update(enum=list(choices)))

    class ExactGroupArtifact(base):
        files: list[ExactGroupFile] = Field(min_length=len(choices), max_length=len(choices))

    ExactGroupArtifact.__name__ = base.__name__
    return ExactGroupArtifact


REPAIR_STAGE = re.compile(r"^repair_(\d+)(?:_select|_edit_retry)?$")
COMPILE_REPAIR_STAGE = re.compile(r"^build(?:_repair)?_compile_repair_(?:\d+_)*(\d+)(?:_select|_edit_retry)?$")
MECHANICAL_TEST_REPAIR_STAGE = re.compile(r"build_tests_(?:syntax|fixture)_\d+")
ESCALATED = "_escalated"


class OutputCapReached(ValueError):
    """A top-tier repair call ended at its output cap; the attempt is spent, the code is unchanged."""

    def __init__(self, stage):
        super().__init__(f"{stage} ended at its output cap before returning a repair")
        self.stage = stage


def repair_tier(bundle, role, stage):
    """Return the 1-based repair tier for a repairer call, or None when tiers do not apply."""
    tiers = (bundle.get("build_contract") or {}).get("repair_generation")
    if role != "repairer" or not tiers:
        return None
    if stage.endswith(ESCALATED):
        return len(tiers)
    match = REPAIR_STAGE.match(stage) or COMPILE_REPAIR_STAGE.match(stage)
    return min(int(match.group(1)), len(tiers)) if match else None


def call_generation(bundle, role, stage):
    """Apply the approved per-stage generation overrides to the role's settings."""
    generation = bundle["generation"][role]
    if role == "test_author" and MECHANICAL_TEST_REPAIR_STAGE.fullmatch(stage):
        mechanical = (bundle.get("build_contract") or {}).get("mechanical_test_repair_generation")
        if mechanical:
            return {**generation, **mechanical}
    tier = repair_tier(bundle, role, stage)
    if tier is None or not isinstance(generation.get("think"), str):
        return generation
    return {**generation, **bundle["build_contract"]["repair_generation"][tier - 1]}


INTERFACE_PATH = re.compile(
    r"(?i)(/dto/|/domain/|/model/|/models/|/types?/|\.d\.ts$|types?\.ts$|/api/|client|/stores?/|"
    r"router|request\.java$|response\.java$|repository\.java$|/config/|/plugins/)"
)


def interface_first(paths):
    """Order a group's files so shared contracts are selected before implementations."""
    return sorted(paths, key=lambda path: INTERFACE_PATH.search(path) is None)


def python_test_names_for(suite):
    from .build_coverage import python_test_names
    return python_test_names({f.path: f.content for f in suite.files})


def same_test(reference, name):
    """True when a coverage reference names this Class.method or module-level test."""
    return reference == name or reference.endswith("." + name)


def remove_test_methods(source, names):
    """Delete the named test methods (Class.method or module-level) with their decorators."""
    import ast
    tree = ast.parse(source)
    spans = []
    for node in tree.body:
        candidates = [(node.name, node)] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else []
        if isinstance(node, ast.ClassDef):
            candidates = [(f"{node.name}.{item.name}", item) for item in node.body
                          if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
            doomed = [item for ref, item in candidates if ref in names]
            if doomed and len(doomed) == len(node.body):
                raise ValueError(f"Quarantine would empty test class {node.name}")
        for ref, item in candidates:
            if ref in names:
                start = min([item.lineno] + [d.lineno for d in item.decorator_list])
                spans.append((start, item.end_lineno))
    found = len(spans)
    if found != len(names):
        raise ValueError("Quarantine could not locate every failing test method")
    lines = source.splitlines(keepends=True)
    for start, end in sorted(spans, reverse=True):
        del lines[start - 1:end]
    revised = "".join(lines)
    ast.parse(revised)
    return revised


UNITTEST_BLOCK = re.compile(
    r"(?ms)^={20,}\n(?:FAIL|ERROR): (test_\w+) \([^\n]*\)\n"
    r"(?:(?![-=]{20,}\n)[^\n]*\n)*-{20,}\n"
    r"(.*?)(?=^={20,}\n|^-{20,}\nRan \d+ tests?|\Z)"
)
ERROR_HEADLINE = re.compile(r"(?m)^[ \t]*[\w.]*(?:Error|Exception|Failure|ExceptionGroup):[^\n]+")


def bounded_failure_excerpt(body, limit):
    tail = body[-limit:]
    marker = "\n...\n"
    if len(body) <= limit or limit <= len(marker) + 1:
        return tail
    headlines = ERROR_HEADLINE.findall(body)
    if not headlines or headlines[-1].strip() in tail:
        return tail
    headline = headlines[-1].strip()[:(limit - len(marker)) // 2]
    prefix = headline + marker
    return prefix + tail[-(limit - len(prefix)):]


def unittest_failure_excerpts(receipt, limit=1200):
    """Per-test unittest failure text from a receipt; test-side tracebacks and messages only."""
    if limit <= 0:
        return {}
    excerpts = {}
    for row in receipt.get("commands") or []:
        log = ((row.get("stdout") or "") + "\n" + (row.get("stderr") or "")).replace("\r\n", "\n")
        for name, body in UNITTEST_BLOCK.findall(log):
            excerpts.setdefault(name, bounded_failure_excerpt(body.strip(), limit))
    return excerpts


def validate_reference_test_replacement(original, replacement, names, byte_limit):
    """Validate the edit before running any generated code against the reference."""
    from .build_coverage import parse_test_files, python_test_definitions

    if replacement.path != original.path or len(replacement.content.encode("utf-8")) > byte_limit:
        raise ValueError("Return the named test file within its replacement byte budget")
    path = original.path
    prior = parse_test_files({path: original.content})[path]
    edited = parse_test_files({path: replacement.content})[path]
    before, after = python_test_definitions(prior), python_test_definitions(edited)
    if before.keys() != after.keys():
        raise ValueError("Preserve the exact set of test methods")
    if any(ast.dump(before[name]) != ast.dump(after[name]) for name in before.keys() - set(names)):
        raise ValueError("Preserve the body and decorators of every test outside the failure list")
    if ast.dump(prior) == ast.dump(edited):
        raise ValueError("The replacement must change code that addresses the named failures")


def remove_unmatched_test_trailer(file, diagnostics):
    """Remove parser-identified delimiters accidentally appended to generated source."""
    if len(diagnostics) != 1:
        return None
    issue = diagnostics[0]
    lines = file.content.splitlines(keepends=True)
    line = issue["line"] - 1
    if issue["path"] != file.path or not 0 <= line < len(lines):
        return None
    trailer = lines[line].strip()
    remaining = [part.strip() for part in lines[line + 1:] if part.strip()]
    if trailer in {")", "]", "}"} and issue["message"] == f"unmatched '{trailer}'" and not remaining:
        return file.model_copy(update={"content": "".join(lines[:line] + lines[line + 1:])})
    if trailer == '"' and remaining == ["}", "]", "}"] and issue["message"].startswith("unterminated string literal"):
        return file.model_copy(update={"content": "".join(lines[:line])})
    return None


@lru_cache(maxsize=64)
def reference_test_replacement_type(approved_path):
    class ReplacementFile(BuildTestFile):
        path: str = Field(min_length=1, max_length=500, json_schema_extra={"enum": [approved_path]})

        @model_validator(mode="after")
        def check_path(self):
            if self.path != approved_path:
                raise ValueError("Return only the approved test file")
            return self

    class ReferenceTestReplacement(BuildTestSyntaxRepair):
        files: list[ReplacementFile] = Field(min_length=1, max_length=1)

    return ReferenceTestReplacement


class RepairArtifact(ProjectArtifact):
    byte_limit: ClassVar[int] = 32768
    files: list[BuildFile] = Field(min_length=1, max_length=16)


class ProjectGroup(Record):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,30}$")
    files: list[str] = Field(min_length=1, max_length=16)
    depends_on: list[str] = Field(default_factory=list, max_length=32)
    context_files: list[str] = Field(default_factory=list, max_length=32)
    interface: str = Field(min_length=1, max_length=2000)


class GroupPlan(Record):
    contract: str = Field(min_length=1, max_length=8000)
    groups: list[ProjectGroup] = Field(min_length=1, max_length=32)


class RepairSelection(Record):
    diagnosis: RepairAdvice
    files: list[str] = Field(min_length=1, max_length=16)


@lru_cache(maxsize=64)
def scoped_repair_selection(paths):
    class ScopedRepairSelection(RepairSelection):
        files: list[Literal[paths]] = Field(min_length=1, max_length=min(16, len(paths)))

    return ScopedRepairSelection


def control_repair_selection(target, repair_paths):
    """Build a selection from a recognized failure and the verifier adapter's scope."""
    advice = RepairAdvice(diagnosis=target["diagnostics"], changes=[target["repair_guidance"]],
                          verification=[target["summary"]])
    return RepairSelection(diagnosis=advice, files=repair_paths)


def control_repair_proof(artifact, target, selection):
    return {"source": "trusted_verifier", "selection": selection.model_dump(),
            "implementation_digest": digest(artifact.model_dump()), "target_digest": digest(target)}


class SelectedRepair(Record):
    diagnosis: RepairAdvice
    artifact: RepairArtifact


class RepairTextEdit(Record):
    path: str = Field(min_length=1, max_length=500)
    before: str = Field(min_length=1, max_length=4096)
    after: str = Field(max_length=12288)


class SelectedTextRepair(Record):
    summary: str = Field(min_length=1, max_length=2000)
    edits: list[RepairTextEdit] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def bounded_text(self):
        parts = [text for edit in self.edits for text in (edit.before, edit.after)]
        if any("\0" in text for text in parts):
            raise ValueError("Text repair contains a null byte")
        if sum(len(text.encode("utf-8")) for text in parts) > 12288:
            raise ValueError("Text repair exceeds the 12288-byte edit budget")
        return self


def materialize_text_repair(artifact, selected_paths, patch) -> RepairArtifact:
    """Resolve exact edits against the original selected files before assembling replacements."""
    patch = SelectedTextRepair.model_validate(patch)
    return RepairArtifact(summary=patch.summary, files=_materialize_text_edits(artifact.files, selected_paths, patch))


def _materialize_text_edits(original_files, selected_paths, patch):
    source = {file.path: file.content for file in original_files}
    scope = set(selected_paths)
    if not scope or len(scope) != len(selected_paths) or not scope <= source.keys():
        raise ValueError("Text repair scope must identify distinct existing implementation files")

    changes = {}
    for edit in patch.edits:
        if edit.path not in scope:
            raise ValueError("Text repair returned a path outside its selection")
        if edit.before == edit.after:
            raise ValueError("Text repair contains an unchanged edit")
        original = source[edit.path]
        start = original.find(edit.before)
        if start < 0:
            raise ValueError(f"Text repair anchor is missing in {edit.path}")
        if original.find(edit.before, start + 1) >= 0:
            raise ValueError(f"Text repair anchor is ambiguous in {edit.path}")
        changes.setdefault(edit.path, []).append((start, start + len(edit.before), edit.after))

    files = []
    for path in selected_paths:
        if path not in changes:
            continue
        original = source[path]
        parts, cursor = [], 0
        for start, end, replacement in sorted(changes[path]):
            if start < cursor:
                raise ValueError(f"Text repair anchors overlap in {path}")
            parts.extend((original[cursor:start], replacement))
            cursor = end
        parts.append(original[cursor:])
        content = "".join(parts)
        if content == original:
            raise ValueError(f"Text repair leaves {path} unchanged")
        files.append(BuildFile(path=path, content=content))
    return files


def test_syntax_repair_inputs(suite, diagnostics):
    """Expose only bounded, exact source around a Python syntax diagnostic."""
    source = {file.path: file.content for file in suite.files}
    snippets = []
    for diagnostic in diagnostics:
        path, line = diagnostic["path"], diagnostic.get("line")
        if path not in source or type(line) is not int or line < 1 or not source[path]:
            return None
        lines = source[path].splitlines(keepends=True)
        index = min(line - 1, len(lines) - 1)
        start = max(0, index - 4)
        snippet = "".join(lines[start:index + 7])
        start_column = 1
        if len(snippet) > 3000:
            # Keep the reported column near the middle even for one long source line.
            column = min(max((diagnostic.get("column") or 1) - 1, 0), len(lines[index]))
            offset = max(0, sum(map(len, lines[start:index])) + column - 1400)
            prefix = snippet[:offset]
            skipped = prefix.splitlines(keepends=True)
            start += len(skipped)
            if skipped and not skipped[-1].endswith(("\n", "\r")):
                start -= 1
                start_column = len(skipped[-1]) + 1
            snippet = snippet[offset:offset + 3000]
        snippets.append({"path": path, "start_line": start + 1, "start_column": start_column, "source": snippet})
    return {"patch_format": EXACT_REPAIR_FORMAT, "diagnostics": diagnostics, "snippets": snippets}


TEST_DECLARATION = re.compile(r"(?m)^[ \t]*(?:class[ \t]+([A-Za-z_]\w*)|(?:async[ \t]+)?def[ \t]+(test_\w+))\b")


def apply_test_syntax_repair(suite, inputs, value):
    """Apply literal syntax edits without changing unrelated tests or coverage."""
    patch = SelectedTextRepair.model_validate(value)
    snippets = inputs["snippets"]
    for edit in patch.edits:
        if not any(edit.path == item["path"] and edit.before in item["source"] for item in snippets):
            raise ValueError("Test syntax repair anchor is outside the diagnosed source snippets")
    paths = list(dict.fromkeys(item["path"] for item in snippets))
    replacements = _materialize_text_edits(suite.files, paths, patch)
    changed = {file.path: file for file in replacements}
    for file in suite.files:
        if file.path in changed and sorted(TEST_DECLARATION.findall(file.content)) != sorted(
                TEST_DECLARATION.findall(changed[file.path].content)):
            raise ValueError("Test syntax repair changed a test or class declaration")
    return type(suite).model_validate({**suite.model_dump(), "files": [
        changed.get(file.path, file).model_dump() for file in suite.files]})


def validate_group_plan(value, declared):
    plan = GroupPlan.model_validate(value)
    expected = [relative(path) for path in declared]
    if len(expected) != len(set(p.casefold() for p in expected)):
        raise ValueError("Declared project paths collide")
    owners, groups, seen = {}, {}, set()
    for group in plan.groups:
        if group.id in groups or len(set(group.depends_on)) != len(group.depends_on):
            raise ValueError("Project groups must have distinct identities and dependencies")
        if not set(group.depends_on) <= groups.keys():
            raise ValueError("Group dependencies must name earlier groups")
        permitted_context = {p for dependency in group.depends_on for p in groups[dependency].files}
        invalid_context = {path: owners.get(path, "no earlier owner")
                           for path in sorted(set(group.context_files) - permitted_context)}
        duplicates = sorted(path for path in set(group.context_files) if group.context_files.count(path) > 1)
        if duplicates or invalid_context:
            required = sorted({owners[path] for path in invalid_context if path in owners})
            raise ValueError(
                f"Group {group.id}: context_files must select distinct files from DIRECT depends_on groups; "
                f"invalid_context_files_and_owners={invalid_context}, "
                f"missing_direct_depends_on={required}, duplicate_context_files={duplicates}. "
                "List each selected file's owning group directly in depends_on, even when that owner is "
                "already a transitive dependency; otherwise remove the context file."
            )
        for path in group.files:
            if path != relative(path) or path not in expected or path.casefold() in seen:
                raise ValueError("Group paths must exactly partition the declared implementation interface")
            seen.add(path.casefold())
            owners[path] = group.id
        if not group.files or len(group.files) != len(set(group.files)):
            raise ValueError("Project groups require distinct declared files")
        groups[group.id] = group
    if set(owners) != set(expected):
        raise ValueError("Project groups must cover every declared implementation file exactly once")
    if len(encoded(plan.model_dump()).encode("utf-8")) > 32768:
        raise ValueError("Project grouping metadata exceeds its bound")
    return plan


def selected_source(files, paths, byte_limit):
    by_path = {f.path: f for f in files}
    if (not paths or len(paths) != len(set(paths)) or any(p not in by_path for p in paths)):
        raise ValueError("Selected source paths must identify distinct existing implementation files")
    chosen = [by_path[path] for path in paths]
    if sum(len(f.content.encode("utf-8")) for f in chosen) > byte_limit:
        raise ValueError("Selected source exceeds the approved context window; inference was not dispatched")
    return [f.model_dump() for f in chosen]


def repair_diagnostic_source(files, paths, byte_limit):
    """Include whole available reference files within the repair context budget."""
    if not paths:
        return []
    by_path = {file.path: file for file in files}
    source = []
    for path in dict.fromkeys(paths):
        if (file := by_path.get(path)) is None:
            continue
        size = len(file.content.encode("utf-8"))
        if size <= byte_limit:
            byte_limit -= size
            source.append(file.model_dump())
    return source


def replace_selected(current, replacement, paths, *, allow_subset=False, artifact_type=ProjectArtifact):
    changes = {f.path: f for f in replacement.files}
    selected = set(paths)
    if allow_subset:
        if not changes.keys() <= selected:
            raise ValueError("A repair returned paths outside its selection")
    elif changes.keys() != selected:
        raise ValueError("A group or repair must return exactly its selected implementation paths")
    return artifact_type(summary=replacement.summary,
                         files=[changes.get(f.path, f) for f in current.files])


def sandbox_stage_results(row):
    from .build_sandbox import final_json_record
    result = final_json_record(row.get("stderr", ""), "OFFLINE_PROJECT_RESULT")
    if result is not None and len(encoded(result).encode("utf-8")) <= 14000:
        return result
    return None


def matching_image_checks(receipt, summaries, requirements):
    if requirements is None:
        return True
    rows = receipt.get("commands") or []
    if len(rows) != 1 or len(summaries) != 1:
        return False
    summary, row = summaries[0], rows[0]
    checks = summary.get("checks")
    if (summary.get("policy") != requirements["policy"] or summary.get("cleanup_confirmed") is not True
            or summary.get("exit_code") != row.get("exit_code") or not isinstance(checks, dict)):
        return False
    if receipt.get("status") == "passed":
        return (summary.get("status") == "passed"
                and all(isinstance(checks.get(name), dict) and checks[name].get("status") == "passed"
                        for name in requirements["required_checks"]))
    expected = {1: "failed", 125: "review_required"}.get(row.get("exit_code"))
    return expected is not None and summary.get("status") == expected


def execution_settled(receipt):
    rows = receipt.get("commands") or []
    return bool(rows) and all(
        type(row.get("exit_code")) is int and row.get("cleanup_confirmed") is True
        and not row.get("timed_out") and not row.get("cancelled") for row in rows
    )


def compile_checkpoint_status(receipt, summary, checkpoint, policy, *, source_hashes=None, image_id=None):
    """Classify only verified compiler outcomes as eligible for repair."""
    profile = checkpoint.get("verification_profile")
    if profile is not None:
        from .sandbox_profiles import verification_profile_identity
        effective = verification_profile_identity(profile, image_id)
        sandbox = receipt.get("sandbox") or {}
        if (source_hashes is None or sandbox.get("image_id") != image_id
                or sandbox.get("network") != "disabled" or sandbox.get("read_only_root") is not True
                or sandbox.get("read_only_project") is not True or sandbox.get("effective_runtime") != effective
                or not receipt.get("commands")
                or any(row.get("effective_runtime") != effective or row.get("profile_sources_unchanged") is not True
                       or row.get("source_unchanged") is not True or row.get("source_sha256") != source_hashes
                       for row in receipt["commands"])):
            return "review_required"
        if profile.get("require_complete_stdout", False):
            from .build_sandbox_lifecycle import complete_stdout_capture
            if not all(complete_stdout_capture(row, profile.get("stdout_capture_bytes", 4000))
                       for row in receipt["commands"]):
                return "review_required"
    requirements = {"policy": policy, "required_checks": checkpoint["required_checks"]}
    if (summary is None or summary.get("checkpoint") != checkpoint["name"]
            or not execution_settled(receipt)
            or not matching_image_checks(receipt, [summary], requirements)):
        return "review_required"
    exit_code = receipt["commands"][0]["exit_code"]
    failures = summary.get("failures")
    if exit_code == 0 and failures == []:
        return "passed"
    if exit_code != 1 or receipt.get("status") != "failed" or not isinstance(failures, list):
        return "review_required"
    checks = summary["checks"]
    states = {name: checks[name].get("status") for name in requirements["required_checks"]
              if isinstance(checks.get(name), dict)}
    failed = {name for name, status in states.items() if status == "failed"}
    if (not failed or set(states) != set(requirements["required_checks"])
            or any(status not in ("passed", "failed") for status in states.values())):
        return "review_required"
    if (any(not isinstance(item, dict) or not isinstance(item.get("stage"), str) or item["stage"] not in failed
            or item.get("status") != "failed" or item.get("exit_code") != 1
            or not isinstance(item.get("diagnostic_excerpt"), str) or not item["diagnostic_excerpt"].strip()
            for item in failures) or {item["stage"] for item in failures} != failed):
        return "review_required"
    return "failed"


def failure_feedback(receipt, *, include_stage_results=False):
    """Label bounded diagnostic excerpts; retain the full execution receipt separately."""
    commands = []
    for row in receipt["commands"]:
        item = {"exit_code": row.get("exit_code"), "output_feedback": {}}
        for key, maximum in (("stdout", 2000), ("stderr", 5000)):
            original = row.get(key, "").encode("utf-8")
            tail = original[-maximum:].decode("utf-8", errors="ignore")
            item[key] = tail
            item["output_feedback"][key] = {
                "original_utf8_bytes": len(original), "included_utf8_bytes": len(tail.encode("utf-8")),
                "truncated": len(original) > maximum,
                "source": "retained_execution_receipt",
            }
        if include_stage_results:
            summary = sandbox_stage_results(row)
            if summary is not None:
                item["sandbox_stage_results"] = summary
        commands.append(item)
    return {"feedback_policy": "utf8-tails-with-full-receipt-digest-v1",
            "full_failure_receipt_digest": digest(receipt), "commands": commands}


def settled_group_response(raw):
    if not isinstance(raw, dict) or raw.get("native_completion") in {"unknown", "not_started_unknown"}:
        return False
    native = raw.get("native") or {}
    return (isinstance(native, dict) and native.get("terminal_state") in {None, "stop"}
            and all(native.get(key) is not False for key in
                    ("cleanup_confirmed", "native_done_ack", "handler_finished", "admission_released")))


def terminal_group_plan_feedback(runtime, state, stage, declared):
    """A corrective call needs a finished normal response with a provably invalid plan.

    An admission/transport failure, missing receipt, non-stop completion, or an
    unrelated exception on a valid plan never authorizes another model call.
    """
    calls = [call for call in runtime.store.get(state["run_id"])["calls"] if call["stage"] == stage]
    if len(calls) != 1:
        return None
    call = calls[0]
    if (call.get("status") != "terminal" or call.get("finished") is None
            or (call.get("backend_diagnostics") or {}).get("native_completion") in {"unknown", "not_started_unknown"}):
        return None
    path = runtime.store.artifact(state["run_id"], stage, "raw")
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not settled_group_response(raw):
        return None
    choices = raw.get("choices")
    if (not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict)
            or choices[0].get("finish_reason") != "stop"):
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str) or len(content.encode("utf-8")) > 32768:
        return None
    feedback = {"rejected_response_digest": digest(raw)}
    try:
        value = _strict_json(runtime._content(raw))
    except ValueError as error:
        feedback["validation_error"] = public_error(error)
        if content.strip():
            feedback["rejected_content"] = content
        else:
            feedback["final_content_missing"] = True
        return feedback
    try:
        validate_group_plan(value, declared)
    except ValueError as error:
        feedback.update(validation_error=public_error(error), rejected_plan=value)
        return feedback
    return None


def group_scope_feedback(raw, artifact_type, declared):
    """Derive scope feedback only from a normally completed, otherwise valid subset."""
    if not settled_group_response(raw):
        return None
    choices = raw.get("choices")
    if (not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict)
            or choices[0].get("finish_reason") != "stop"):
        return None
    try:
        artifact = artifact_type.model_validate(_strict_json(choices[0]["message"]["content"]))
    except (KeyError, TypeError, ValueError):
        return None
    returned = {file.path for file in artifact.files}
    expected = set(declared)
    if not returned < expected:
        return None
    return {"expected_paths": sorted(expected), "missing_paths": sorted(expected - returned),
            "rejected_response_digest": digest(raw)}


def terminal_group_scope_feedback(runtime, state, stage, artifact_type, declared):
    run_id = state["run_id"]
    calls = [call for call in runtime.store.get(run_id)["calls"] if call["stage"] == stage]
    if len(calls) != 1:
        return None
    call = calls[0]
    if (call["role"] != "implementer" or call["status"] != "terminal" or call.get("finished") is None
            or call.get("backend_diagnostics", {}).get("native_completion") in {"unknown", "not_started_unknown"}):
        return None
    return group_scope_feedback(runtime.store.cached(run_id, stage), artifact_type, declared)


PROJECT_TEST_BRIEF = TEST_BRIEF.replace(
    "Build verifies Python software; describe its public interface in assumptions so another model can implement it.",
    "The application may use a non-Python stack. Author Python unittest black-box acceptance tests against "
    "the application API and frontend. Set commands to a one-item list containing the exact Python "
    "unittest discovery command. Run every frontend check inside a discoverable Python unittest case. "
    "For Vitest checks, have the Python case invoke the image's declared Vitest entry point with "
    "subprocess and assert a successful exit status. The trusted image may "
    "bootstrap the application before tests. Describe precise application paths and interfaces in assumptions. "
    "A frontend build alone does not verify UI behavior; use executable DOM checks when the image supplies "
    "their dependencies, and state any UI behavior not tested. Keep the independent test source within "
    f"{DEFAULT_TEST_BYTES} UTF-8 bytes and 15 files. Declare ALL implementation paths, up to 128, each small enough "
    "for a 24576-byte implementation group. Do not include binary "
    "files, dependency caches or lockfiles already provisioned by the image. "
    "unittest creates a fresh instance for every test method: never depend on self.token or other "
    "instance assignments made by an earlier test. Independently initialize class credentials in "
    "setUpClass (write cls.token, using a separate unique fixture user), or authenticate per case in setUp. "
    "HTTP helpers must read response bytes before JSON decoding and return None for an empty 204 body; "
    "malformed nonempty JSON must still fail. Test methods must not depend on execution order. "
    "Cases may share an application/database: create unique fixtures and compare your own fixture IDs, "
    "never assume global collections or reports are empty or your row is first. Missing-resource checks "
    "on protected endpoints must send a valid JWT. UI unittest helpers must use correctly bound self/cls "
    "variables. Invoke npm only if it is declared. Use only declared available tools.")

LEGACY_DOM_TEST_BRIEF = (
    " When the image provides Vitest/jsdom for frontend/, use a temporary Vitest configuration "
    "with root=APP_PROJECT_DIR/frontend, jsdom environment and globals enabled; resolve imports against "
    "that root using absolute frontend source paths in temporary tests (root does not retarget relative "
    "imports). Merge the application's Vite config to preserve its Vue plugin and path aliases. In Vitest "
    "set test.server.deps.inline to include Vuetify if its components import CSS. Register the framework "
    "plugins the mounted component needs, using a fresh store and router instance where applicable. "
    "Invoke the declared node executable with the supplied tool's absolute JS entry point, such as "
    "/opt/frontend/node_modules/vitest/vitest.mjs for Vitest."
)


class ProjectBuildRun(BuildRun):
    artifact_type = ProjectArtifact
    project_type = ProjectProject
    suite_type = ProjectTestSuite
    test_brief = PROJECT_TEST_BRIEF
    fixture_repair_constraints = (
        " Keep every test method's complete syntax tree and every assertion exactly unchanged, including "
        "calls, expected values, decorators and control flow. Change only the diagnosed setup/helper bodies. "
        "You may add setUp or a classmethod setUpClass where absent, but no other methods, imports, class "
        "attributes, skips or executable top-level statements. Initialize separate fixture credentials "
        "without editing test cases or their users. Do not add, remove or rewrite assertions."
        " For an embedded_js_braces diagnostic, the only permitted change inside its test method is "
        "doubling the diagnosed literal JavaScript object's opening and closing braces. Preserve its "
        "JavaScript, other interpolations, and all assertions."
    )

    def __init__(self, runtime, state):
        super().__init__(runtime, state)
        project_enabled(self.bundle)
        self.contract = {**self.bundle["build_contract"], "group_bytes": group_artifact_type(self.bundle).byte_limit}
        self.reviewed = self.contract["profile"] == REVIEWED_POLICY
        self.suite_type, self.project_type = project_test_types(self.bundle)
        test_bytes = self.contract["test_bytes"]
        if test_bytes != DEFAULT_TEST_BYTES:
            self.test_brief = self.test_brief.replace(f"{DEFAULT_TEST_BYTES} UTF-8 bytes", f"{test_bytes} UTF-8 bytes", 1)
        self.repair_invalid_test_references = self.reviewed
        self.opencode_worker = getattr(runtime, "opencode_worker", None)
        if self.opencode_worker is not None or "project_worker" in self.bundle:
            self._check_worker_binding(self.opencode_worker, "project_worker")
        self.opencode_reviewer = getattr(runtime, "opencode_reviewer", None)
        if self.opencode_reviewer is not None or "project_reviewer" in self.bundle:
            self._check_worker_binding(self.opencode_reviewer, "project_reviewer")
            settings = (self.bundle.get("role_profiles") or self.bundle["generation"])["reviewer"]
            if not self.reviewed or settings.get("context") != 65536 or settings.get("think") is not True:
                raise ValueError("OpenCode review requires the reviewed project profile with 65536 context and thinking enabled")
        self.limits = {**self.limits, "max_test_fixture_repairs": self.contract["max_test_fixture_repairs"],
                       "max_test_defect_repairs": self.contract.get("max_test_defect_repairs", 0)}
        from .project_reference import validate_reference
        self.reference = validate_reference(self.bundle.get("project_reference"))
        self._observed_seconds = {}
        self.reference_test_repair_feedback = {}
        self.reference_test_repair_notes = self._reference_repair_notes()
        self.brief["execution_contract"] = copy.deepcopy(self.contract["sandbox_protocol"])
        from .project_environment import frontend_dom_test_instructions, validate_project_environment
        self.environment = validate_project_environment(self.bundle)
        self.frontend_dom_test_brief = frontend_dom_test_instructions(self.environment)
        self.test_brief += self.frontend_dom_test_brief or LEGACY_DOM_TEST_BRIEF
        if self.environment is not None:
            self.brief["project_environment"] = copy.deepcopy(self.environment)
            self.brief["environment_instructions"] = (
                "Use the operator's declared image capabilities. required_application_files must all be "
                "declared and implemented. python_modules/tools inventories are authoritative when non-null; "
                "null means unverified, not available. An api_root base URL already includes path_prefix; "
                "append endpoint-relative paths without repeating it. frontend_http_server describes an "
                "already running server, not whether offline frontend build/DOM tools are available.")
        self.output["build_contract"] = copy.deepcopy(self.contract)
        self.output["verification"]["policy"] = self.contract["verification_policy"]
        self.output["verification"]["mutation_verification"] = "not_run"
        if self.reviewed:
            self.output["verification"].update(test_preflights=[], code_reviews=[])
            self.test_brief += (
                " First map every numbered criterion to the smallest set of observable cases. "
                "Target at most 12288 UTF-8 bytes across all source files, leaving "
                f"{test_bytes - 12288} bytes below "
                "the hard limit for later coverage additions. Put shared HTTP, authentication and "
                "resource setup in reusable helpers rather than duplicating them across modules. "
                "Use compact data tables or subtests for repeated CRUD and validation cases. "
                "Keep real assertions for every criterion and omit verbose comments and docstrings. "
                "Put unittest cases in tests/test_*.py, for example tests/test_api.py. Importable helper modules "
                "may live under tests/ and must not be cited as coverage. Import test-local modules relative to "
                "the unittest discovery root. For tests/helpers.py, write `from helpers import name`. "
                "Each coverage entry must use the "
                "exact Class.test_method name from its file, without #case suffixes or other labels. "
                "Use absolute URLs built from the declared API environment for urllib requests. "
                "On ranked reports, find your own rows by fixture ID before checking values or order. "
                "Put embedded multiline JavaScript in raw triple-quoted Python strings and insert "
                "dynamic paths with JSON encoding. Set a timeout on frontend test subprocesses. "
                "Cover invalid input and failure recovery when the brief calls for them. For counters or "
                "other shared updates, send overlapping requests and check the final stored value. "
                "For UI criteria, exercise actual routes, state and interactions using the supported tools. "
                "A DOM-only check cannot demonstrate element position, visibility behind another element, "
                "or responsive layout. Report any browser behavior the available environment cannot measure."
            )
        self.compile_checkpoints = (self.environment or {}).get("compile_checkpoints", [])
        if self.compile_checkpoints:
            self.output["verification"]["compile_checkpoints"] = []
            self.output["compile_repair_attempts"] = 0

    def ask(self, stage, role, model_type, instruction, **data):
        """Ask a model; a lower-tier repair cut off at its output cap escalates once in the same attempt."""
        if self.reviewed and model_type is BuildTestCoverageRepair and "addition_feedback" in data:
            data = self.fit_addition_feedback(role, instruction, data)
        try:
            return self._timed_ask(stage, role, model_type, instruction, **data)
        except ValueError as error:
            tier = repair_tier(self.bundle, role, stage)
            tiers = self.contract.get("repair_generation") or []
            if tier is None or not self._ended_at_output_cap(stage):
                raise
            if tier >= len(tiers):
                raise OutputCapReached(stage) from error
        escalated = stage + ESCALATED
        record = self.stages.setdefault(stage, {"role": role, "model": self.bundle["roles"][role]})
        record.update(output_cap_reached=True, escalated_to=escalated)
        try:
            value = self._timed_ask(escalated, role, model_type, instruction, **data)
        except ValueError as error:
            if self._ended_at_output_cap(escalated):
                raise OutputCapReached(escalated) from error
            raise
        self.stages[stage].setdefault("escalated_to", escalated)
        self.stage = stage
        return value

    def fit_addition_feedback(self, role, instruction, data):
        payload = build_model_payload(BuildTestCoverageRepair, instruction, self.model_inputs(data))
        try:
            validate_project_prompt(payload, self.bundle, role)
        except ValueError:
            # The rejected draft is retained in its raw receipt. Keep the immutable
            # source and the validation error in the prompt when it cannot also fit.
            feedback = data["addition_feedback"]
            rejected = feedback["rejected_addition"]
            compact = {key: value for key, value in feedback.items() if key != "rejected_addition"}
            if isinstance(rejected, dict) and isinstance(rejected.get("files"), list):
                paths = [file["path"] for file in rejected["files"]
                         if isinstance(file, dict) and isinstance(file.get("path"), str)]
                compact["rejected_paths"] = paths[:15]
            return {**data, "addition_feedback": compact}
        return data

    def _timed_ask(self, stage, role, model_type, instruction, **data):
        started = time.monotonic()
        try:
            return super().ask(stage, role, model_type, instruction, **data)
        finally:
            if role == "repairer":
                think = str(call_generation(self.bundle, role, stage).get("think"))
                self._observed_seconds[think] = max(self._observed_seconds.get(think, 0),
                                                    time.monotonic() - started)

    def _timed_check(self, kind, run):
        started = time.monotonic()
        try:
            return run()
        finally:
            self._observed_seconds[kind] = max(self._observed_seconds.get(kind, 0), time.monotonic() - started)

    def _ended_at_output_cap(self, stage):
        try:
            path = self.runtime.store.artifact(self.state["run_id"], stage, "raw")
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw["choices"][0]["finish_reason"] == "length"
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return False

    def repair_time_available(self, stage, check="verification"):
        """True when the run has time for this repair and the check after it.

        Each estimate is the longest duration observed so far in this run for the
        same repair tier or check kind, never below the contract floor, so a run
        stops cleanly with its candidate instead of expiring inside a repair.
        """
        floors = self.contract.get("repair_time_floor_seconds")
        deadline = getattr(self.runtime, "deadline", 0)
        if not floors or not deadline:
            return True
        if deadline == math.inf:
            self.output.setdefault("repair_time_checks", []).append({
                "stage": stage, "remaining_seconds": None, "no_overall_deadline": True, "allowed": True})
            return True
        think = str(call_generation(self.bundle, "repairer", stage).get("think"))
        repair = max(floors.get(think, max(floors.values())), self._observed_seconds.get(think, 0))
        verify = max(self.contract["check_time_floor_seconds"][check], self._observed_seconds.get(check, 0))
        remaining = deadline - time.monotonic()
        self.output.setdefault("repair_time_checks", []).append({
            "stage": stage, "think": think, "estimate_seconds": round(repair + verify),
            "remaining_seconds": round(remaining), "allowed": remaining >= repair + verify})
        return remaining >= repair + verify

    def control(self, suite):
        """Run the suite against the harness-only reference; never exposed to any model."""
        if self.reference is None:
            return super().control(suite)
        number = len(self.output["verification"]["controls"])
        self.stage = f"build_control_{number}"
        reference = self.artifact_type(summary="Trusted reference", files=self.reference["files"])
        # The reference implements the public contract with its own source layout.
        project = self.project_type(summary=reference.summary, files=[*reference.files, *suite.files])
        result = self.runtime._build_execution(self.state, self.stage, 100 + number, project, suite)
        self.output["verification"]["controls"].append({
            "stage": self.stage, "reference_digest": self.reference["digest"], "status": result.get("status"),
            "receipt_digest": digest(result),
        })
        requirements = (self.environment or {}).get("verification") if self.reviewed else None
        if requirements:
            summaries = [summary for row in result.get("commands", [])
                         if (summary := sandbox_stage_results(row)) is not None]
            checks = (summaries[0].get("checks") if summaries else None) or {}
            required = [name for name in requirements["required_checks"] if name != "python-unittest"]
            missing = sorted(name for name in required if name not in checks)
            broken = sorted(name for name in required
                            if name in checks and (checks.get(name) or {}).get("status") != "passed")
            if summaries and missing:
                raise ValueError("The pinned image does not report required checks " + ", ".join(missing)
                                 + "; rebuild the image or update the project environment")
            if summaries and broken:
                raise ValueError("The trusted reference fails required image checks " + ", ".join(broken)
                                 + "; fix the reference or the checks before generating code")
        return result

    def test_defects(self, suite):
        from .project_test_lint import defects
        return defects(suite)

    def repair_test_defects(self, suite, defects):
        return self.rewrite_tests(suite, defects, (
            "Static review found these generated tests cannot discriminate a correct implementation "
            "from an incorrect one."))

    def repair_reference_failures(self, suite, problems, result):
        excerpts = unittest_failure_excerpts(result)
        diagnostics = [{**problem, "failures": [{"test": name.rsplit(".", 1)[-1],
                                                  "excerpt": excerpts.get(name.rsplit(".", 1)[-1], "")}
                                                 for name in problem["names"]]}
                       for problem in problems]
        if self.counters["build_tests_defect"] >= self.limits["max_test_defect_repairs"]:
            return self.quarantine_reference_failures(suite, diagnostics)
        policy = self.bundle.get("reference_test_repair")
        if policy in {SCOPED_REFERENCE_TEST_REPAIR, PATH_BOUND_REFERENCE_TEST_REPAIR, REFERENCE_TEST_REPAIR}:
            return self._repair_reference_test_files(suite, diagnostics, policy)
        return self.rewrite_tests(suite, diagnostics, (
            "A hidden trusted implementation of the specification failed these tests in the pinned image. "
            "Treat each failure as a test defect: a wrong expectation, a fixture that conflicts with the "
            "specification (for example a reused unique value) or a broken helper."))

    def _repair_reference_test_files(self, suite, diagnostics, policy):
        from .build_coverage import TestSyntaxError
        from .project_environment import frontend_dom_repair_instructions

        bind_path = policy != SCOPED_REFERENCE_TEST_REPAIR
        stage = self.bounded("build_tests_defect", "max_test_defect_repairs")
        instruction = (
            "Repair the named test failures observed against a trusted implementation. Return one complete "
            "replacement for the supplied test file. Correct each named failure and the helpers it relies on. "
            "Preserve all other test methods, names and coverage. Keep real, discriminating HTTP or DOM assertions "
            "and never skip a case or make a required action optional. Derive expectations from the public contract. "
            "For HTTP tests, require the expected status and response shape before reading fields or recording fixtures. "
            "Use unique fixtures and do not depend on objects made by earlier cases. Treat tokens and generated IDs "
            "as opaque values. Verify each issued credential by using it on a protected operation. In shared result "
            "lists, check guaranteed ordering across the actual list, then find this test's independently created rows "
            "by identity and check their values. Do not assume fixture rows occupy absolute positions."
            + self.frontend_dom_test_brief + frontend_dom_repair_instructions(self.environment)
        )
        if bind_path:
            instruction += (
                " When the public brief specifies Material Design Icons, row actions may be icon-only. "
                "Find the fixture row by an exact rendered cell value, prefer a labelled action there, then "
                "use the row's rendered pencil or delete icon when present. Exercise the actual dialog and "
                "verify the row changes after save or confirmation. When public_reference_observations are supplied, "
                "use them to replace each specific failing interaction or assertion and rework any shared helpers "
                "that block the corrected flow. Match equivalent rendered controls and keep the public behavior assertions."
            )
        for index, failure in enumerate(sorted(diagnostics, key=lambda item: item["path"]), 1):
            call_stage = f"{stage}_file_{index}"
            path = failure["path"]
            original = next(file for file in suite.files if file.path == path)
            budget = self.contract["test_bytes"] - sum(
                len(file.content.encode("utf-8")) for file in suite.files if file.path != path)
            extra = {}
            if feedback := self.reference_test_repair_feedback.get(path):
                extra["previous_rejection"] = feedback
            if notes := self.reference_test_repair_notes.get(path):
                extra["public_reference_observations"] = notes
            replacement_type = reference_test_replacement_type(path) if bind_path else BuildTestSyntaxRepair
            raw = self.ask(call_stage, "test_author", replacement_type, instruction,
                           files=[original.model_dump()], diagnostics=[failure], required_tests=failure["names"],
                           max_replacement_utf8_bytes=budget, **extra)
            self.stages[call_stage]["defects"] = [{"path": path, "names": failure["names"]}]
            try:
                replacement, = replacement_type.model_validate(raw).files
                try:
                    validate_reference_test_replacement(original, replacement, failure["names"], budget)
                except TestSyntaxError as error:
                    if policy != REFERENCE_TEST_REPAIR:
                        raise
                    corrected = remove_unmatched_test_trailer(replacement, error.diagnostics)
                    if corrected is None:
                        raise
                    validate_reference_test_replacement(original, corrected, failure["names"], budget)
                    self.stages[call_stage]["syntax_correction"] = {
                        "diagnostic": error.diagnostics[0], "original_digest": digest(replacement.content),
                        "corrected_digest": digest(corrected.content),
                    }
                    replacement = corrected
                suite = self.suite_type.model_validate({**suite.model_dump(), "files": [
                    replacement.model_dump() if file.path == path else file.model_dump() for file in suite.files]})
            except (ValueError, SyntaxError) as error:
                rejection = public_error(error)
                self.stages[call_stage]["replacement_rejected"] = rejection
                self.reference_test_repair_feedback[path] = (
                    {"message": rejection, "diagnostics": error.diagnostics}
                    if policy == REFERENCE_TEST_REPAIR and isinstance(error, TestSyntaxError) else rejection)
            else:
                self.reference_test_repair_feedback.pop(path, None)
        return suite

    def _reference_repair_notes(self):
        value = self.bundle.get("reference_test_repair_observations")
        if value is None:
            return {}
        seed = self.bundle.get("whole_project_seed") or {}
        provenance = seed.get("provenance", {})
        repair = seed.get("independent_test_repair", {})
        paths = {file["path"] for file in repair.get("suite", {}).get("files", [])}
        if (self.bundle.get("reference_test_repair") not in {PATH_BOUND_REFERENCE_TEST_REPAIR, REFERENCE_TEST_REPAIR}
                or not isinstance(value, dict)
                or value.keys() != {"source_run_id", "reference_failure_digest", "by_file"}
                or value["source_run_id"] != provenance.get("source_run_id")
                or value["reference_failure_digest"] != provenance.get("source_reference_failure_digest")
                or not isinstance(value["by_file"], dict) or not value["by_file"] or value["by_file"].keys() - paths
                or any(not isinstance(note, str) or not note.strip() for note in value["by_file"].values())):
            raise ValueError("Public reference observations must match the approved saved test-repair checkpoint")
        return value["by_file"]

    def quarantine_reference_failures(self, suite, diagnostics):
        """Drop the few tests the trusted reference still fails after every rewrite, and say so.

        A test that a correct implementation fails cannot judge a candidate. Removing it costs
        coverage, never correctness: the removed tests and any criteria left without a test are
        recorded as unverified, and the remaining suite must still pass the reference to freeze.
        """
        limit = self.contract.get("max_quarantined_tests", 0) if self.reviewed else 0
        failing = [(d["path"], name) for d in diagnostics for name in d["names"]]
        total = sum(len(names) for names in python_test_names_for(suite).values())
        if (not limit or self.output["verification"].get("quarantined")
                or len(failing) > limit or len(failing) * 4 > total):
            raise ValueError(
                f"build_tests_defect repair budget exhausted; {len(failing)} of {total} tests still fail "
                f"the trusted reference (quarantine allows at most {limit}, once, and no more than a "
                "quarter of the suite)")
        removed = {(path, name) for path, name in failing}
        files = []
        for file in suite.files:
            names = {name for path, name in removed if path == file.path}
            files.append({**file.model_dump(), "content": remove_test_methods(file.content, names)}
                         if names else file.model_dump())
        kept = [entry for entry in suite.coverage
                if not any(entry.test_file == path and same_test(entry.test_name, name) for path, name in removed)]
        if not kept:
            raise ValueError("build_tests_defect repair budget exhausted; quarantine would leave no covered criterion")
        dropped = [entry for entry in suite.coverage if entry not in kept]
        orphaned = {entry.criterion for entry in dropped} - {entry.criterion for entry in kept}
        excerpts = {(d["path"], f["test"]): f.get("excerpt", "") for d in diagnostics for f in d.get("failures", [])}
        record = [{"path": path, "test": name,
                   "criteria": sorted({e.criterion for e in dropped if e.test_file == path and same_test(e.test_name, name)}),
                   "reference_failure": excerpts.get((path, name.rsplit(".", 1)[-1]), "")[:600]}
                  for path, name in sorted(removed)]
        self.output["verification"]["quarantined"] = record
        self.unverified_criteria = frozenset(self.unverified_criteria | orphaned)
        self.output["verification"]["unverified_criteria"] = sorted(self.unverified_criteria)
        self.stages["build_tests_quarantine"] = {"tests": [{k: r[k] for k in ("path", "test", "criteria")} for r in record],
                                                 "unverified_criteria": sorted(self.unverified_criteria)}
        merged = suite.model_dump()
        merged.update(files=files, coverage=[entry.model_dump() for entry in kept])
        return self.suite_type.model_validate(merged)

    def rewrite_tests(self, suite, diagnostics, reason):
        """Let the test author rewrite named test methods, then re-run the full freeze checks."""
        from .build_coverage import TestSyntaxError, python_test_names

        paths = {d["path"] for d in diagnostics}
        selected = [f.model_dump() for f in suite.files if f.path in paths]
        replacement_budget = self.contract["test_bytes"] - sum(
            len(file.content.encode("utf-8")) for file in suite.files if file.path not in paths)
        instruction = (
            reason + " Rewrite only the named test methods and the helpers they use, in exactly the named "
            "files. You may change their setup, requests, fixtures and assertions so each one checks the "
            "specified behavior through real HTTP requests or real DOM rendering, with expectations taken "
            "only from the specification. Keep every existing test method name and leave other tests "
            "unchanged. Never turn a test into a placeholder, an always-true assertion or a skipped case. "
            "Return complete corrected files for exactly those paths. The replacement files together must "
            "fit max_replacement_utf8_bytes. Keep shared setup compact and leave the other files unchanged."
            + self.frontend_dom_test_brief)
        feedback = {"max_replacement_utf8_bytes": replacement_budget}
        while True:
            stage = self.bounded("build_tests_defect", "max_test_defect_repairs")
            replacements = BuildTestSyntaxRepair.model_validate(self.ask(
                stage, "test_author", BuildTestSyntaxRepair, instruction,
                files=selected, diagnostics=diagnostics, **feedback,
                required_tests=[c.test_name for c in suite.coverage if c.test_file in paths]))
            actual = sum(len(file.content.encode("utf-8")) for file in replacements.files)
            if actual <= replacement_budget:
                break
            self.stages[stage]["suite_size_failure"] = {
                "observed_utf8_bytes": actual, "max_utf8_bytes": replacement_budget}
            feedback["previous_replacement_utf8_bytes"] = actual
        changed = {f.path: f for f in replacements.files}
        if set(changed) != paths or len(changed) != len(replacements.files):
            raise ValueError("Test defect repair must return exactly the indicated test files")
        before = python_test_names({f.path: f.content for f in suite.files if f.path in paths})
        self.stages[stage]["defects"] = [{key: d.get(key) for key in ("path", "line", "names", "code")}
                                         for d in diagnostics]
        merged = suite.model_dump()
        merged["files"] = [changed.get(f.path, f).model_dump() for f in suite.files]
        revised = self.suite_type.model_validate(merged)
        while True:
            try:
                after = python_test_names({f.path: f.content for f in revised.files if f.path in paths})
            except TestSyntaxError as error:
                self.stages[stage].setdefault("syntax_handoff", {"diagnostics": error.diagnostics})
                revised = self.replace_broken(revised, error.diagnostics)
                continue
            if any(not names <= after.get(path, set()) for path, names in before.items()):
                raise ValueError("Test defect repair removed or renamed a test method")
            return revised

    def fixture_problems(self, suite):
        from .project_fixture_checks import diagnostics
        if self.reviewed:
            self.compile_test_suite(suite)
        python_version = None if self.reviewed else (self.environment or {}).get("python_version")
        return super().fixture_problems(suite) + diagnostics(suite, python_version=python_version)

    def replace_broken(self, suite, diagnostics, fixture=False):
        if (fixture or not self.reviewed or "whole_project_workflow" in self.bundle
                or "mechanical_test_repair_generation" not in self.contract):
            return super().replace_broken(suite, diagnostics, fixture)
        inputs = test_syntax_repair_inputs(suite, diagnostics)
        if inputs is None:
            return super().replace_broken(suite, diagnostics, fixture)
        stage = self.bounded("build_tests_syntax", "max_test_syntax_repairs")
        self.stage = stage
        instruction = (
            "Correct the Python syntax diagnostics with minimal exact-text edits. Return an edits array "
            "with path, before and after. Copy each before anchor verbatim from a provided snippet, "
            "using only enough context for a unique match. Make independent edits disjoint. Preserve "
            "all test and class names, assertions and intended behavior. For embedded TypeScript, "
            "preserve the emitted JavaScript while correcting Python string quoting or brace escapes. "
            "Return only the edit object and its short summary.")
        if any("f-string" in diagnostic["message"] for diagnostic in diagnostics):
            instruction += (
                " In an f-string containing JavaScript, check literal braces throughout the snippet. "
                "For example, `if (ready) {` becomes `if (ready) {{`, and its closing `}` becomes `}}`. "
                "Keep existing brace escapes and Python interpolations. Quotes inside a triple-quoted "
                "string are usually valid. The reported line can be inside an unmatched brace pair."
            )
        value = self.runtime._build_model(self.state, stage, "test_author", SelectedTextRepair,
                                          instruction, inputs)
        self.stages[stage] = {"role": "test_author", "model": self.bundle["roles"]["test_author"],
                              "patch_format": EXACT_REPAIR_FORMAT,
                              "diagnostics": diagnostics}
        return apply_test_syntax_repair(suite, inputs, value)

    def addition_brief(self, suite):
        if not self.reviewed:
            return super().addition_brief(suite)
        occupied = {item.path.casefold() for item in suite.files} | {
            path.casefold() for path in suite.implementation_files
        }
        suffix = next(index for index in range(1, len(occupied) + 2)
                      if f"tests/test_coverage_extra_{index}.py" not in occupied)
        budget = {
            "max_utf8_bytes": self.contract["test_bytes"] - sum(
                len(item.content.encode("utf-8")) for item in suite.files
            ),
            "max_files": self.contract["test_files"] - len(suite.files),
            "suggested_path": f"tests/test_coverage_extra_{suffix}.py",
        }
        return (
            "Write compact black-box tests for the missing criteria. Derive expected behavior from "
            "the specification and keep success fixtures valid. Prefer the unused test file in "
            "addition_budget.suggested_path. Total new source must fit addition_budget.max_utf8_bytes "
            "and addition_budget.max_files, which are the remaining limits after the existing draft. "
            "Leave margin below the byte limit. Reuse importable setup from the draft when appropriate "
            "and keep executable assertions for each requested criterion. For Node tools, use the declared "
            "node executable and the tool's supplied absolute JS entry point. Invoke npm only if declared."
            + self.frontend_dom_test_brief,
            {"addition_budget": budget},
        )

    def compile_test_suite(self, suite):
        from .build_coverage import TestSyntaxError
        from .build_sandbox import final_json_record

        results = self.output["verification"]["test_preflights"]
        index = len(results)
        self.stage = f"build_test_preflight_{index}"
        run_id = self.state["run_id"]
        image = self.bundle["sandbox"]["image_id"]
        manifest = self.suite_type.manifest_type(summary="Independent tests for syntax preflight", files=suite.files)
        binding = {"image_id": image, "manifest": digest(manifest.model_dump()),
                   "policy": self.contract["test_preflight_policy"]}

        def execute():
            executor = project_executor(self.runtime, self.state)
            return executor.preflight_tests(run_id, 5000 + index, manifest, image,
                                            self.runtime.events[run_id], self.runtime.deadline)

        receipt = self.runtime._build_receipt(run_id, self.stage, binding, execute)
        rows = receipt.get("commands") or []
        row = rows[0] if len(rows) == 1 else {}
        result = final_json_record(row.get("stderr", ""), "WORKBENCH_TEST_COMPILE")
        if (not result or row.get("cleanup_confirmed") is not True or row.get("cancelled")
                or row.get("timed_out") or row.get("exit_code") not in (0, 1)):
            raise ValueError("The pinned image did not produce a conclusive test compilation receipt")
        version = result.get("python_version")
        expected = (self.environment or {}).get("python_version")
        paths = {f.path for f in suite.files}
        problems = result.get("diagnostics")
        if (not isinstance(version, str) or (expected and expected != version)
                or not isinstance(problems, list) or bool(problems) != (row["exit_code"] == 1)
                or any(not isinstance(item, dict) or item.get("path") not in paths
                       or type(item.get("line")) is not int or item["line"] < 1
                       or not isinstance(item.get("message"), str) for item in problems)):
            raise ValueError("Test compilation receipt does not match the approved suite or runtime")
        results.append({**binding, **result, "status": "failed" if problems else "passed",
                        "receipt_digest": digest(receipt)})
        if problems:
            raise TestSyntaxError(problems)

    def validate_suite(self, suite):
        from .project_environment import validate_suite_environment
        validate_suite_environment(suite, self.environment)

    def validate_addition(self, suite, addition):
        if not self.reviewed:
            return
        from .project_environment import suite_environment_issues
        new_paths = {file.path for file in addition.files}
        issues = [issue for issue in suite_environment_issues(suite, self.environment) if issue["path"] in new_paths]
        if issues:
            detail = "; ".join(f"{issue['path']}:{issue.get('line', 0)}: {issue['message']}" for issue in issues[:8])
            raise ValueError("Coverage addition conflicts with the pinned project environment: " + detail)

    def validate_fixture_repair(self, original_files, revised_files, diagnostics):
        from .project_fixture_checks import validate_repair
        super().validate_fixture_repair(original_files, revised_files, diagnostics)
        validate_repair(original_files, revised_files, diagnostics)

    def grouping(self, suite):
        reused = getattr(self, "reuse", None)
        if reused is not None and reused.group_plan is not None:
            plan = reused.group_plan
            frozen = plan.model_dump()
            self.output["group_plan"] = frozen
            self.stages["build_project_frozen"] = {
                "digest": digest(frozen), "group_plan": frozen,
                "test_suite_digest": self.output["suite_digest"],
                "validation_failures": [], "format_repairs": 0,
                "reused_from": reused.provenance["source"],
            }
            self.runtime.store.save_artifact(self.state["run_id"], "build_project_frozen", "raw",
                                             self.stages["build_project_frozen"])
            return plan
        instruction = (
            "Partition ALL declared implementation paths into ordered groups of at most 16 files. "
            f"Each group's COMPLETE source output must fit {self.contract['group_bytes']} UTF-8 bytes. Use small cohesive groups; "
            "split large modules in the independent interface before freezing it. Define a precise global "
            "cross-file API contract and each group's interface. depends_on names earlier group IDs only; "
            "context_files selects only existing files from those dependencies whose combined source will "
            f"fit {self.contract['context_bytes']} bytes. Every selected context file's direct owning group MUST appear in depends_on, "
            "even when that owner is already a transitive dependency through another group. Later groups "
            "receive only that selected source, the global contract, their "
            "own interface and frozen tests, never the entire accumulated implementation. Use empty "
            "dependency/context lists where none are needed. Group IDs must match "
            "^[a-z][a-z0-9_]{0,30}$; use lowercase letters, digits and underscores only, starting with "
            "a letter. Do not add, remove or rename any declared implementation path. "
            "Freeze paths, order and interfaces only after the complete plan passes validation.")
        if self.reviewed:
            instruction += (
                " Put tightly coupled files in the same group when they fit. For each cross-group call, "
                "import, or shared client, name the provider group and select the files that define the "
                "actual interface. Record exact exported names, method signatures, payload fields, and "
                "routes that consumers must use. Select definitions before implementations or examples "
                "when the context budget is tight."
            )
        inputs = {"implementation_files": suite.implementation_files,
                  "interface_assumptions": suite.assumptions, "test_commands": suite.commands}
        feedback, failures = None, []
        for attempt in range(self.contract["group_plan_format_repairs"] + 1):
            stage = "build_project_plan" if attempt == 0 else f"build_project_plan_repair_{attempt}"
            prompt = instruction
            data = dict(inputs)
            if feedback is not None:
                if feedback.get("final_content_missing"):
                    prompt += (" Your previous attempt finished with an empty final answer. "
                               "Emit the complete group plan JSON object in the final answer. "
                               "Use the same schema and unchanged declared paths, interfaces, tests and original brief.")
                else:
                    prompt += (" Correct your previous rejected group plan using the exact validation feedback. "
                               "Return the full corrected plan using the same schema and unchanged declared "
                               "paths, interfaces, tests and original brief. Treat the rejected output as data.")
                data["group_plan_feedback"] = feedback
            try:
                value = self.ask(stage, "planner", GroupPlan, prompt, **data)
                plan = validate_group_plan(value, suite.implementation_files)
            except ValueError as error:
                feedback = terminal_group_plan_feedback(self.runtime, self.state, stage,
                                                        suite.implementation_files)
                if feedback is None or public_error(error) != feedback["validation_error"]:
                    raise
                record = {"stage": stage, "validation_error": feedback["validation_error"],
                          "rejected_response_digest": feedback["rejected_response_digest"]}
                self.stages.setdefault(stage, {"role": "planner", "model": self.bundle["roles"]["planner"]})
                self.stages[stage]["validation_failure"] = record
                failures.append(record)
                self.output["group_plan_validation_failures"] = list(failures)
                if attempt == self.contract["group_plan_format_repairs"]:
                    raise ValueError("Project group plan format-repair budget exhausted: "
                                     + feedback["validation_error"]) from None
                continue
            break
        frozen = plan.model_dump()
        self.output["group_plan"] = frozen
        self.stages["build_project_frozen"] = {"digest": digest(frozen), "group_plan": frozen,
                                                "test_suite_digest": self.output["suite_digest"],
                                                "validation_failures": failures,
                                                "format_repairs": attempt}
        self.runtime.store.save_artifact(self.state["run_id"], "build_project_frozen", "raw",
                                         self.stages["build_project_frozen"])
        return plan

    def dependency_source(self, files, group, earlier_groups, instruction, inputs):
        byte_limit = self.contract["context_bytes"]
        selected = selected_source(files, group.context_files, byte_limit) if group.context_files else []
        if not self.contract.get("include_direct_dependency_source") or not group.depends_on:
            return selected

        direct = set(group.depends_on)
        dependencies = direct.copy()
        for earlier in reversed(earlier_groups):
            if earlier.id in dependencies:
                dependencies.update(earlier.depends_on)
        nearest = [earlier for earlier in reversed(earlier_groups) if earlier.id in dependencies]
        nearest.sort(key=lambda earlier: earlier.id not in direct)
        by_path = {item.path: item for item in files}
        # Keep the planner's selection, then add dependency files with shared contracts first
        # (DTOs, types, API clients, stores, routers) while the context budget allows.
        context = list(selected)
        source_bytes = sum(len(item["content"].encode("utf-8")) for item in context)
        chosen = {item["path"] for item in context}
        for dependency in nearest:
            for path in interface_first(dependency.files):
                if path in chosen:
                    continue
                item = by_path[path]
                size = len(item.content.encode("utf-8"))
                if source_bytes + size > byte_limit:
                    continue
                candidate = [*context, item.model_dump()]
                payload = build_model_payload(group_artifact_type(self.bundle), instruction, self.model_inputs({
                    **inputs, "selected_source": candidate,
                }))
                try:
                    validate_project_prompt(payload, self.bundle, "implementer")
                except ValueError:
                    continue
                context = candidate
                chosen.add(path)
                source_bytes += size
        return context

    def _check_worker_binding(self, worker, key):
        binding = self.bundle.get(key)
        identity = getattr(worker, "identity", None)
        if binding is None or not callable(identity) or identity() != binding:
            name = key.removeprefix("project_")
            raise ValueError(f"OpenCode {name} identity differs from the approved project {name}")

    def _worker_receipt(self, stage, worker, binding_key, request, dispatch, **arguments):
        run_id = self.state["run_id"]
        self.stage = stage

        def execute():
            runtime = self.runtime
            while not runtime.inference_lock.acquire(timeout=.1):
                runtime._guard(run_id)
            try:
                runtime._guard(run_id)
                if runtime.store.has_unknown_calls():
                    raise RuntimeError("native_completion_unknown; resolve the physical backend before another call")
                self._check_worker_binding(worker, binding_key)
                return dispatch(
                    stage=stage, **arguments,
                    cancel=runtime.events[run_id], deadline=runtime.deadline, scratch_parent=runtime.store.directory,
                )
            finally:
                runtime.inference_lock.release()

        result = self.runtime._build_receipt(run_id, stage, request, execute)
        if not isinstance(result, dict) or not isinstance(result.get("receipt"), dict):
            raise ValueError("OpenCode worker returned no execution receipt")
        self.stages[stage] = {"role": request["role"], "model": request["model"], "worker": "opencode",
                              "worker_receipt_digest": digest(result["receipt"])}
        return result

    def _worker_artifact(self, stage, role, artifact_type, instruction, inputs, files, paths, *, allow_create):
        model = self.bundle["roles"][role]
        settings = (self.bundle.get("role_profiles") or self.bundle["generation"])[role]
        inputs = self.model_inputs(inputs)
        visible_files = {file["path"]: file["content"] for file in files}
        request = {"worker": self.bundle["project_worker"], "role": role, "model": model,
                   "settings": settings, "instruction": instruction, "inputs": inputs,
                   "visible_files": {path: digest(content) for path, content in visible_files.items()},
                   "editable_paths": list(paths), "allow_create": allow_create}
        result = self._worker_receipt(
            stage, self.opencode_worker, "project_worker", request, self.opencode_worker.run,
            role=role, model=model, settings=settings, instruction=instruction, inputs=inputs,
            visible_files=visible_files, editable_paths=list(paths), allow_create=allow_create,
        )
        changes = result.get("files")
        if not isinstance(changes, dict) or not changes.keys() <= set(paths):
            raise ValueError("OpenCode worker returned files outside its approved scope")
        if not allow_create and any(visible_files[path] == content for path, content in changes.items()):
            raise ValueError("OpenCode worker returned an unchanged repair file")
        return artifact_type.model_validate({
            "summary": f"OpenCode {role} output",
            "files": [{"path": path, "content": changes[path]} for path in paths if path in changes],
        })

    def generate_group(self, plan, suite, index, group, files, interfaces):
        dependencies = {key: interfaces[key] for key in group.depends_on}
        if len(encoded(dependencies).encode("utf-8")) > 8192:
            raise ValueError("Dependency interfaces exceed the bounded group context")
        stage = f"implement_group_{index}"
        artifact_type = group_artifact_type(self.bundle)
        if self.opencode_worker is None:
            artifact_type = exact_group_artifact(artifact_type, tuple(group.files))
        instruction = (
            "Implement ONLY this frozen group's exact file paths. Use workspace tools to write complete source "
            "to each approved path. " if self.opencode_worker is not None else
            "Implement ONLY this frozen group's exact file paths, returning complete source for each. "
        )
        instruction += (
            f"The group's output limit is {artifact_type.byte_limit} UTF-8 bytes. Preserve the global and group interfaces; "
            "all earlier files and independent tests are immutable. Do not return other groups, "
            "tests, dependency caches, binary artifacts or a wrapper directory. Do not bypass tests."
        )
        if self.reviewed:
            instruction += (
                " Use idiomatic formatting and readable names within the byte limit. "
                "Check imports, method signatures, field names and routes against the supplied "
                "dependency interfaces and source before returning each file. Use the actual "
                "exports in selected_source and reuse shared clients and authentication helpers."
            )
        inputs = {"contract": plan.contract, "group": group.model_dump(),
                  "dependency_interfaces": dependencies, "frozen_tests": compact_suite(suite)}
        context = self.dependency_source(files, group, plan.groups[:index - 1], instruction, inputs)
        inputs["selected_source"] = context
        if self.opencode_worker is None:
            raw = self.ask(stage, "implementer", artifact_type, instruction, **inputs)
            artifact = artifact_type.model_validate(raw)
            if self.reviewed and {file.path for file in artifact.files} < set(group.files):
                feedback = terminal_group_scope_feedback(self.runtime, self.state, stage, artifact_type, group.files)
                if feedback is not None:
                    retry_stage = stage + "_scope_retry"
                    self.stages[stage]["scope_retry"] = {"stage": retry_stage, **feedback}
                    raw = self.ask(
                        retry_stage, "implementer", artifact_type,
                        instruction + " The previous response omitted some approved paths. Return the complete "
                        "replacement artifact with source for every expected path, including the missing paths. "
                        "Keep the original interfaces and source-byte limit.",
                        **inputs, implementation_scope_feedback=feedback,
                    )
                    artifact = artifact_type.model_validate(raw)
        else:
            artifact = self._worker_artifact(stage, "implementer", artifact_type, instruction, inputs,
                                             context, group.files, allow_create=True)
        if {f.path for f in artifact.files} != set(group.files):
            raise ValueError("Implementation group changed its frozen file set")
        if self.reviewed:
            self.stages[stage]["context_paths"] = [item["path"] for item in context]
        return artifact

    def compile_boundaries(self, plan):
        owners = {path: index for index, group in enumerate(plan.groups, 1) for path in group.files}
        boundaries = {}
        for slot, checkpoint in enumerate(self.compile_checkpoints, 1):
            prefix = checkpoint["source_root"] + "/"
            paths = {path for path in owners if path.startswith(prefix)}
            if not paths:
                raise ValueError("Compile checkpoint has no files in the frozen group plan")
            index = max(owners[path] for path in paths)
            boundaries.setdefault(index, []).append((slot, checkpoint, paths))
        return boundaries

    def compile_component(self, artifact, suite, plan, index, slot, checkpoint, paths, *,
                          repair_round=None, editable_paths=None, repair_intent=None, exact_edits=False,
                          repair_phase="repair", progress_limit=0, attempt_offset=0):
        if repair_phase not in {"repair", "self"} or repair_phase == "self" and repair_round not in range(1, 4):
            raise ValueError("Invalid compiler repair phase or self-repair round")
        run_id = self.state["run_id"]
        image = self.bundle["sandbox"]["image_id"]
        name = checkpoint["name"]
        policy = self.environment["verification"]["policy"]
        command = f"workbench-compile-checkpoint {name}"
        limit = self.contract["max_compile_repairs"]
        # Remaining attempts can differ by component on resume. Workspace slots
        # must use the original bound, not shrink into a neighboring component.
        stride = ((self.bundle.get("build_contract") or self.contract)["max_compile_repairs"] + 1
                  if getattr(self, "scoped_repairs", False) else limit + 1)
        if progress_limit or attempt_offset:
            from .compile_progress import additional_repairs
            stride += additional_repairs(self.bundle)
        if repair_round is None:
            checkpoint_stage, repair_stage, workspace_base = "build_compile_checkpoint", "build_compile_repair", 6000
            stage_slot = str(slot)
        else:
            prefix = "build_self_compile" if repair_phase == "self" else "build_repair_compile"
            checkpoint_stage, repair_stage = prefix + "_checkpoint", prefix + "_repair"
            stage_slot = f"{repair_round}_{slot}"
            workspace_base = (7100 if repair_phase == "self" else 6100) + repair_round * self.contract["max_compile_checkpoints"] * stride
        for local_attempt in range(limit + progress_limit + 1):
            attempt = attempt_offset + local_attempt
            stage = self.stage = f"{checkpoint_stage}_{stage_slot}_{attempt}"
            component = self.artifact_type(summary=f"Compile checkpoint {name}",
                                           files=[f for f in artifact.files if f.path in paths])
            workspace = workspace_base + slot * stride + attempt
            binding = {"image_id": image, "component_digest": digest(component.model_dump()),
                       "checkpoint": checkpoint, "group_index": index, "command": command}
            profile = checkpoint.get("verification_profile")
            options = {"verification_profile": profile} if profile is not None else {}

            def execute():
                executor = project_executor(self.runtime, self.state)
                project_path = executor.materialize(run_id, workspace, component)
                return executor.test(run_id, workspace, project_path, [command], image=image,
                                     cancel=self.runtime.events[run_id], deadline=self.runtime.deadline,
                                     read_only_project=True, **options)

            execution = self._timed_check("compile", lambda: self.runtime._build_receipt(run_id, stage, binding, execute))
            rows = execution.get("commands") or []
            summary = sandbox_stage_results(rows[0]) if len(rows) == 1 else None
            source_hashes = {f.path: hashlib.sha256(f.content.encode("utf-8")).hexdigest() for f in component.files}
            status = compile_checkpoint_status(execution, summary, checkpoint, policy,
                                               source_hashes=source_hashes, image_id=image)
            record = {"checkpoint": name, "group_index": index, "attempt": attempt, "status": status,
                      "stage": stage, "execution": execution, "stage_results": summary}
            scope = f"{checkpoint_stage}_{stage_slot}"
            if progress_limit or attempt_offset:
                record["compile_scope"] = scope
            if repair_round is not None:
                record["repair_round"] = repair_round
                if repair_phase == "self":
                    record["repair_phase"] = repair_phase
            self.output["verification"]["compile_checkpoints"].append(record)
            if getattr(self, 'scoped_repairs', False):
                # A failed initial compile previously discarded the current draft
                # from terminal output. Preserve it as unverified evidence.
                self.output['artifact'] = artifact.model_dump()
            self.stages[stage] = {"status": status, "component_digest": binding["component_digest"],
                                  "checkpoint": name, "group_index": index}
            if status == "passed":
                if getattr(self, 'recovery', None) is not None:
                    self.recovery.verified(stage, execution)
                return artifact
            source_request = None
            if (status == 'review_required' and local_attempt < limit
                    and hasattr(self, 'diagnose_execution') and self.repair_time_available(
                        f'{repair_stage}_{stage_slot}_{attempt + 1}', check='compile')):
                source_request = self.diagnose_execution(component, None, execution, checkpoint=checkpoint)
            progress = None
            frontend_details = None
            if status == 'failed' and getattr(self, 'scoped_repairs', False) and local_attempt < limit + progress_limit:
                from .frontend_diagnostics import inspect_failure as inspect_frontend
                frontend_details = inspect_frontend(self, stage + '_diagnostics', component,
                    {'failed_execution': failure_feedback(execution, include_stage_results=True)})
                if frontend_details is not None:
                    record['compiler_diagnostics'] = frontend_details
            dependency_evidence = None
            if status == 'failed' and getattr(self, 'scoped_repairs', False) and local_attempt < limit + progress_limit:
                from .compiler_api import inspect_failure
                dependency_evidence = inspect_failure(self, stage + '_api', component,
                    {'failed_execution': failure_feedback(execution, include_stage_results=True)})
                if dependency_evidence is not None:
                    record['compiler_dependency_evidence'] = dependency_evidence
            if status == "failed" and local_attempt >= limit and local_attempt < limit + progress_limit:
                from .compile_progress import progress_proof
                history = [r for r in self.output["verification"]["compile_checkpoints"]
                           if r.get("compile_scope") == scope]
                progress = progress_proof(history, component.files, checkpoint, policy, image,
                    recovery_policy=self.bundle.get('whole_project_compile_progress_policy'))
                record["progress_recovery"] = progress
                if progress is None and dependency_evidence is not None:
                    from .compiler_api import dependency_recovery_proof
                    progress = dependency_recovery_proof(history, dependency_evidence)
                    if progress is not None:
                        record['dependency_recovery'] = progress
            if (status != "failed" and source_request is None) or local_attempt >= limit and progress is None:
                message = ("Compiler errors remain after the checkpoint repair budget"
                           if status == "failed" else "Compile checkpoint did not produce a conclusive compiler receipt")
                self.output["error"] = {"stage": stage, "message": f"{name}: {message}"}
                return None

            repair_name = f"{repair_stage}_{stage_slot}_{attempt + 1}"
            if not self.repair_time_available(repair_name, check="compile"):
                self.output["error"] = {"stage": stage, "message": f"{name}: Not enough run time remains for "
                                        "another compile repair and verification"}
                return None
            self.output["compile_repair_attempts"] += 1
            feedback = {"compile_checkpoint": name,
                        "failed_execution": failure_feedback(execution, include_stage_results=True)}
            if dependency_evidence is not None:
                feedback['compiler_dependency_evidence'] = dependency_evidence
            if frontend_details is not None:
                feedback['compiler_diagnostics'] = frontend_details
            if source_request is not None:
                record['source_recovery_request'] = source_request
                feedback['stage_recovery_request'] = source_request
            if progress is not None:
                self.output["compile_progress_repair_attempts"] = self.output.get("compile_progress_repair_attempts", 0) + 1
                feedback["compile_progress_recovery"] = progress
            if repair_intent is not None:
                feedback["repair_intent"] = repair_intent
            try:
                repaired = self.repair_selected(
                    repair_name, component, suite, plan,
                    f"compiler failure in the {name} component", feedback, editable_paths=editable_paths,
                    exact_edits=exact_edits,
                )
            except OutputCapReached as capped:
                self.output.setdefault("capped_repairs", []).append(capped.stage)
                if local_attempt + 1 >= limit:
                    self.output["error"] = {"stage": capped.stage, "message": f"{name}: The last compile repair "
                                            "reached its output cap; compiler errors remain"}
                    return None
                continue  # the next attempt rechecks the unchanged component and repairs again
            artifact = replace_selected(artifact, repaired, paths, artifact_type=self.artifact_type)

    def compile_after_repair(self, previous, artifact, suite, grouping, selected, focus, *, exact_edits=False):
        before = {item.path: item.content for item in previous.files}
        changed = {item.path for item in artifact.files if item.content != before[item.path]}
        if not changed:
            return artifact
        for index, checkpoints in self.compile_boundaries(grouping).items():
            for slot, checkpoint, paths in checkpoints:
                if changed & paths:
                    intent = {"changed_paths": sorted(changed & paths)}
                    if focus is not None:
                        intent.update(focus)
                    artifact = self.compile_component(
                        artifact, suite, grouping, index, slot, checkpoint, paths,
                        repair_round=self.output["repair_attempts"], editable_paths=paths & selected,
                        repair_intent=intent, exact_edits=exact_edits,
                    )
                    if artifact is None:
                        return None
        return artifact

    def implement_groups(self, plan, suite):
        files = []
        interfaces = {g.id: g.interface for g in plan.groups}
        reused = getattr(self, "reuse", None)
        boundaries = self.compile_boundaries(plan)
        for index, group in enumerate(plan.groups, 1):
            stage = f"implement_group_{index}"
            if reused is not None and index <= len(reused.groups):
                artifact = reused.groups[index - 1]
                self.stages[stage] = {
                    "role": "implementer", "model": self.bundle["roles"]["implementer"],
                    "reused_from": reused.provenance["source"],
                }
            else:
                artifact = self.generate_group(plan, suite, index, group, files, interfaces)
            files.extend(artifact.files)
            accumulated = ProjectArtifact(summary="Incremental offline project", files=files)
            for slot, checkpoint, paths in boundaries.get(index, []):
                accumulated = self.compile_component(accumulated, suite, plan, index, slot, checkpoint, paths)
                if accumulated is None:
                    return None
            files = accumulated.files
            accumulated = ProjectArtifact(summary="Incremental offline project", files=files)
            self.stages[stage]["accumulated_digest"] = digest(accumulated.model_dump())
            self.stages[stage]["group_id"] = group.id
            self.runtime.store.save_artifact(self.state["run_id"], f"project_after_{index}",
                                             "raw", accumulated.model_dump())
        return ProjectArtifact(summary="Complete offline project", files=files)

    def _review_projection(self, chunk, project, plan):
        by_path = {file.path: file for file in project.files}
        paths = {file.path for file in chunk}
        groups = [group for group in plan.groups if paths.intersection(group.files)]
        related = {group.id for group in groups} | {key for group in groups for key in group.depends_on}
        projected = dict.fromkeys([
            *[file.path for file in chunk],
            *[file.path for file in project.files if file.path.startswith("tests/")],
            *[path for group in plan.groups if group.id in related or related.intersection(group.depends_on)
              for path in group.files],
            *by_path,
        ])
        visible = {}
        budget = self.contract["implementation_bytes"]
        for path in projected:
            file = by_path[path]
            size = len(file.content.encode("utf-8"))
            if size <= budget:
                visible[path] = file.content
                budget -= size
            if len(visible) == self.contract["implementation_files"]:
                break
        if not paths <= visible.keys():
            raise ValueError("The complete review chunk does not fit the OpenCode source projection")
        return visible

    def _worker_review(self, stage, settings, payload, visible_files):
        model = self.bundle["roles"]["reviewer"]
        request = {"worker": self.bundle["project_reviewer"], "role": "reviewer", "model": model,
                   "settings": settings, "payload": payload,
                   "visible_files": {path: digest(content) for path, content in visible_files.items()}}
        result = self._worker_receipt(
            stage, self.opencode_reviewer, "project_reviewer", request, self.opencode_reviewer.review,
            model=model, settings=settings, payload=payload, visible_files=visible_files,
        )
        if not isinstance(result.get("value"), dict):
            raise ValueError("OpenCode reviewer returned no structured review")
        return result["value"]

    def code_review(self, number, artifact, project, plan, baseline, *, tests_passed=False, previous_artifact=None):
        if self.bundle.get('project_review_policy') == 'source-anchors-v1':
            from .project_review_flow import review_project
            return review_project(self, number, artifact, project, plan, baseline,
                                  tests_passed=tests_passed, previous_artifact=previous_artifact)
        from .project_review import (ProjectCodeReview, REVIEW_BRIEF, ReviewCitationError, numbered_review_source,
                                     resolve_review_citations, review_chunks, style_findings)

        stage = f"build_code_review_{number}"
        self.stage = stage
        required = ((self.environment or {}).get("verification") or {}).get("required_checks", [])
        checks = baseline["sandbox_stage_results"][0]["checks"] if required else {}
        image_checks = {}
        for name in dict.fromkeys([*required, "browser"]):
            check = checks.get(name)
            status = check.get("status") if isinstance(check, dict) else None
            if isinstance(status, str) and status in {"passed", "failed", "not_run", "unavailable"}:
                image_checks[name] = status
        verification = {"status": "passed" if tests_passed else "inconclusive",
                        "image_checks": image_checks}
        settings = (self.bundle.get("role_profiles") or self.bundle["generation"])["reviewer"]
        previous_source = ({file.path: file.content for file in previous_artifact.files}
                           if self.opencode_reviewer is not None and previous_artifact is not None else {})

        def review_inputs(files):
            return {"contract": plan.contract, "implementation": numbered_review_source(files),
                    "verification": verification}

        def payload_fits(payload):
            try:
                tokens = validate_project_prompt(payload, self.bundle, "reviewer")
            except ValueError:
                return False
            return tokens + self.contract["review_output_reserve"] <= settings["context"]

        def fits(files, instruction=REVIEW_BRIEF):
            return payload_fits(build_model_payload(ProjectCodeReview, instruction, self.model_inputs(review_inputs(files))))

        chunks = review_chunks(artifact.files, fits)
        if len(chunks) > self.contract["max_review_chunks"]:
            raise ValueError("Complete source review exceeds the approved chunk budget")
        receipts, findings, validation_failures = [], [], []
        for index, files in enumerate(chunks, 1):
            chunk_stage = f"{stage}_chunk_{index}"
            instruction = REVIEW_BRIEF
            paths = [f.path for f in files]
            visible = self._review_projection(files, project, plan) if self.opencode_reviewer is not None else None
            diffs, remaining = [], 4096
            for file in files:
                if (before := previous_source.get(file.path)) is None or before == file.content or not remaining:
                    continue
                patch = "\n".join(unified_diff(before.splitlines(), file.content.splitlines(), n=1, lineterm=""))
                data = patch.encode("utf-8")
                diffs.append({"path": file.path, "diff": data[:remaining].decode("utf-8", errors="ignore"),
                              "truncated": len(data) > remaining})
                remaining -= min(len(data), remaining)
            grounded_findings = []
            for attempt in range(1 + tests_passed):
                call_stage = f"{chunk_stage}_citation_retry" if attempt else chunk_stage
                chunk = {"stage": call_stage, "paths": paths}
                inputs = review_inputs(files)
                if visible is None:
                    value = self.ask(call_stage, "reviewer", ProjectCodeReview, instruction, **inputs)
                else:
                    payload = build_model_payload(ProjectCodeReview, instruction, self.model_inputs(inputs))
                    if diffs:
                        enhanced = build_model_payload(ProjectCodeReview, instruction,
                                                       self.model_inputs({**inputs, "changes_since_prior_review": diffs}))
                        if payload_fits(enhanced):
                            payload = enhanced
                    value = self._worker_review(call_stage, settings, payload, visible)
                    chunk["worker_receipt_digest"] = self.stages[call_stage]["worker_receipt_digest"]
                try:
                    result, corrections = resolve_review_citations(value, files)
                except ValueError as error:
                    failure = {**chunk, "validation_error": public_error(error)}
                    self.stages[call_stage]["validation_failure"] = failure
                    if not attempt and tests_passed and isinstance(error, ReviewCitationError):
                        instruction = (REVIEW_BRIEF + " Re-review this complete source chunk. Your prior review "
                                       f"had a citation error: {failure['validation_error']}. Verify each path, line, "
                                       "and exact source quote before returning the complete findings.")
                        if fits(files, instruction):
                            grounded_findings = error.validated_findings
                            continue
                    validation_failures.append(failure)
                else:
                    if corrections:
                        chunk["citation_corrections"] = corrections
                    if attempt:
                        chunk["retry_of"] = chunk_stage
                        chunk["findings_retained_from_initial_review"] = len(grounded_findings)
                    receipts.append(chunk)
                    findings.extend(f.model_dump() for f in grounded_findings)
                    findings.extend(f.model_dump() for f in result.findings if f not in grounded_findings)
                break
        findings.sort(key=lambda f: (f["severity"], f["path"], f["line"]))
        blockers = sum(f["severity"] in self.contract["review_blocking_severities"] for f in findings)
        status = "changes_required" if blockers else "passed"
        result = {
            "policy": self.contract["code_review_policy"],
            "status": "incomplete" if validation_failures else status,
            "implementation_digest": digest(artifact.model_dump()),
            "project_digest": digest(project.model_dump()),
            "chunks": receipts,
            "findings": findings,
            "blocking_findings": blockers,
            "style_findings": [f.model_dump() for f in style_findings(artifact.files)],
        }
        if validation_failures:
            result["validation_failures"] = validation_failures
        self.runtime.store.save_artifact(self.state["run_id"], stage, "raw", result)
        self.stages[stage] = {"status": result["status"], "digest": digest(result)}
        self.output["verification"]["code_reviews"].append(result)
        return result

    def repair_within_budget(self, artifact, suite, baseline, plan, review, focus):
        """Repair once; a capped top-tier attempt is spent and the next starts from the same evidence.

        The code is unchanged after a capped attempt, so verification is not repeated. Returns None,
        with the reason in output["error"], when attempts or run time run out first.
        """
        while True:
            try:
                return self.repair(artifact, suite, baseline, plan, review, focus=focus)
            except OutputCapReached as capped:
                self.output.setdefault("capped_repairs", []).append(capped.stage)
                message = None
                if self.output["repair_attempts"] >= self.limits["max_repair_attempts"]:
                    message = "Every remaining repair attempt reached its output cap"
                elif not self.repair_time_available(f"repair_{self.counters['repair'] + 1}"):
                    message = "A repair reached its output cap and not enough run time remains for another"
                if message:
                    self.output["error"] = {"stage": capped.stage, "message": message}
                    return None

    def repair(self, artifact, suite, baseline, plan, review=None, *, focus=None):
        stage = self.bounded("repair", "max_repair_attempts")
        self.output["repair_attempts"] += 1
        feedback = {} if baseline is None else {
            "failed_execution": failure_feedback(baseline, include_stage_results=self.reviewed)
        }
        if focus is not None:
            feedback["failed_execution"] = {"full_failure_receipt_digest": digest(baseline)}
            feedback.update(focus)
        if review is not None and (not self.reviewed or baseline is None):
            blockers = [f for f in review["findings"]
                        if f["severity"] in self.contract["review_blocking_severities"]]
            if blockers:
                feedback["independent_code_review"] = {
                    "findings": blockers[:self.contract["max_review_repair_findings"]],
                    "blocking_findings": len(blockers), "full_review_digest": digest(review),
                }
        problem = "failed test execution" if baseline is not None else "independent code review findings"
        selection = None
        scope = getattr(self.runtime, "verification_repair_scope", None)
        if (self.reviewed and scope is not None and focus is not None and baseline is not None
                and baseline.get("sandbox", {}).get("image_id") == self.bundle["sandbox"]["image_id"]):
            target = focus["repair_target"]
            if target.get("control_match") is True and (paths := scope(target, artifact.files)):
                selection = control_repair_selection(target, paths)
        return self.repair_selected(stage, artifact, suite, plan, problem, feedback, control_selection=selection)

    def repair_selected(self, stage, artifact, suite, plan, problem, feedback, *,
                        editable_paths=None, control_selection=None, exact_edits=False):
        diagnostic_source = repair_diagnostic_source(
            artifact.files, feedback.get("repair_target", {}).get("source_paths", []),
            self.contract["context_bytes"] // 2,
        )
        focus_instruction = (
            "Repair only repair_target. Pending verification failures are context and must stay open until rechecked. "
            if "repair_target" in feedback else ""
        )
        if "previous_target_observation" in feedback:
            focus_instruction += (
                "The previous target observation predates this verification. Resolve the current failure "
                "while preserving the original expected behavior. "
            )
        if "repair_intent" in feedback:
            focus_instruction = (
                "Fix the compiler diagnostics only. Preserve the behavior intended by repair_intent. "
                "Use only paths in the supplied editable file inventory. "
            )
        selection_proof = None
        patch_feedback = feedback
        if control_selection is None:
            tree = [{"path": f.path, "bytes": len(f.content.encode("utf-8")),
                     "sha256": hashlib.sha256(f.content.encode("utf-8")).hexdigest()}
                    for f in artifact.files if editable_paths is None or f.path in editable_paths]
            if not tree:
                raise ValueError("No existing implementation files are available for repair")
            selection_type = scoped_repair_selection(tuple(item["path"] for item in tree))
            selection_feedback = {**feedback, "diagnostic_source": diagnostic_source} if diagnostic_source else feedback
            selection = selection_type.model_validate(self.ask(stage + "_select", "repairer", selection_type,
                f"Diagnose this {problem} and select the smallest set of existing implementation files to repair. "
                + focus_instruction +
                "Select at most 16 files with combined source at most 32768 UTF-8 bytes. Use the tree's byte "
                "counts and select only paths in that inventory. Tests, file paths and interfaces "
                "are frozen. Do not select tests or create paths. Return diagnosis and selected paths only.",
                contract=plan.contract, implementation_tree=tree,
                frozen_tests=compact_suite(suite), **selection_feedback))
            selection = selection.model_copy(update={"files": list(dict.fromkeys(selection.files))})
        else:
            selection = control_selection
            selection_proof = control_repair_proof(artifact, feedback["repair_target"], selection)
            focus_instruction += ("The verifier adapter fixed the editable scope. Follow the verified repair guidance. "
                                  "Use diagnostic_source only as read-only reference. ")
            patch_feedback = {**feedback, "diagnostic_source": [file for file in diagnostic_source
                                                               if file["path"] not in selection.files]}
        if editable_paths is not None and not set(selection.files) <= editable_paths:
            raise ValueError("A compiler repair selected paths outside the preceding repair scope")
        source = selected_source(artifact.files, selection.files, self.contract["repair_selected_bytes"])
        if selection_proof is not None:
            self.runtime.store.save_artifact(self.state["run_id"], stage + "_select", "raw", selection_proof)
        exact_edits = (control_selection is not None or exact_edits) and (
            self.bundle.get("project_repair_format") == EXACT_REPAIR_FORMAT
        )
        inputs = {"contract": plan.contract, "diagnosis": selection.diagnosis.model_dump(),
                  "selected_source": source, "frozen_tests": compact_suite(suite), **patch_feedback}
        if self.opencode_worker is not None:
            instruction = (
                focus_instruction + "Use workspace tools to make the smallest necessary changes in the selected "
                "implementation files. Preserve unrelated behavior and existing interfaces. Treat all other "
                "files as read-only. Do not create, delete, or rename any files. Never weaken validation, change "
                "tests, detect a test runner, or special-case fixtures."
            )
            visible = [*source, *patch_feedback.get("diagnostic_source", [])]
            replacement = self._worker_artifact(stage, "repairer", RepairArtifact, instruction, inputs,
                                                visible, selection.files, allow_create=False)
            diagnosis = selection.diagnosis
            self.stages[stage]["patch_format"] = OPENCODE_REPAIR_FORMAT
        elif exact_edits:
            instruction = (focus_instruction + "Return a short summary and only the needed exact text edits. "
                           "Copy each before block verbatim from the original selected_source, including its whitespace. "
                           "Each before block must match once, and edits must be disjoint. Use a short unique surrounding "
                           "block for an insertion. Omit unchanged code and preserve the rest of each file. "
                           "Use at most 12 edits and 12288 UTF-8 bytes across all before and after blocks. "
                           "All other implementation files and all independent tests are immutable. Honor the approved "
                           "cross-file API contract. Never weaken validation, change tests, detect a test runner, "
                           "or special-case fixtures.")
            replacement = self.text_repair(stage, artifact, selection.files, instruction, inputs)
            diagnosis = selection.diagnosis
        else:
            replacement_scope = ("Return a diagnosis and complete replacements for the selected files you change. "
                                 "Omit selected files that should stay unchanged. " if self.reviewed else
                                 "Return a diagnosis and complete replacements for EXACTLY the selected implementation paths. ")
            instruction = (focus_instruction + replacement_scope
                           + "All other implementation files and all independent tests are immutable. Selected replacement "
                           "source must fit 32768 UTF-8 bytes total. Honor the approved cross-file API contract. Never "
                           "weaken validation, change tests, detect a test runner, or special-case fixtures.")
            repaired = SelectedRepair.model_validate(self.ask(stage, "repairer", SelectedRepair, instruction, **inputs))
            replacement, diagnosis = repaired.artifact, repaired.diagnosis
        self.stages[stage]["diagnosis"] = diagnosis.model_dump()
        self.stages[stage]["selected_paths"] = selection.files
        self.stages[stage]["replaced_paths"] = [item.path for item in replacement.files]
        if "repair_target" in feedback:
            self.stages[stage]["repair_target"] = feedback["repair_target"]
        if selection_proof is not None:
            self.stages[stage]["selection_proof"] = selection_proof
        return replace_selected(artifact, replacement, selection.files, allow_subset=self.reviewed)

    def text_repair(self, stage, artifact, paths, instruction, inputs):
        inputs = {**inputs, "patch_format": EXACT_REPAIR_FORMAT}
        value = self.ask(stage, "repairer", SelectedTextRepair, instruction, **inputs)
        patch_stage = stage
        try:
            replacement = materialize_text_repair(artifact, paths, value)
        except ValueError as error:
            diagnostic = public_error(error)
            self.stages[stage]["patch_validation_error"] = diagnostic
            patch_stage = stage + "_edit_retry"
            value = self.ask(patch_stage, "repairer", SelectedTextRepair,
                             instruction + " Correct the edit validation error and return all required edits again.",
                             **inputs, repair_format_error=diagnostic)
            replacement = materialize_text_repair(artifact, paths, value)
        self.stages[stage].update(patch_format=EXACT_REPAIR_FORMAT, patch_stage=patch_stage)
        self.stage = stage
        return replacement

    def _initial_test_suite(self):
        stage = "build_tests"
        brief = self.test_brief
        if self.reviewed:
            brief += (" List only application paths as strings in implementation_files. "
                      "Put only the authored tests/ source in files.")
        raw = self.ask(stage, "test_author", self.suite_type, brief)
        declared = raw.get("implementation_files") if isinstance(raw, dict) else None
        value, fixes = normalized_manifest(raw, declared, recover_test_paths=self.reviewed)
        self.stages[stage]["manifest_corrections"] = fixes
        if self.reviewed:
            recovered = sorted(path for path in declared if path not in value["implementation_files"])
            if recovered:
                self.stages[stage]["recovered_test_paths"] = recovered
            value, renames = normalize_authored_test_paths(value)
            if renames:
                self.stages[stage]["filename_corrections"] = renames
        try:
            return self.suite_type.model_validate(value)
        except ValidationError as error:
            limit = self.contract["test_bytes"]
            details = error.errors(include_input=False, include_url=False)
            if (not self.reviewed or len(details) != 1 or details[0]["loc"]
                    or details[0]["msg"] != f"Value error, Build files exceed the {limit}-byte manifest bound"):
                raise
        source_bytes = sum(len(file["content"].encode("utf-8")) for file in value["files"])
        feedback = {"observed_utf8_bytes": source_bytes, "max_utf8_bytes": limit}
        self.stages[stage]["suite_size_repair"] = feedback
        replacements = BuildTestSyntaxRepair.model_validate(self.ask(
            "build_tests_retry", "test_author", BuildTestSyntaxRepair,
            "The independent test suite exceeds its UTF-8 source limit. Return complete source for "
            "exactly the same test files within the limit. Consolidate repeated setup and helper code. "
            "Preserve every coverage reference, scenario, test method and meaningful assertion. "
            "Keep HTTP and UI checks executable. Do not remove, skip or weaken tests.",
            files=value["files"], coverage=value["coverage"], suite_size=feedback,
        ))
        revised = {file.path: file.content for file in replacements.files}
        original = {file["path"]: file["content"] for file in value["files"]}
        if revised.keys() != original.keys() or len(revised) != len(replacements.files):
            raise ValueError("Suite size repair must return exactly the original test paths")
        from .build_coverage import python_test_names
        before, after = python_test_names(original), python_test_names(revised)
        if any(not names <= after[path] for path, names in before.items()):
            raise ValueError("Suite size repair removed or renamed a test method")
        return self.suite_type.model_validate({**value, "files": replacements.model_dump()["files"]})

    def prepare_test_suite(self):
        from .project_reuse import load_project_reuse
        self.reuse = load_project_reuse(self.bundle)
        if self.reuse is not None:
            if self.reuse.provenance["source"]["run_id"] == self.state["run_id"]:
                raise ValueError("Checkpoint reuse requires a fresh run and new approval")
            plan = self.reuse.plan
            self.stages["build_plan"] = {"role": "planner", "model": self.bundle["roles"]["planner"],
                                         "reused_from": self.reuse.provenance["source"]}
            self.output["reuse"] = self.reuse.provenance
        else:
            plan = BuildPlan.model_validate(self.ask("build_plan", "planner", BuildPlan,
                "Design a from-scratch offline application implementation plan. Preserve every requested "
                "behavior in observable numbered acceptance criteria. Use the specified stack and exact API "
                "and UI contracts. Implementation will use bounded ordered file groups, up to 128 files and "
                "131072 UTF-8 bytes total. Independent Python black-box tests are authored before source."))
        self.output["plan"] = plan.model_dump()
        if self.reuse is not None and self.reuse.suite is not None:
            suite = self.reuse.suite
            from .build_coverage import validate_test_coverage
            if self.reviewed and self.fixture_problems(suite):
                raise ValueError("Frozen checkpoint fixtures failed current preflight")
            if self.test_defects(suite):
                raise ValueError("Frozen checkpoint tests fail the generated-test lint; author a new suite")
            reference_result = self.control(suite)
            if reference_result is not None and not validate_baseline(reference_result, suite):
                raise ValueError("Frozen checkpoint tests fail against the trusted reference; author a new suite")
            self.output.update(suite=suite.model_dump(), suite_digest=digest(suite.model_dump()))
            self.stages["build_frozen"] = {"digest": self.output["suite_digest"],
                "coverage": validate_test_coverage(suite, plan.acceptance_criteria),
                "reference_corrections": [],
                "trusted_control": "passed" if reference_result is not None else "not_supplied",
                "reused_from": self.reuse.provenance["source"]}
        else:
            suite = self.check_suite(self._initial_test_suite())
        self.runtime.store.save_artifact(self.state["run_id"], "build_frozen", "raw", {
            **self.stages["build_frozen"], "plan_digest": digest(plan.model_dump()),
            "suite": suite.model_dump(), "suite_digest": self.output["suite_digest"],
        })
        return suite

    def execute(self):
        suite = self.prepare_test_suite()
        grouping = self.grouping(suite)
        artifact = self.implement_groups(grouping, suite)
        if artifact is None:
            return "review_required"
        return self.verify_and_repair(artifact, suite, grouping)

    def before_acceptance(self, artifact):
        """Return readiness and the candidate, or no candidate when acceptance must stop."""
        return True, artifact

    def verification_candidate(self, artifact, suite, baseline, report):
        """Allow an approved workflow to reject a regression before review or delivery."""
        return artifact

    def verify_and_repair(self, artifact, suite, grouping, *, prior_verification=()):
        """Verify an assembled implementation, review it, and apply bounded repairs."""
        requirements = (self.environment or {}).get("verification") if self.reviewed else None
        failures = VerificationFailures(requirements["required_checks"]) if requirements else None
        case_details = getattr(self.runtime, "verification_repair_cases", None)
        previously_reviewed = None

        def observe(receipt, summaries, stage):
            if failures is None or not execution_settled(receipt):
                return None
            summary = summaries[0]
            cases = case_details(summary) if case_details else {}
            return failures.observe(receipt, summary, stage, cases if isinstance(cases, dict) else {})

        for source, receipt in prior_verification:
            summaries = [result for row in receipt.get("commands", [])
                         if (result := sandbox_stage_results(row)) is not None]
            if (requirements and receipt.get("sandbox", {}).get("image_id") == self.bundle["sandbox"]["image_id"]
                    and matching_image_checks(receipt, summaries, requirements)):
                observe(receipt, summaries, source)

        while True:
            project = assemble(artifact, suite, self.project_type)
            number = len(self.output["verification"]["baselines"])
            self.stage = f"build_verify_{number}"
            baseline = self._timed_check("verification", lambda: self.runtime._build_execution(
                self.state, self.stage, number, project, suite))
            report = {"suite_digest": self.output["suite_digest"], **baseline}
            if self.reviewed:
                report["sandbox_stage_results"] = [
                    result for row in baseline.get("commands", []) if (result := sandbox_stage_results(row)) is not None
                ]
            self.output["verification"]["baselines"].append(report)
            if not matching_image_checks(baseline, report.get("sandbox_stage_results", []), requirements):
                self.output["error"] = {"stage": self.stage,
                                        "message": "Required image checks did not produce a matching receipt"}
                return "review_required"
            retained = self.verification_candidate(artifact, suite, baseline, report)
            if retained is not artifact:
                # The previous receipt belongs to the rejected candidate. Always execute
                # the restored source again; never credit it with another source's pass.
                artifact = retained
                continue
            focus = observe(baseline, report.get("sandbox_stage_results", []), self.stage)
            if failures is not None:
                self.output["verification"]["open_failures"] = list(failures.open.values())
            passed = validate_baseline(baseline, suite)
            if passed and getattr(self, 'recovery', None) is not None:
                self.recovery.verified(self.stage, baseline)
            if not passed and not actionable_failure(baseline):
                recovery_request = None
                if (hasattr(self, 'diagnose_execution') and self.output['repair_attempts'] < self.limits['max_repair_attempts']
                        and self.repair_time_available(f"repair_{self.counters['repair'] + 1}")):
                    recovery_request = self.diagnose_execution(artifact, suite, baseline)
                if recovery_request is not None:
                    self._verification_recovery_request = recovery_request
                else:
                    self.output['error'] = {'stage': self.stage, 'message': 'Inconclusive sandbox test execution'}
                    if self.reviewed and execution_settled(baseline) and getattr(self, 'recovery', None) is None:
                        self.code_review(number, artifact, project, grouping, report, previous_artifact=previously_reviewed)
                    return 'review_required'
            review = None
            if self.reviewed and passed:
                review = self.code_review(number, artifact, project, grouping, report, tests_passed=True,
                                          previous_artifact=previously_reviewed)
                if self.opencode_reviewer is not None:
                    previously_reviewed = artifact
            if passed and review and review["status"] == "incomplete":
                self.output["error"] = {"stage": f"build_code_review_{number}",
                                        "message": "Independent review did not validate every source chunk"}
                return "review_required"
            if not passed or (review and review["blocking_findings"]):
                if self.reviewed and self.output["repair_attempts"] >= self.limits["max_repair_attempts"]:
                    self.output["error"] = {"stage": self.stage, "message": "Unresolved verification or review findings"}
                    return "review_required"
                if not self.repair_time_available(f"repair_{self.counters['repair'] + 1}"):
                    self.output["error"] = {"stage": self.stage, "message": "Unresolved verification or review "
                                            "findings; not enough run time remains for another repair"}
                    return "review_required"
                previous = artifact
                artifact = self.repair_within_budget(artifact, suite, None if passed else baseline, grouping,
                                                     review, focus)
                if artifact is None:
                    return "review_required"
                if self.compile_checkpoints:
                    repair_stage = self.stages[self.stage]
                    artifact = self.compile_after_repair(
                        previous, artifact, suite, grouping, set(repair_stage["selected_paths"]), focus,
                        exact_edits=repair_stage.get("patch_format") in {EXACT_REPAIR_FORMAT, OPENCODE_REPAIR_FORMAT},
                    )
                    if artifact is None:
                        return "review_required"
                continue
            ready, artifact = self.before_acceptance(artifact)
            if not ready:
                if artifact is None:
                    return "review_required"
                continue
            self.stage = "build_project_acceptance"
            quality = self.runtime._build_quality(self.state, self.stage, project, suite, baseline)
            self.output["verification"]["strength"].append(quality)
            if quality["status"] != "passed":
                return "review_required"
            self.output["artifact"] = project.model_dump()
            if self.unverified_criteria:
                self.output["error"] = {
                    "stage": self.stage,
                    "message": "Independent coverage remains unverified for criteria "
                               + ", ".join(map(str, sorted(self.unverified_criteria))),
                }
                return "review_required"
            return "completed"
