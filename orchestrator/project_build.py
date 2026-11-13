"""Opt-in bounded, incremental offline projects; the ordinary Python Build is unchanged."""
from __future__ import annotations

import copy
from difflib import unified_diff
from functools import lru_cache
import hashlib
import json
from types import SimpleNamespace
from typing import ClassVar

from pydantic import Field, model_validator

from .build_discovery import normalize_authored_test_paths
from .build_models import (BuildArtifact, BuildFile, BuildPlan, ReviewedBuildTestSuite,
                           RepairAdvice, UnittestDiscoveryCommand)
from .build_sandbox import DockerBuildExecutor, validate_artifact
from .build_workflow import (BuildRun, TEST_BRIEF, actionable_failure, assemble, build_model_payload, compact_suite,
                             normalized_manifest, public_error, validate_baseline)
from .ollama import _strict_json
from .project_repair import VerificationFailures
from .repository import relative
from .schemas import Record
from .store import digest, encoded

POLICY = "offline-project-v1"
REVIEWED_POLICY = "offline-project-reviewed-v1"
CODE_REVIEW_POLICY = "independent-project-code-review-v1"
TEST_PREFLIGHT_POLICY = "image-python-test-compile-v1"
EXACT_REPAIR_FORMAT = "scoped-text-edits-v1"
OPENCODE_REPAIR_FORMAT = "opencode-worktree-v1"


def offline_project_contract(profile=POLICY):
    """Return a fresh approval-bound contract, never operator-editable numeric limits."""
    if profile not in (POLICY, REVIEWED_POLICY):
        raise ValueError("Unknown offline project profile")
    contract = {
        "profile": profile,
        "implementation_bytes": 131072, "implementation_files": 128,
        "test_bytes": 16384, "test_files": 15,
        "project_bytes": 163840, "project_files": 143,
        "group_bytes": 24576, "group_files": 16, "group_count": 32,
        "group_plan_format_repairs": 2,
        "test_fixture_policy": "unittest-http-fixtures-v1", "max_test_fixture_repairs": 2,
        "context_bytes": 24576, "repair_selected_bytes": 32768, "repair_files": 16,
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
            max_review_chunks=16,
            max_review_repair_findings=6,
            review_output_reserve=4096,
            review_blocking_severities=["P0", "P1"],
            sandbox_failure_stderr_bytes=32768,
            include_direct_dependency_source=True,
            max_compile_checkpoints=4,
            max_compile_repairs=2,
        )
    return contract


def project_enabled(bundle):
    profile = bundle.get("request", {}).get("build_profile")
    repair_format = bundle.get("project_repair_format")
    if repair_format is not None and (profile != REVIEWED_POLICY or repair_format != EXACT_REPAIR_FORMAT):
        raise ValueError("The scoped repair format requires the reviewed project profile")
    if profile is None:
        if bundle.get("build_contract") is not None or "implementation_group_bytes" in bundle:
            raise ValueError("A project contract requires its explicit Build profile")
        return False
    if (profile not in (POLICY, REVIEWED_POLICY) or bundle.get("mode", "build") != "build"
            or bundle.get("build_contract") != offline_project_contract(profile)
            or bundle.get("build", {}).get("offline_project_enabled") is not True):
        raise ValueError("Offline projects require operator enablement and the exact approved contract")
    group_artifact_type(bundle)
    roles = bundle.get("roles", {})
    if (not roles.get("repairer") or not roles.get("implementer") or not roles.get("test_author")
            or roles["test_author"] in {roles["implementer"], roles["repairer"]}):
        raise ValueError("Offline projects require an independent test author and configured repair role")
    if bundle.get("backend", {}).get("name") in {"strata", "llama-cpp"}:
        raise ValueError("Offline projects need a backend that can serve independent models for testing and implementation")
    if bundle.get("request", {}).get("control_files"):
        raise ValueError("Offline projects do not accept reference implementations")
    if profile == REVIEWED_POLICY:
        settings = (bundle.get("role_profiles") or bundle.get("generation") or {}).get("reviewer", {})
        if (not roles.get("reviewer") or roles["reviewer"] in {roles["implementer"], roles["repairer"]}
                or type(settings.get("context")) is not int or settings["context"] <= 0):
            raise ValueError("Reviewed projects require an independent reviewer and frozen reviewer context")
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


