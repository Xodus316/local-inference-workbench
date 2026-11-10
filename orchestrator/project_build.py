"""Opt-in bounded, incremental offline projects; the ordinary Python Build is unchanged."""
from __future__ import annotations

import copy
import hashlib
from types import SimpleNamespace
from typing import ClassVar

from pydantic import Field, model_validator

from .build_models import (BuildArtifact, BuildFile, BuildPlan, ReviewedBuildTestSuite,
                           RepairAdvice)
from .build_sandbox import DockerBuildExecutor, validate_artifact
from .build_workflow import (BuildRun, TEST_BRIEF, actionable_failure, assemble, compact_suite,
                             normalized_manifest, validate_baseline)
from .repository import relative
from .schemas import Record
from .store import digest, encoded

POLICY = "offline-project-v1"


def offline_project_contract():
    """Return a fresh approval-bound contract, never operator-editable numeric limits."""
    return {
        "profile": POLICY,
        "implementation_bytes": 131072, "implementation_files": 128,
        "test_bytes": 16384, "test_files": 15,
        "project_bytes": 163840, "project_files": 143,
        "group_bytes": 24576, "group_files": 16, "group_count": 32,
        "context_bytes": 24576, "repair_selected_bytes": 32768, "repair_files": 16,
        "verification_policy": "unittest-acceptance-v1", "mutation_verification": "not_run",
        "failure_feedback_policy": "utf8-tails-with-full-receipt-digest-v1",
        "sandbox_tmpfs_mb": 2048, "sandbox_memory_mb": 6144,
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


def project_enabled(bundle):
    profile = bundle.get("request", {}).get("build_profile")
    if profile is None:
        if bundle.get("build_contract") is not None:
            raise ValueError("A project contract requires its explicit Build profile")
        return False
    if (profile != POLICY or bundle.get("mode", "build") != "build"
            or bundle.get("build_contract") != offline_project_contract()
            or bundle.get("build", {}).get("offline_project_enabled") is not True):
        raise ValueError("Offline projects require operator enablement and the exact approved contract")
    roles = bundle.get("roles", {})
    if (not roles.get("repairer") or not roles.get("implementer") or not roles.get("test_author")
            or roles["test_author"] in {roles["implementer"], roles["repairer"]}):
        raise ValueError("Offline projects require an independent test author and configured repair role")
    if bundle.get("request", {}).get("control_files"):
        raise ValueError("Offline projects do not accept reference implementations")
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


class ProjectProject(BuildArtifact):
    byte_limit: ClassVar[int] = 163840
    files: list[BuildFile] = Field(min_length=1, max_length=143)

    @model_validator(mode="after")
    def component_limits(self):
        validate_artifact(self)
        tests = [f for f in self.files if f.path.startswith("tests/")]
        implementation = [f for f in self.files if not f.path.startswith("tests/")]
        ProjectArtifact(summary=self.summary, files=implementation)
        TestManifest(summary="Frozen independent tests", files=tests)
        return self


def validate_project_artifact(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return ProjectProject.model_validate(value)


class GroupArtifact(ProjectArtifact):
    byte_limit: ClassVar[int] = 24576
    files: list[BuildFile] = Field(min_length=1, max_length=16)


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


class SelectedRepair(Record):
    diagnosis: RepairAdvice
    artifact: RepairArtifact


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
        if len(set(group.context_files)) != len(group.context_files) or not set(group.context_files) <= permitted_context:
            raise ValueError("Group context must select files from its declared earlier dependencies")
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


def replace_selected(current, replacement, paths):
    if {f.path for f in replacement.files} != set(paths):
        raise ValueError("A group or repair must return exactly its selected implementation paths")
    changes = {f.path: f for f in replacement.files}
    return ProjectArtifact(summary=replacement.summary,
                           files=[changes.get(f.path, f) for f in current.files])


def failure_feedback(receipt):
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
        commands.append(item)
    return {"feedback_policy": "utf8-tails-with-full-receipt-digest-v1",
            "full_failure_receipt_digest": digest(receipt), "commands": commands}


PROJECT_TEST_BRIEF = TEST_BRIEF.replace(
    "Build verifies Python software; describe its public interface in assumptions so another model can implement it.",
    "The application may use a non-Python stack. Author Python unittest black-box acceptance tests against "
    "the application API and offline frontend commands supplied by the pinned image. The trusted image may "
    "bootstrap the application before tests. Describe precise application paths and interfaces in assumptions. "
    "A frontend build alone does not verify UI behavior; use executable DOM checks when the image supplies "
    "their dependencies, and state any UI behavior not tested. Keep the independent test source within "
    "16384 UTF-8 bytes and 15 files. Declare ALL implementation paths, up to 128, each small enough "
    "for a 24576-byte implementation group. Do not include binary "
    "files, dependency caches or lockfiles already provisioned by the image.")


class ProjectBuildRun(BuildRun):
    artifact_type = ProjectArtifact
    project_type = ProjectProject
    suite_type = ProjectTestSuite
    test_brief = PROJECT_TEST_BRIEF

    def __init__(self, runtime, state):
        super().__init__(runtime, state)
        project_enabled(self.bundle)
        self.contract = self.bundle["build_contract"]
        self.brief["execution_contract"] = copy.deepcopy(self.contract["sandbox_protocol"])
        self.output["build_contract"] = copy.deepcopy(self.contract)
        self.output["verification"]["policy"] = self.contract["verification_policy"]
        self.output["verification"]["mutation_verification"] = "not_run"

    def grouping(self, suite):
        value = self.ask("build_project_plan", "planner", GroupPlan,
            "Partition ALL declared implementation paths into ordered groups of at most 16 files. "
            "Each group's COMPLETE source output must fit 24576 UTF-8 bytes. Use small cohesive groups; "
            "split large modules in the independent interface before freezing it. Define a precise global "
            "cross-file API contract and each group's interface. depends_on names earlier group IDs only; "
            "context_files selects only existing files from those dependencies whose combined source will "
            "fit 24576 bytes. Later groups receive only that selected source, the global contract, their "
            "own interface and frozen tests, never the entire accumulated implementation. Use empty "
            "dependency/context lists where none are needed. Freeze paths, order and interfaces now.",
            implementation_files=suite.implementation_files, interface_assumptions=suite.assumptions,
            test_commands=suite.commands)
        plan = validate_group_plan(value, suite.implementation_files)
        frozen = plan.model_dump()
        self.output["group_plan"] = frozen
        self.stages["build_project_frozen"] = {"digest": digest(frozen), "group_plan": frozen,
                                                "test_suite_digest": self.output["suite_digest"]}
        self.runtime.store.save_artifact(self.state["run_id"], "build_project_frozen", "raw",
                                         self.stages["build_project_frozen"])
        return plan

    def implement_groups(self, plan, suite):
        files = []
        interfaces = {g.id: g.interface for g in plan.groups}
        for index, group in enumerate(plan.groups, 1):
            context = selected_source(files, group.context_files, self.contract["context_bytes"]) if group.context_files else []
            dependencies = {key: interfaces[key] for key in group.depends_on}
            if len(encoded(dependencies).encode("utf-8")) > 8192:
                raise ValueError("Dependency interfaces exceed the bounded group context")
            stage = f"implement_group_{index}"
            raw = self.ask(stage, "implementer", GroupArtifact,
                "Implement ONLY this frozen group's exact file paths, returning complete source for each. "
                "The group's output limit is 24576 UTF-8 bytes. Preserve the global and group interfaces; "
                "all earlier files and independent tests are immutable. Do not return other groups, "
                "tests, dependency caches, binary artifacts or a wrapper directory. Do not bypass tests.",
                contract=plan.contract, group=group.model_dump(), dependency_interfaces=dependencies,
                selected_source=context, frozen_tests=compact_suite(suite))
            artifact = GroupArtifact.model_validate(raw)
            if {f.path for f in artifact.files} != set(group.files):
                raise ValueError("Implementation group changed its frozen file set")
            files.extend(artifact.files)
            accumulated = ProjectArtifact(summary="Incremental offline project", files=files)
            self.stages[stage]["accumulated_digest"] = digest(accumulated.model_dump())
            self.stages[stage]["group_id"] = group.id
            self.runtime.store.save_artifact(self.state["run_id"], f"project_after_{index}",
                                             "raw", accumulated.model_dump())
        return ProjectArtifact(summary="Complete offline project", files=files)

    def repair(self, artifact, suite, baseline, plan):
        stage = self.bounded("repair", "max_repair_attempts")
        self.output["repair_attempts"] += 1
        failures = failure_feedback(baseline)
        tree = [{"path": f.path, "bytes": len(f.content.encode("utf-8")),
                 "sha256": hashlib.sha256(f.content.encode("utf-8")).hexdigest()}
                for f in artifact.files]
        selection = RepairSelection.model_validate(self.ask(stage + "_select", "repairer", RepairSelection,
            "Diagnose this failed test execution and select existing implementation files to repair. "
            "Select at most 16 files with combined source at most 32768 UTF-8 bytes. Use the tree's byte "
            "counts; no source has been omitted from that inventory. Tests, file paths and interfaces "
            "are frozen. Do not select tests or create paths. Return diagnosis and selected paths only.",
            contract=plan.contract, implementation_tree=tree,
            frozen_tests=compact_suite(suite), failed_execution=failures))
        source = selected_source(artifact.files, selection.files, self.contract["repair_selected_bytes"])
        repaired = SelectedRepair.model_validate(self.ask(stage, "repairer", SelectedRepair,
            "Return a diagnosis and complete replacements for EXACTLY the selected implementation paths. "
            "All other implementation files and all independent tests are immutable. Selected replacement "
            "source must fit 32768 UTF-8 bytes total. Honor the approved cross-file API contract. Never "
            "weaken validation, change tests, detect a test runner, or special-case fixtures.",
            contract=plan.contract, diagnosis=selection.diagnosis.model_dump(), selected_source=source,
            frozen_tests=compact_suite(suite), failed_execution=failures))
        self.stages[stage]["diagnosis"] = repaired.diagnosis.model_dump()
        self.stages[stage]["selected_paths"] = selection.files
        return replace_selected(artifact, repaired.artifact, selection.files)

    def execute(self):
        plan = BuildPlan.model_validate(self.ask("build_plan", "planner", BuildPlan,
            "Design a from-scratch offline application implementation plan. Preserve every requested "
            "behavior in observable numbered acceptance criteria. Use the specified stack and exact API "
            "and UI contracts. Implementation will use bounded ordered file groups, up to 128 files and "
            "131072 UTF-8 bytes total. Independent Python black-box tests are authored before source."))
        self.output["plan"] = plan.model_dump()
        raw = self.ask("build_tests", "test_author", ProjectTestSuite, self.test_brief)
        value, fixes = normalized_manifest(raw, raw.get("implementation_files") if isinstance(raw, dict) else None)
        self.stages["build_tests"]["manifest_corrections"] = fixes
        suite = self.check_suite(ProjectTestSuite.model_validate(value))
        grouping = self.grouping(suite)
        artifact = self.implement_groups(grouping, suite)
        while True:
            project = assemble(artifact, suite, ProjectProject)
            number = len(self.output["verification"]["baselines"])
            self.stage = f"build_verify_{number}"
            baseline = self.runtime._build_execution(self.state, self.stage, number, project, suite)
            self.output["verification"]["baselines"].append({"suite_digest": self.output["suite_digest"], **baseline})
            if not validate_baseline(baseline, suite):
                if not actionable_failure(baseline):
                    self.output["error"] = {"stage": self.stage, "message": "Inconclusive sandbox test execution"}
                    return "review_required"
                artifact = self.repair(artifact, suite, baseline, grouping)
                continue
            self.stage = "build_project_acceptance"
            quality = self.runtime._build_quality(self.state, self.stage, project, suite, baseline)
            self.output["verification"]["strength"].append(quality)
            if quality["status"] != "passed":
                return "review_required"
            self.output["artifact"] = project.model_dump()
            return "completed"
