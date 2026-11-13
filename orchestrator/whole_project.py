"""Run an opt-in whole-project implementation and two separate test suites."""

from functools import lru_cache, partial
import re
from types import SimpleNamespace
from uuid import UUID

from pydantic import Field, model_validator

from .build_models import BuildPlan
from .build_workflow import build_model_payload, normalized_manifest, public_error
from .build_discovery import normalize_authored_test_paths
from .project_build import (REVIEWED_POLICY, ProjectArtifact, ProjectBuildRun, ProjectProject, ProjectTestSuite,
                            TestManifest, failure_feedback, replace_selected, validate_project_prompt)
from .repository import MAX_FILE
from .store import digest
from .whole_project_limits import DEFAULT_SOURCE_BYTES, LEGACY_SOURCE_BYTES, MAX_SOURCE_FILES, validate_source_bytes
from .whole_project_sandbox import execute_self_test, validate_self_suite


POLICY = "opencode-whole-project-v1"
WORKER_ROLES = {"implementer", "test_author", "repairer"}
PLAN_BRIEF = (
    "Design a complete offline application. Return one flat inventory of all implementation_files, including "
    "the declared required application files, build and runtime configuration, and essential entry points. "
    f"Use at most {MAX_SOURCE_FILES} paths under the supplied UTF-8 source budget. Keep tests out of the inventory. "
    "Describe the exact cross-file contracts and observable acceptance criteria for the requested behavior. "
    "If the environment declares an API path_prefix, the server itself must serve that prefix. "
    "The implementer receives the entire plan and writes the complete application in one worktree."
)


def whole_project_source_bytes(bundle):
    approved_workers = bundle.get("whole_project_workers", {})
    old_binding = approved_workers and "source_bytes" not in approved_workers.get("implementer", {})
    default = LEGACY_SOURCE_BYTES if old_binding else DEFAULT_SOURCE_BYTES
    return validate_source_bytes(bundle.get("whole_project_source_bytes", default))


@lru_cache(maxsize=8)
def _whole_project_types(source_bytes):
    class WholeArtifact(ProjectArtifact):
        byte_limit = source_bytes

        @model_validator(mode="after")
        def file_storage_limit(self):
            if any(len(file.content.encode("utf-8")) > MAX_FILE for file in self.files):
                raise ValueError(f"Whole-project files must fit the {MAX_FILE}-byte worktree file limit")
            return self

    class WholeProject(ProjectProject):
        byte_limit = source_bytes + TestManifest.byte_limit
        implementation_type = WholeArtifact

    return WholeArtifact, WholeProject


def whole_project_types(bundle):
    return _whole_project_types(whole_project_source_bytes(bundle))


class WholeProjectPlan(BuildPlan):
    implementation_files: list[str] = Field(min_length=1, max_length=MAX_SOURCE_FILES)

    @model_validator(mode="after")
    def canonical_paths(self):
        manifest = ProjectArtifact(summary="Planned source inventory", files=[
            {"path": path, "content": ""} for path in self.implementation_files
        ])
        if self.implementation_files != [file.path for file in manifest.files]:
            raise ValueError("The implementation plan must use canonical paths")
        return self


def validate_whole_project_seed(seed, artifact_type, required_paths):
    if not isinstance(seed, dict) or seed.keys() not in (
            {"plan", "artifact", "provenance"}, {"plan", "artifact", "self_suite", "provenance"}):
        raise ValueError("Expected a whole-project draft, plan and snapshot provenance")
    plan = WholeProjectPlan.model_validate(seed["plan"])
    artifact = artifact_type.model_validate(seed["artifact"])
    provenance = seed["provenance"]
    fields = {"kind", "source_run_id", "source_plan_digest", "source_artifact_digest"}
    retained = "self_suite" in seed
    kind = "settled-worker-receipts" if retained else "unverified-worktree-snapshot"
    evidence_keys = {"source_implementation_sha256", "source_self_tests_sha256"} if retained else {"source_snapshot_sha256"}
    if (not isinstance(provenance, dict) or provenance.keys() != fields | evidence_keys
            or provenance["kind"] != kind
            or not isinstance(provenance["source_run_id"], str)
            or any(not isinstance(provenance[key], str) or not re.fullmatch(r"[0-9a-f]{64}", provenance[key])
                   for key in evidence_keys)
            or provenance["source_plan_digest"] != digest(plan.model_dump())
            or provenance["source_artifact_digest"] != digest(artifact.model_dump())):
        raise ValueError("Whole-project draft snapshot provenance differs from the approved plan or source")
    UUID(provenance["source_run_id"])
    if (set(plan.implementation_files) != {file.path for file in artifact.files}
            or not set(required_paths) <= set(plan.implementation_files)):
        raise ValueError("Whole-project draft snapshot differs from the approved implementation inventory")
    return plan, artifact, provenance