def validate_project_prompt(payload, bundle, role):
    """Preserve conservative admission, counting complete serialized source and schema.

    Transport JSON escaping is decoded before the model sees a message; the user
    message already contains the complete serialized stage data. Do not estimate
    source tokens or truncate a source/test/brief to admit a request.
    """
    settings = (bundle.get("role_profiles") or bundle.get("generation") or {}).get(role, {})
    context = settings.get("context")
    if type(context) is not int or context <= 0:
        raise ValueError("Offline projects require an explicit frozen context for every model role")
    required = (sum(len(m["content"].encode("utf-8")) for m in payload["messages"])
                + len(encoded(payload["schema"]).encode("utf-8")) + 2048)
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
    byte_limit: ClassVar[int] = 16384
    files: list[BuildFile] = Field(min_length=1, max_length=15)


class ProjectTestSuite(ReviewedBuildTestSuite):
    manifest_type: ClassVar[type[BuildArtifact]] = TestManifest
    implementation_files: list[str] = Field(min_length=1, max_length=128)
    commands: list[UnittestDiscoveryCommand] = Field(min_length=1, max_length=1)


class ProjectProject(BuildArtifact):
    byte_limit: ClassVar[int] = 163840
    implementation_type: ClassVar[type[ProjectArtifact]] = ProjectArtifact
    files: list[BuildFile] = Field(min_length=1, max_length=143)

    @model_validator(mode="after")
    def component_limits(self):
        validate_artifact(self)
        tests = [f for f in self.files if f.path.startswith("tests/")]
        implementation = [f for f in self.files if not f.path.startswith("tests/")]
        self.implementation_type(summary=self.summary, files=implementation)
        TestManifest(summary="Frozen independent tests", files=tests)
        return self


def validate_project_artifact(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return ProjectProject.model_validate(value)


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
    source = {file.path: file.content for file in artifact.files}
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
    return RepairArtifact(summary=patch.summary, files=files)


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


def compile_checkpoint_status(receipt, summary, checkpoint, policy):
    """Classify only verified compiler outcomes as eligible for repair."""
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


def terminal_group_plan_feedback(runtime, state, stage, declared):
    """A corrective call needs a finished normal response with a provably invalid plan.

    An admission/transport failure, missing receipt, non-stop completion, or an
    unrelated exception on a valid plan never authorizes another model call.
    """
    calls = [call for call in runtime.store.get(state["run_id"])["calls"] if call["stage"] == stage]
    if len(calls) != 1 or calls[0].get("status") != "terminal" or calls[0].get("finished") is None:
        return None
    path = runtime.store.artifact(state["run_id"], stage, "raw")
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    choices = raw.get("choices") if isinstance(raw, dict) else None
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
        value = _strict_json(content)
    except ValueError as error:
        feedback.update(validation_error=public_error(error), rejected_content=content)
        return feedback
    try:
        validate_group_plan(value, declared)
    except ValueError as error:
        feedback.update(validation_error=public_error(error), rejected_plan=value)
        return feedback
    return None


def group_scope_feedback(raw, artifact_type, declared):
    """Derive scope feedback only from a normally completed, otherwise valid subset."""
    if not isinstance(raw, dict) or raw.get("native_completion") == "unknown":
        return None
    native = raw.get("native") or {}
    if (not isinstance(native, dict) or native.get("terminal_state") not in {None, "stop"}
            or any(native.get(key) is False for key in
                   ("cleanup_confirmed", "native_done_ack", "handler_finished", "admission_released"))):
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
    "For Vitest checks, have the Python case invoke the image's installed Vitest with subprocess and "
    "assert a successful exit status. The trusted image may "
    "bootstrap the application before tests. Describe precise application paths and interfaces in assumptions. "
    "A frontend build alone does not verify UI behavior; use executable DOM checks when the image supplies "
    "their dependencies, and state any UI behavior not tested. Keep the independent test source within "
    "16384 UTF-8 bytes and 15 files. Declare ALL implementation paths, up to 128, each small enough "
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
    "variables. When the image provides Vitest/jsdom for frontend/, use a temporary Vitest configuration "
    "with root=APP_PROJECT_DIR/frontend, jsdom environment and globals enabled; resolve imports against "
    "that root using absolute frontend source paths in temporary tests (root does not retarget relative "
    "imports). Enable the supplied Vue SFC plugin when available and use only declared available tools.")


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
        self.opencode_worker = getattr(runtime, "opencode_worker", None)
        if self.opencode_worker is not None or "project_worker" in self.bundle:
            self._check_worker_binding(self.opencode_worker, "project_worker")
        self.opencode_reviewer = getattr(runtime, "opencode_reviewer", None)
        if self.opencode_reviewer is not None or "project_reviewer" in self.bundle:
            self._check_worker_binding(self.opencode_reviewer, "project_reviewer")
            settings = (self.bundle.get("role_profiles") or self.bundle["generation"])["reviewer"]
            if not self.reviewed or settings.get("context") != 65536 or settings.get("think") is not True:
                raise ValueError("OpenCode review requires the reviewed project profile with 65536 context and thinking enabled")
        self.limits = {**self.limits, "max_test_fixture_repairs": self.contract["max_test_fixture_repairs"]}
        self.brief["execution_contract"] = copy.deepcopy(self.contract["sandbox_protocol"])
        from .project_environment import validate_project_environment
        self.environment = validate_project_environment(self.bundle)
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
                "Target at most 12288 UTF-8 bytes across all source files, leaving 4096 bytes below "
                "the hard limit for later coverage additions. Put shared HTTP, authentication and "
                "resource setup in reusable helpers rather than duplicating them across modules. "
                "Use compact data tables or subtests for repeated CRUD and validation cases. "
                "Keep real assertions for every criterion and omit verbose comments and docstrings. "
                "Name every Python test module tests/test_*.py, for example tests/test_api.py. "
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

    def fixture_problems(self, suite):
        from .project_fixture_checks import diagnostics
        if self.reviewed:
            self.compile_test_suite(suite)
        python_version = None if self.reviewed else (self.environment or {}).get("python_version")
        return super().fixture_problems(suite) + diagnostics(suite, python_version=python_version)

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
            "and keep executable assertions for each requested criterion.",
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
        manifest = TestManifest(summary="Independent tests for syntax preflight", files=suite.files)
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
            "fit 24576 bytes. Every selected context file's direct owning group MUST appear in depends_on, "
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
        if group.context_files:
            return selected_source(files, group.context_files, byte_limit)
        if not self.contract.get("include_direct_dependency_source") or not group.depends_on:
            return []

        direct = set(group.depends_on)
        dependencies = direct.copy()
        for earlier in reversed(earlier_groups):
            if earlier.id in dependencies:
                dependencies.update(earlier.depends_on)
        nearest = [earlier for earlier in reversed(earlier_groups) if earlier.id in dependencies]
        nearest.sort(key=lambda earlier: earlier.id not in direct)
        by_path = {item.path: item for item in files}
        context, source_bytes = [], 0
        for dependency in nearest:
            for path in dependency.files:
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
                          repair_round=None, editable_paths=None, repair_intent=None, exact_edits=False):
        run_id = self.state["run_id"]
        image = self.bundle["sandbox"]["image_id"]
        name = checkpoint["name"]
        policy = self.environment["verification"]["policy"]
        command = f"workbench-compile-checkpoint {name}"
        limit = self.contract["max_compile_repairs"]
        if repair_round is None:
            checkpoint_stage, repair_stage, workspace_base = "build_compile_checkpoint", "build_compile_repair", 6000
            stage_slot = str(slot)
        else:
            checkpoint_stage, repair_stage = "build_repair_compile_checkpoint", "build_repair_compile_repair"
            stage_slot = f"{repair_round}_{slot}"
            workspace_base = 6100 + repair_round * self.contract["max_compile_checkpoints"] * (limit + 1)
        for attempt in range(limit + 1):
            stage = self.stage = f"{checkpoint_stage}_{stage_slot}_{attempt}"
            component = ProjectArtifact(summary=f"Compile checkpoint {name}",
                                        files=[f for f in artifact.files if f.path in paths])
            workspace = workspace_base + slot * (limit + 1) + attempt
            binding = {"image_id": image, "component_digest": digest(component.model_dump()),
                       "checkpoint": checkpoint, "group_index": index, "command": command}

            def execute():
                executor = project_executor(self.runtime, self.state)
                project_path = executor.materialize(run_id, workspace, component)
                return executor.test(run_id, workspace, project_path, [command], image=image,
                                     cancel=self.runtime.events[run_id], deadline=self.runtime.deadline,
                                     read_only_project=True)

            execution = self.runtime._build_receipt(run_id, stage, binding, execute)
            rows = execution.get("commands") or []
            summary = sandbox_stage_results(rows[0]) if len(rows) == 1 else None
            status = compile_checkpoint_status(execution, summary, checkpoint, policy)
            record = {"checkpoint": name, "group_index": index, "attempt": attempt, "status": status,
                      "stage": stage, "execution": execution, "stage_results": summary}
            if repair_round is not None:
                record["repair_round"] = repair_round
            self.output["verification"]["compile_checkpoints"].append(record)
            self.stages[stage] = {"status": status, "component_digest": binding["component_digest"],
                                  "checkpoint": name, "group_index": index}
            if status == "passed":
                return artifact
            if status != "failed" or attempt == limit:
                message = ("Compiler errors remain after the checkpoint repair budget"
                           if status == "failed" else "Compile checkpoint did not produce a conclusive compiler receipt")
                self.output["error"] = {"stage": stage, "message": f"{name}: {message}"}
                return None

            self.output["compile_repair_attempts"] += 1
            feedback = {"compile_checkpoint": name,
                        "failed_execution": failure_feedback(execution, include_stage_results=True)}
            if repair_intent is not None:
                feedback["repair_intent"] = repair_intent
            repaired = self.repair_selected(
                f"{repair_stage}_{stage_slot}_{attempt + 1}", component, suite, plan,
                f"compiler failure in the {name} component", feedback, editable_paths=editable_paths,
                exact_edits=exact_edits,
            )
            artifact = replace_selected(artifact, repaired, paths)

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
            selection_feedback = {**feedback, "diagnostic_source": diagnostic_source} if diagnostic_source else feedback
            selection = RepairSelection.model_validate(self.ask(stage + "_select", "repairer", RepairSelection,
                f"Diagnose this {problem} and select the smallest set of existing implementation files to repair. "
                + focus_instruction +
                "Select at most 16 files with combined source at most 32768 UTF-8 bytes. Use the tree's byte "
                "counts and select only paths in that inventory. Tests, file paths and interfaces "
                "are frozen. Do not select tests or create paths. Return diagnosis and selected paths only.",
                contract=plan.contract, implementation_tree=tree,
                frozen_tests=compact_suite(suite), **selection_feedback))
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

    def execute(self):
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
            self.output.update(suite=suite.model_dump(), suite_digest=digest(suite.model_dump()))
            self.stages["build_frozen"] = {"digest": self.output["suite_digest"],
                "coverage": validate_test_coverage(suite, plan.acceptance_criteria),
                "reference_corrections": [], "trusted_control": "not_supplied",
                "reused_from": self.reuse.provenance["source"]}
        else:
            raw = self.ask("build_tests", "test_author", ProjectTestSuite, self.test_brief)
            value, fixes = normalized_manifest(raw, raw.get("implementation_files") if isinstance(raw, dict) else None)
            self.stages["build_tests"]["manifest_corrections"] = fixes
            if self.reviewed:
                value, renames = normalize_authored_test_paths(value)
                if renames:
                    self.stages["build_tests"]["filename_corrections"] = renames
            suite = self.check_suite(ProjectTestSuite.model_validate(value))
        self.runtime.store.save_artifact(self.state["run_id"], "build_frozen", "raw", {
            **self.stages["build_frozen"], "plan_digest": digest(plan.model_dump()),
            "suite": suite.model_dump(), "suite_digest": self.output["suite_digest"],
        })
        grouping = self.grouping(suite)
        artifact = self.implement_groups(grouping, suite)
        if artifact is None:
            return "review_required"
        return self.verify_and_repair(artifact, suite, grouping)

    def before_acceptance(self, artifact):
        """Return readiness and the candidate, or no candidate when acceptance must stop."""
        return True, artifact

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
            baseline = self.runtime._build_execution(self.state, self.stage, number, project, suite)
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
            focus = observe(baseline, report.get("sandbox_stage_results", []), self.stage)
            if failures is not None:
                self.output["verification"]["open_failures"] = list(failures.open.values())
            passed = validate_baseline(baseline, suite)
            if not passed and not actionable_failure(baseline):
                self.output["error"] = {"stage": self.stage, "message": "Inconclusive sandbox test execution"}
                if self.reviewed and execution_settled(baseline):
                    self.code_review(number, artifact, project, grouping, report, previous_artifact=previously_reviewed)
                return "review_required"
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
                previous = artifact
                artifact = self.repair(artifact, suite, None if passed else baseline, grouping, review, focus=focus)
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
            return "completed"