def _suite_feedback(value, error, *, byte_limit, file_limit):
    feedback = {"validation_error": public_error(error), "max_utf8_bytes": byte_limit, "max_files": file_limit}
    files = value.get("files") if isinstance(value, dict) else None
    if isinstance(files, list):
        feedback["observed_files"] = len(files)
        if all(isinstance(file, dict) and isinstance(file.get("content"), str) for file in files):
            feedback["observed_utf8_bytes"] = sum(len(file["content"].encode("utf-8")) for file in files)
    return feedback


class WholeProjectBuildRun(ProjectBuildRun):
    def __init__(self, runtime, state):
        super().__init__(runtime, state)
        if (self.bundle["whole_project_workflow"] != POLICY
                or self.bundle["request"]["build_profile"] != REVIEWED_POLICY
                or "project_reuse" in self.bundle or self.opencode_worker is not None):
            raise ValueError("Whole-project builds require their own reviewed workflow approval")
        self.source_bytes = whole_project_source_bytes(self.bundle)
        self.artifact_type, self.project_type = whole_project_types(self.bundle)
        self.workers = getattr(runtime, "whole_project_workers", {})
        bindings = self.bundle.get("whole_project_workers", {})
        if self.workers.keys() != WORKER_ROLES or bindings.keys() != WORKER_ROLES:
            raise ValueError("Whole-project builds require all three approved OpenCode workers")
        for role in sorted(WORKER_ROLES):
            self._worker_identity(role)
        self.compile_checkpoints = []
        self.test_brief = self.test_brief.replace(
            "Declare ALL implementation paths, up to 128, each small enough for a 24576-byte implementation group.",
            "Copy the complete approved implementation path inventory unchanged.",
        )
        self.evidence = self.output["verification"]["whole_project"] = {
            "policy": POLICY, "source_bytes": self.source_bytes, "self_checks": [], "final_self_checks": [],
        }

    def _worker_identity(self, role):
        identity = self.workers[role].identity()
        settings = (self.bundle.get("role_profiles") or self.bundle["generation"])[role]
        if (identity != self.bundle["whole_project_workers"][role] or identity.get("name") != "opencode"
                or identity.get("role") != role or identity.get("model") != self.bundle["roles"][role]
                or identity.get("context") != settings["context"] or identity.get("thinking") != settings["think"]
                or identity.get("source_bytes", LEGACY_SOURCE_BYTES) != self.source_bytes
                or identity.get("auto_compaction") is not True):
            raise ValueError(f"The approved whole-project OpenCode worker differs for {role}")
        return identity, settings

    def _opencode(self, stage, role, operation, visible, **arguments):
        self.stage = stage
        runtime, run_id = self.runtime, self.state["run_id"]
        identity, settings = self._worker_identity(role)
        model = self.bundle["roles"][role]
        request = {"worker": identity, "role": role, "model": model, "settings": settings,
                   "operation": operation, "visible_files": {path: digest(text) for path, text in visible.items()},
                   **arguments}
        runtime._guard(run_id)
        result = runtime.store.cached(run_id, stage, request=request, role=role, model=model)
        if result is None:
            while not runtime.inference_lock.acquire(timeout=.1):
                runtime._guard(run_id)
            try:
                runtime._guard(run_id)
                if runtime.store.has_unknown_calls():
                    raise RuntimeError("native_completion_unknown; resolve the physical backend before another call")
                self._worker_identity(role)
                switch = getattr(runtime, "before_whole_project_worker", None)
                if callable(switch):
                    switch(role, runtime.events[run_id], runtime.deadline)
                    runtime._guard(run_id)
                request_id = runtime.store.begin_call(run_id, stage, role, model, request)
                callback = getattr(runtime, "whole_project_event", None)
                on_event = partial(callback, run_id, request_id, stage, role, model) if callable(callback) else None
                try:
                    dispatch = getattr(self.workers[role], operation)
                    result = dispatch(stage=stage, role=role, model=model, settings=settings, visible_files=visible,
                                      cancel=runtime.events[run_id], deadline=runtime.deadline,
                                      scratch_parent=runtime.store.directory, on_event=on_event, **arguments)
                except Exception as error:
                    if getattr(error, "not_dispatched", False):
                        runtime.store.finish_call(run_id, stage, {"dispatch_status": "not_dispatched",
                                                                  "worker_error": public_error(error)})
                    elif getattr(error, "backend_cleanup_confirmed", False):
                        runtime.store.finish_call(run_id, stage, {"worker_error": public_error(error),
                                                                  "backend_cleanup_confirmed": True})
                    else:
                        runtime._save_call_failure(run_id, stage, error)
                        if runtime.store.has_unknown(run_id):
                            raise RuntimeError("OpenCode completion is unknown") from error
                    raise
                runtime.store.finish_call(run_id, stage, result)
                runtime._guard(run_id)
            finally:
                runtime.inference_lock.release()
        if "worker_error" in result:
            raise ValueError(result["worker_error"])
        receipt = result.get("receipt", {})
        resident = receipt.get("resident", {})
        expected = {"worker": identity, "role": role, "stage": stage, "context": settings["context"],
                    "thinking": settings["think"], "model_digest": identity["model_digest"]}
        if (any(receipt.get(key) != value for key, value in expected.items())
                or resident.get("digest") != identity["model_digest"]
                or resident.get("context_length") != settings["context"]):
            raise ValueError("OpenCode receipt differs from the approved whole-project worker")
        self.stages[stage] = {"role": role, "model": model, "worker": "opencode",
                              "worker_receipt_digest": digest(receipt)}
        return result

    def _generate(self, stage, role, model_type, instruction, data, visible):
        self.stage = stage
        payload = build_model_payload(model_type, instruction, self.model_inputs(data))
        validate_project_prompt(payload, self.bundle, role)
        result = self._opencode(stage, role, "generate", visible, payload=payload)
        value = result.get("value")
        if not isinstance(value, dict):
            raise ValueError("OpenCode did not return a structured test manifest")
        return value

    def ask(self, stage, role, model_type, instruction, **data):
        if role == "test_author":
            return self._generate(stage, role, model_type, instruction, data, {})
        return super().ask(stage, role, model_type, instruction, **data)

    def _implement(self, paths):
        result = self._opencode(
            "implement", "implementer", "run", {}, allow_create=True, editable_paths=paths,
            instruction=("Write the complete application in this one session. Implement every approved path. "
                         "Follow the plan and serve the API under its declared prefix. Write source files through "
                         "the workspace tools, and ensure build configuration and imports agree across files. "
                         "Return only a short summary after all files are on disk. Workbench runs tests next."),
            inputs=self.model_inputs({"implementation_files": paths, "max_utf8_source_bytes": self.source_bytes}),
        )
        files = result.get("files")
        if not isinstance(files, dict) or files.keys() != set(paths):
            raise ValueError("Whole-project implementation changed the approved path inventory")
        return self.artifact_type(summary="Whole-project OpenCode implementation", files=[
            {"path": path, "content": files[path]} for path in paths
        ])

    def _author_self_suite(self, artifact, paths):
        stage = "whole_self_tests"
        brief = (
            "Write your own compact executable Python unittest smoke suite for this application. Use valid "
            "fixtures and three to five focused checks for critical behavior, including a meaningful failure path. "
            "Aim for under 8192 UTF-8 bytes in one or two files. "
            "Workbench also runs the image's required build and runtime checks. Select the highest-priority smoke "
            "checks and avoid an exhaustive endpoint or UI matrix. Map tested behaviors to criterion numbers "
            "from the plan. Verify API registration and a protected call if the plan declares them. "
            "Use the runtime environment metadata. Copy implementation_files exactly, "
            "and return test source only under tests/test_*.py with the declared unittest discovery command. "
            "The application source is read-only. Workbench will execute your suite."
        )
        data = {"implementation_files": paths}
        source = {file.path: file.content for file in artifact.files}
        value = self._generate(stage, "implementer", ProjectTestSuite, brief, data, source)
        try:
            return validate_self_suite(self, value, artifact)
        except ValueError as error:
            feedback = _suite_feedback(value, error, byte_limit=self.contract["test_bytes"],
                                       file_limit=self.contract["test_files"])
            self.stages[stage]["validation_error"] = feedback["validation_error"]
            corrected = self._generate(
                stage + "_retry", "implementer", ProjectTestSuite,
                brief + " Use the validation feedback to write a corrected complete smoke suite.",
                {**data, "previous_suite_validation": feedback}, source,
            )
            return validate_self_suite(self, corrected, artifact)

    def _parse_independent_suite(self, raw, paths, stage):
        declared = raw.get("implementation_files")
        if (not isinstance(declared, list) or len(declared) != len(paths)
                or not all(isinstance(path, str) for path in declared) or set(declared) != set(paths)):
            raise ValueError("Independent tests changed the approved implementation path inventory")
        value, fixes = normalized_manifest({**raw, "implementation_files": paths}, paths)
        self.stages[stage]["manifest_corrections"] = fixes
        # The normalizer uses the coverage list to reconcile references when renaming test files.
        if not isinstance(value.get("coverage"), list):
            raise ValueError("Independent tests must declare a coverage list")
        value, renames = normalize_authored_test_paths(value)
        if renames:
            self.stages[stage]["filename_corrections"] = renames
        return ProjectTestSuite.model_validate(value)

    def _author_independent_suite(self, paths):
        stage = "build_tests"
        brief = (self.test_brief + " This is an independent acceptance suite for the specification. "
                 "The approved implementation file inventory is supplied; return it unchanged.")
        data = {"implementation_files": paths}
        raw = self.ask(stage, "test_author", ProjectTestSuite, brief, **data)
        try:
            return self._parse_independent_suite(raw, paths, stage)
        except ValueError as error:
            feedback = _suite_feedback(raw, error, byte_limit=self.contract["test_bytes"],
                                       file_limit=self.contract["test_files"])
            self.stages[stage]["validation_error"] = feedback["validation_error"]
            stage += "_retry"
            corrected = self.ask(
                stage, "test_author", ProjectTestSuite,
                brief + " Use the validation feedback to write a corrected complete test suite.",
                **data, previous_suite_validation=feedback,
            )
            return self._parse_independent_suite(corrected, paths, stage)

    def _patch(self, stage, role, artifact, suite, feedback, *, paths=None):
        editable = sorted(paths if paths is not None else (file.path for file in artifact.files))
        visible = {file.path: file.content for file in (*artifact.files, *suite.files)}
        result = self._opencode(
            stage, role, "run", visible, editable_paths=editable, allow_create=False,
            instruction=("Repair the observed failures in the application. Diagnose from the trusted receipt and "
                         "inspect the relevant source. Make minimal changes to existing approved application files. "
                         "Preserve the original public contracts and tests. Never modify, intercept, detect, or "
                         "bypass the test runner. Workbench re-executes the original tests after this patch."),
            inputs=self.model_inputs({"tests": {"paths": [f.path for f in suite.files], "commands": suite.commands},
                                      "max_utf8_source_bytes": self.source_bytes,
                                      **feedback}),
        )
        files = result.get("files")
        if (not isinstance(files, dict) or not files or not files.keys() <= set(editable)
                or any(text == visible[path] for path, text in files.items())):
            raise ValueError("OpenCode repair returned no changes or exceeded its approved file scope")
        patch = self.artifact_type(summary="Whole-project OpenCode repair", files=[
            {"path": path, "content": text} for path, text in files.items()
        ])
        self.stages[stage]["replaced_paths"] = list(files)
        return replace_selected(artifact, patch, editable, allow_subset=True, artifact_type=self.artifact_type)

    def _self_check(self, artifact, stage, index):
        result = execute_self_test(self, artifact, self.self_suite, stage, index)
        result["implementation_digest"] = digest(artifact.model_dump())
        self.stages[stage] = {"status": result["status"], "project_digest": result["project_digest"],
                              "suite_digest": result["suite_digest"]}
        return result

    def before_acceptance(self, artifact):
        checks = self.evidence["final_self_checks"]
        index = len(checks)
        stage = "whole_self_verify_final" if not index else f"whole_self_verify_final_{index}"
        result = self.evidence["final_self_check"] = self._self_check(artifact, stage, 8002 + index)
        checks.append(result)
        if result["status"] == "passed":
            return True, artifact
        if result["status"] == "failed" and self.output["repair_attempts"] < self.limits["max_repair_attempts"]:
            stage = self.bounded("repair", "max_repair_attempts")
            self.output["repair_attempts"] += 1
            return False, self._patch(stage, "repairer", artifact, self.self_suite,
                                      {"failed_execution": result["feedback"]})
        self.output["error"] = {"stage": self.stage, "message": "Final self-test verification did not pass"}
        return False, None

    def repair(self, artifact, suite, baseline, plan, review=None, *, focus=None):
        stage = self.bounded("repair", "max_repair_attempts")
        self.output["repair_attempts"] += 1
        evidence = {"failed_execution": failure_feedback(baseline, include_stage_results=True)} if baseline else {}
        paths = None
        if focus:
            evidence.update(focus)
            scope = getattr(self.runtime, "verification_repair_scope", None)
            target = focus["repair_target"]
            if (scope and baseline and target.get("control_match") is True
                    and baseline.get("sandbox", {}).get("image_id") == self.bundle["sandbox"]["image_id"]):
                paths = scope(target, artifact.files) or None
        if review:
            evidence["independent_code_review"] = [finding for finding in review["findings"]
                if finding["severity"] in self.contract["review_blocking_severities"]][
                    :self.contract["max_review_repair_findings"]]
        return self._patch(stage, "repairer", artifact, suite, evidence, paths=paths)

    def execute(self):
        required_paths = (self.environment or {}).get("required_application_files", [])
        seeded = "whole_project_seed" in self.bundle
        if seeded:
            plan, artifact, provenance = validate_whole_project_seed(
                self.bundle["whole_project_seed"], self.artifact_type, required_paths,
            )
            self.evidence["draft_provenance"] = provenance
        else:
            plan = WholeProjectPlan.model_validate(self.ask("build_plan", "planner", WholeProjectPlan, PLAN_BRIEF,
                                                            max_utf8_source_bytes=self.source_bytes))
        self.output["plan"] = plan.model_dump()
        paths = plan.implementation_files
        if not seeded:
            if not set(required_paths) <= set(paths):
                raise ValueError("Whole-project plan omitted required application files")
            artifact = self._implement(paths)
        self.evidence.update(draft_digest=digest(artifact.model_dump()), implementation_paths=paths)
        seed = self.bundle.get("whole_project_seed") or {}
        if "self_suite" in seed:
            self.stage = "whole_self_tests_retry"
            self.self_suite = validate_self_suite(self, seed["self_suite"], artifact)
            self.stages[self.stage] = {"reused_from": seed["provenance"]["source_run_id"]}
        else:
            self.self_suite = self._author_self_suite(artifact, paths)
        self.evidence["self_suite_digest"] = digest(self.self_suite.model_dump())
        self.evidence["self_suite"] = self.self_suite.model_dump()
        result = self._self_check(artifact, "whole_self_verify_0", 8000)
        self.evidence["self_checks"].append(result)
        if result["status"] == "failed":
            artifact = self._patch("whole_self_repair", "implementer", artifact, self.self_suite,
                                   {"failed_execution": result["feedback"]})
            result = self._self_check(artifact, "whole_self_verify_1", 8001)
            self.evidence["self_checks"].append(result)
        if result["status"] == "review_required":
            self.output["error"] = {"stage": self.stage, "message": "Self-test execution did not produce a trusted receipt"}
            return "review_required"

        suite = self.check_suite(self._author_independent_suite(paths))
        if suite.implementation_files != paths:
            raise ValueError("Test-suite repair changed the approved implementation path inventory")
        self.evidence["independent_suite_digest"] = self.output["suite_digest"]
        self.runtime.store.save_artifact(self.state["run_id"], "build_frozen", "raw", {
            **self.stages["build_frozen"], "plan_digest": digest(self.output["plan"]),
            "suite": suite.model_dump(), "suite_digest": self.output["suite_digest"],
        })
        return self.verify_and_repair(artifact, suite, SimpleNamespace(contract=plan.model_dump(), groups=[]))
