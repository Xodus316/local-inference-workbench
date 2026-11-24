"""Run an opt-in whole-project implementation and two separate test suites."""

from functools import lru_cache, partial
import math
import re
import time
from types import SimpleNamespace
from typing import Annotated
from uuid import UUID

from pydantic import Field, ValidationError, model_validator

from .build_models import BuildPlan
from .build_workflow import (actionable_failure, build_model_payload, normalized_manifest, public_error,
                             reference_test_failures, validate_baseline)
from .build_discovery import normalize_authored_test_paths
from .project_build import (DEFAULT_TEST_BYTES, REVIEWED_POLICY, ProjectArtifact, ProjectBuildRun, ProjectProject,
                            execution_settled, failure_feedback, project_test_types,
                            replace_selected, scoped_repair_selection, selected_source, settled_group_response,
                            unittest_failure_excerpts, validate_project_prompt)
from .ollama import _strict_json
from .project_opencode import OpenCodeOutputLimitError
from .project_opencode_whole import OpenCodeSettledResponseError
from .repository import MAX_FILE
from .store import digest
from .whole_project_java_format import JAVA_FORMAT_REPAIR_GUIDANCE, java_date_format_findings
from .whole_project_limits import (DEFAULT_SOURCE_BYTES, LEGACY_SOURCE_BYTES, MAX_SOURCE_FILES,
                                   WHOLE_PROJECT_COMPLETION_TOKENS, validate_source_bytes)
from .whole_project_mongo import TARGET_ID as MONGO_INCREMENT_TARGET
from .whole_project_repair_evidence import repair_feedback, repair_record
from .whole_project_sandbox import (SelfTestFixtureError, compose_smoke_source_patch, execute_self_test,
                                    smoke_source_patch_type, smoke_test_type, validate_self_fixture_correction,
                                    validate_self_suite)
from .project_dependency_reference import validate_dependency_reference
from .whole_project_repair import (POLICY as REPAIR_POLICY, focused_feedback, public_repair_plan,
                                   verification_observation, verification_regressions)
from .whole_project_selection import (COMPILER_SELECTION_POLICY, INLINE_SELECTION_POLICY, SELECTION_OUTPUT_TOKENS,
                                      SELECTION_RESTART_SOURCE_BYTES,
                                      compiler_diagnostic_selection, gradle_kotlin_dsl_findings, projection_details, selection_source,
                                      trim_selection_source)
from .whole_project_smoke import (INLINE_SMOKE_POLICY, SMOKE_OUTPUT_TOKENS, SMOKE_REQUEST_GUIDANCE, SMOKE_STAGES,
                                  SMOKE_REFERENCE_REPAIR_STAGES, project_smoke_inputs,
                                  public_smoke_plan, smoke_author_data)
from .whole_project_smoke_reference import validate_smoke_reference
from .whole_project_test_author import (AUTHOR_GUIDANCE, AUTHOR_RESTART_STAGE, COVERAGE_PATCH_POLICY,
                                        INLINE_AUTHOR_RESTART_POLICY, IndependentCoverageError,
                                        IndependentCoveragePatch, compose_coverage_patch, coverage_patch_inputs, coverage_patch_type,
                                        numbered_criteria, public_test_author_plan,
                                        validate_coverage_indices)
from .whole_project_test_repair import (DIAGNOSIS_POLICY, DIAGNOSIS_POLICY_V2, DIAGNOSIS_STAGE, INLINE_DIAGNOSIS_POLICY,
                                        INLINE_DIAGNOSIS_POLICY_V2, INLINE_METHOD_PATCH_POLICY, INLINE_METHOD_PATCH_POLICY_V3,
                                        INLINE_METHOD_PATCH_POLICY_V4,
                                        METHOD_PATCH_POLICY, METHOD_PATCH_POLICY_V2, METHOD_PATCH_POLICY_V3, METHOD_PATCH_POLICY_V4,
                                        METHOD_PATCH_STAGE, REFERENCE_GENERATION_LIMITS, REFERENCE_REPAIR_GUIDANCE,
                                        ReferenceDiagnosis, ReferenceMethodPatch, compose_reference_method_patch,
                                        reference_diagnosis_binding, reference_diagnosis_inputs, reference_diagnosis_type,
                                        reference_generation_limits,
                                        reference_method_patch_inputs, reference_method_patch_type)
from .whole_project_reference_history import (empty_reference_history, history_with_record,
                                               reference_attempt_record, validate_reference_history)


POLICY = "opencode-whole-project-v1"
WORKER_ROLES = {"implementer", "test_author", "repairer"}
MAX_SELF_REPAIRS = 3


class IndependentSuiteRepairExhausted(ValueError):
    """A scoped run cannot accept a suite by dropping failed reference tests."""


PLAN_BRIEF = (
    "Design a complete offline application. Return one flat inventory of all implementation_files, including "
    "the declared required application files, build and runtime configuration, and essential entry points. "
    f"Use at most {MAX_SOURCE_FILES} paths under the supplied UTF-8 source budget. Keep tests out of the inventory. "
    "Each implementation_files item must be only a bare canonical relative path string, such as "
    "frontend/src/main.ts. Do not include path labels, purposes, required flags, metadata, colons or line breaks "
    "inside an inventory item. Group implementation_steps by subsystem and name the cross-file contracts "
    "where they matter. Use one or two concise sentences per entry. Record observable acceptance criteria "
    "and a short test strategy. The implementer and test author also receive the complete brief, so refer "
    "to it for full details instead of repeating it. Return compact JSON with no source code or commentary. "
    "Preserve the meaning of the supplied task and explicit contract clarifications. Separate required public "
    "behavior from implementation choices: do not add stricter field types, encodings, defaults, validation "
    "rules or response requirements that the supplied contract does not establish. Precision and representation "
    "are separate properties; a precision requirement alone does not imply a numeric or textual wire format. "
    "Use provided clarifications to resolve ambiguities. Record any remaining interpretation or implementation "
    "choice in assumptions, and do not promote it into a mandatory acceptance criterion. "
    "If the environment declares an API path_prefix, the server itself must serve that prefix. "
    "The implementer receives the entire plan and writes the complete application in one worktree."
)


def _stopped_opencode_result(result):
    receipt = result.get("receipt", {})
    if not isinstance(receipt, dict) or receipt.get("scratch_cleanup_confirmed") is not True:
        return False
    events = receipt.get("events")
    if not isinstance(events, list) or not events or not isinstance(events[-1], dict):
        return False
    final = events[-1]
    part = final.get("part")
    return final.get("type") == "step_finish" and isinstance(part, dict) and part.get("reason") == "stop"


def _smoke_repair_feedback(result):
    evidence = {"failed_execution": result["feedback"]}
    if 'source_recovery_request' in result:
        evidence['stage_recovery_request'] = result['source_recovery_request']
    if "repair_target" in result:
        evidence["repair_target"] = result["repair_target"]
    return evidence


def whole_project_source_bytes(bundle):
    approved_workers = bundle.get("whole_project_workers", {})
    old_binding = approved_workers and "source_bytes" not in approved_workers.get("implementer", {})
    default = LEGACY_SOURCE_BYTES if old_binding else DEFAULT_SOURCE_BYTES
    return validate_source_bytes(bundle.get("whole_project_source_bytes", default))


@lru_cache(maxsize=8)
def _whole_project_types(source_bytes, test_manifest):
    class WholeArtifact(ProjectArtifact):
        byte_limit = source_bytes

        @model_validator(mode="after")
        def file_storage_limit(self):
            if any(len(file.content.encode("utf-8")) > MAX_FILE for file in self.files):
                raise ValueError(f"Whole-project files must fit the {MAX_FILE}-byte worktree file limit")
            return self

    class WholeProject(ProjectProject):
        byte_limit = source_bytes + test_manifest.byte_limit
        implementation_type = WholeArtifact
        test_manifest_type = test_manifest

    return WholeArtifact, WholeProject


def whole_project_types(bundle):
    suite_type, _ = project_test_types(bundle)
    manifest = suite_type.manifest_type
    supplemental = bundle.get("whole_project_regression_suite") or {}
    if supplemental.get("policy") == "reference-validated-regression-suite-v2":
        from .supplemental_regression import supplemental_suite_type
        supplemental_manifest = supplemental_suite_type(supplemental.get("capacity")).manifest_type
        if supplemental_manifest.byte_limit > manifest.byte_limit:
            manifest = supplemental_manifest
    return _whole_project_types(whole_project_source_bytes(bundle), manifest)


class WholeProjectPlan(BuildPlan):
    implementation_files: list[Annotated[str, Field(
        min_length=1, max_length=500, pattern=r"^[^\x00-\x1f:]+$",
        description="One bare canonical relative file path only, without metadata, labels or line breaks.",
    )]] = Field(min_length=1, max_length=MAX_SOURCE_FILES)

    @model_validator(mode="after")
    def canonical_paths(self):
        manifest = ProjectArtifact(summary="Planned source inventory", files=[
            {"path": path, "content": ""} for path in self.implementation_files
        ])
        if self.implementation_files != [file.path for file in manifest.files]:
            raise ValueError("The implementation plan must use canonical paths")
        return self


def validate_whole_project_seed(seed, artifact_type, required_paths):
    draft_fields = {"plan", "artifact", "provenance"}
    if not isinstance(seed, dict) or seed.keys() not in (
            draft_fields, draft_fields | {"self_suite"}, draft_fields | {"self_suite", "independent_test_repair"},
            draft_fields | {"self_suite", "independent_test_draft"}, draft_fields | {"self_suite", "independent_reference_draft"},
            draft_fields | {"self_suite", "independent_reference_draft", "independent_reference_history"}):
        raise ValueError("Expected a whole-project draft, plan and snapshot provenance")
    plan = WholeProjectPlan.model_validate(seed["plan"])
    artifact = artifact_type.model_validate(seed["artifact"])
    provenance = seed["provenance"]
    fields = {"kind", "source_run_id", "source_plan_digest", "source_artifact_digest"}
    resume_repair = "independent_test_repair" in seed
    if resume_repair:
        evidence = {"settled-test-repair": {"source_self_tests_sha256", "source_self_suite_digest",
                                            "source_test_suite_digest", "source_reference_failure_digest"}}
    elif "self_suite" in seed:
        evidence = {
            "settled-worker-receipts": {"source_implementation_sha256", "source_self_tests_sha256"},
            "observed-opencode-smoke-response": {"source_plan_response_sha256", "source_implementation_sha256",
                                                 "source_self_tests_sha256"},
            "settled-self-verification-receipts": {"source_recovery_digest", "source_self_repair_chain_sha256",
                "source_self_tests_sha256", "source_self_suite_digest", "source_reference_receipt_digest"},
            "settled-independent-draft-receipts": {"source_recovery_digest", "source_self_repair_chain_sha256",
                "source_self_tests_sha256", "source_self_suite_digest", "source_reference_receipt_digest",
                "source_independent_request_sha256", "source_independent_response_sha256", "source_independent_draft_digest"},
            "settled-independent-reference-receipts": {"source_recovery_digest", "source_self_tests_sha256",
                "source_self_suite_digest", "source_reference_receipt_digest", "source_independent_suite_digest",
                "source_independent_repair_chain_digest", "source_independent_reference_receipt_digest"},
        }
    else:
        evidence = {
            "unverified-worktree-snapshot": {"source_snapshot_sha256"},
            "settled-implementation-receipts": {"source_plan_response_sha256", "source_implementation_sha256"},
            "settled-compile-repair-receipts": {"source_recovery_digest", "source_compile_chain_sha256"},
        }
    kind = provenance.get("kind") if isinstance(provenance, dict) else None
    evidence_keys = evidence.get(kind) if isinstance(kind, str) else None
    identifiers = {"source_self_tests_run_id"} if kind == "observed-opencode-smoke-response" else set()
    retained_self = isinstance(kind, str) and kind in {"settled-self-verification-receipts", "settled-independent-draft-receipts",
                                                     "settled-independent-reference-receipts"}
    counters = {"source_self_repair_attempts"} if retained_self else set()
    if kind == "settled-independent-reference-receipts":
        counters.add("source_reference_repair_attempts")
    if (evidence_keys is None or provenance.keys() != fields | evidence_keys | identifiers | counters
            or not isinstance(provenance["source_run_id"], str)
            or any(not isinstance(provenance[key], str) for key in identifiers)
            or any(not isinstance(provenance[key], str) or not re.fullmatch(r"[0-9a-f]{64}", provenance[key])
                   for key in evidence_keys)
            or provenance["source_plan_digest"] != digest(plan.model_dump())
            or provenance["source_artifact_digest"] != digest(artifact.model_dump())):
        raise ValueError("Whole-project draft snapshot provenance differs from the approved plan or source")
    if retained_self and (
            type(provenance["source_self_repair_attempts"]) is not int
            or not 1 <= provenance["source_self_repair_attempts"] <= 3
            or digest(seed["self_suite"]) != provenance["source_self_suite_digest"]):
        raise ValueError("Saved self-verification suite or spent self-repair budget differs from its provenance")
    if ("independent_test_draft" in seed) != (kind == "settled-independent-draft-receipts"):
        raise ValueError("An unfrozen independent draft requires its own completed author provenance")
    if kind == "settled-independent-draft-receipts":
        draft = seed["independent_test_draft"]
        if (not isinstance(draft, dict) or draft.keys() != {"suite", "source_draft_digest"}
                or not isinstance(draft["suite"], dict) or draft["source_draft_digest"] != digest(draft["suite"])
                or draft["source_draft_digest"] != provenance["source_independent_draft_digest"]):
            raise ValueError("Saved unfrozen independent draft differs from its completed author response")
    if ("independent_reference_draft" in seed) != (kind == "settled-independent-reference-receipts"):
        raise ValueError("An unfinished reference draft requires its settled correction provenance")
    if kind == "settled-independent-reference-receipts":
        draft = seed["independent_reference_draft"]
        if (not isinstance(draft, dict) or draft.keys() != {"suite", "suite_digest"}
                or not isinstance(draft["suite"], dict) or draft["suite_digest"] != digest(draft["suite"])
                or draft["suite_digest"] != provenance["source_independent_suite_digest"]
                or type(provenance["source_reference_repair_attempts"]) is not int
                or provenance["source_reference_repair_attempts"] < 2
                or provenance["source_reference_repair_attempts"] % 2):
            raise ValueError("Saved reference draft or spent repair rounds differ from their settled provenance")
        if "independent_reference_history" in seed:
            from .whole_project_reference_history import validate_reference_history
            validate_reference_history(seed["independent_reference_history"])
    UUID(provenance["source_run_id"])
    for key in identifiers:
        UUID(provenance[key])
    if (set(plan.implementation_files) != {file.path for file in artifact.files}
            or not set(required_paths) <= set(plan.implementation_files)):
        raise ValueError("Whole-project draft snapshot differs from the approved implementation inventory")
    if resume_repair:
        checkpoint = seed["independent_test_repair"]
        if (not isinstance(checkpoint, dict)
                or checkpoint.keys() != {"suite", "reference_failure", "verification", "counters"}
                or digest(seed["self_suite"]) != provenance["source_self_suite_digest"]
                or digest(checkpoint["suite"]) != provenance["source_test_suite_digest"]
                or digest(checkpoint["reference_failure"]) != provenance["source_reference_failure_digest"]):
            raise ValueError("Saved test repair files or reference failure differ from their provenance")
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
        self.dependency_reference = validate_dependency_reference(
            self.bundle.get("project_dependency_reference"), self.bundle["sandbox"]["image_id"])
        if "project_dependency_reference" in self.bundle and self.dependency_reference is None:
            raise ValueError("An approved dependency API reference cannot be null")
        if "whole_project_regression_suite" in self.bundle:
            from .whole_project_regression import validate_regression_suite
            validate_regression_suite(self.bundle["whole_project_regression_suite"], self.bundle, [])
        self.source_bytes = whole_project_source_bytes(self.bundle)
        self.artifact_type, self.project_type = whole_project_types(self.bundle)
        self.workers = getattr(runtime, "whole_project_workers", {})
        bindings = self.bundle.get("whole_project_workers", {})
        if self.workers.keys() != bindings.keys() or bindings.keys() not in (WORKER_ROLES, WORKER_ROLES | {"planner"}):
            raise ValueError("Whole-project builds require approved implementation, testing and repair workers")
        for role in sorted(bindings):
            self._worker_identity(role)
        self.self_repair_role = getattr(runtime, "whole_self_repair_role", "implementer")
        if self.self_repair_role not in ("implementer", "repairer"):
            raise ValueError("Whole-project self-repair requires an approved implementation or repair worker")
        repair_policy = self.bundle.get("whole_project_repair_policy")
        if repair_policy not in (None, REPAIR_POLICY):
            raise ValueError("Unknown whole-project repair policy")
        self.scoped_repairs = repair_policy == REPAIR_POLICY
        from .compiler_api import KEY, validate_policy
        validate_policy(self.bundle.get(KEY))
        from .frontend_diagnostics import KEY as FRONTEND_DIAGNOSTICS, validate_policy as validate_frontend
        validate_frontend(self.bundle.get(FRONTEND_DIAGNOSTICS))
        from .compile_progress import additional_repairs
        additional_repairs(self.bundle)
        if not self.scoped_repairs:
            self.compile_checkpoints = []
        self._compiled_artifacts = set()
        self._compile_budget_key = None
        self._self_compile_round = None
        self._best_verification = None
        self._regression_feedback = None
        self.test_brief = self.test_brief.replace(
            "Declare ALL implementation paths, up to 128, each small enough for a 24576-byte implementation group.",
            "Copy the complete approved implementation path inventory unchanged.",
        )
        self.evidence = self.output["verification"]["whole_project"] = {
            "policy": POLICY, "source_bytes": self.source_bytes, "self_checks": [], "final_self_checks": [],
        }
        if self.scoped_repairs:
            self.evidence.update(repair_policy=REPAIR_POLICY, promotions=[], rollbacks=[], budget_counters={})
            self._reference_history()
        from .stage_recovery import StageRecovery, enabled
        self.recovery = StageRecovery(self) if enabled(self.bundle) else None

    def diagnose_execution(self, artifact, suite, receipt, **options):
        recovery = getattr(self, 'recovery', None)
        return recovery.diagnose(artifact, suite, receipt, **options) if recovery is not None else None

    def authorize_source_repair(self, artifact, suite, result, *, profile=None):
        if result.get('fixture_failure') and getattr(self, 'recovery', None) is not None:
            owner = 'implementer' if self.stage.startswith('whole_self_') else 'test_author'
            self.recovery.request(self.stage, owner, 'correct_test_fixture', evidence=result['fixture_failure'],
                status='blocked', reason='Test fixture correction requires its bounded author path and reference requalification; application repair is not authorized')
            return False
        if result.get('status') == 'failed':
            return True
        if (getattr(self, 'recovery', None) is None or result.get('status') != 'review_required'
                or result.get('fixture_failure')):
            return False
        receipt = result.get('execution')
        if receipt is None and isinstance(result.get('candidate'), dict):
            # Reference-first profile checks must have actually reached the
            # candidate before application repair can even be considered.
            receipt = result['candidate'].get('execution')
        if receipt is None:
            self.recovery.request(self.stage, 'operator', 'repair_reference_or_verifier',
                evidence={'result_digest': digest(result)}, status='blocked',
                reason='The reference barrier failed before candidate verification; application source is not implicated')
            return False
        request = self.diagnose_execution(artifact, suite, receipt, profile=profile)
        if request is None:
            return False
        result['source_recovery_request'] = request
        result['feedback'] = failure_feedback(receipt, include_stage_results=True)
        result['feedback']['stage_recovery_request'] = request
        return True

    def _reference_history(self):
        if "reference_attempt_history" not in self.evidence:
            saved = (self.bundle.get("whole_project_seed") or {}).get("independent_reference_history")
            self.evidence["reference_attempt_history"] = validate_reference_history(
                saved if saved is not None else empty_reference_history())
        return self.evidence["reference_attempt_history"]

    def repair_reference_failures(self, suite, problems, result):
        if self.scoped_repairs:
            self._reference_observations = {}
            controls = self.output["verification"].get("controls", [])
            if controls:
                receipt = controls[-1]
                if receipt.get("receipt_digest") != digest(result):
                    raise ValueError("Reference attempt history differs from the completed reference receipt")
                excerpts = unittest_failure_excerpts(result)
                for problem in problems:
                    failure = {**problem, "failures": [{"test": name.rsplit(".", 1)[-1],
                        "excerpt": excerpts.get(name.rsplit(".", 1)[-1], "")} for name in problem["names"]]}
                    record = {"source_run_id": self.state["run_id"], "reference_stage": receipt["stage"],
                        "reference_receipt_digest": digest(result), "suite_digest": digest(suite.model_dump()),
                        "failure": failure, "attempt": None}
                    self.evidence["reference_attempt_history"] = history_with_record(self._reference_history(), record)
                    self._reference_observations[problem["path"]] = record
        return super().repair_reference_failures(suite, problems, result)

    def bounded(self, label, limit):
        try:
            return super().bounded(label, limit)
        finally:
            if getattr(self, "scoped_repairs", False) and hasattr(self, "evidence"):
                self.evidence["budget_counters"] = dict(self.counters)

    def model_inputs(self, data):
        inputs = super().model_inputs(data)
        if getattr(self, "_public_review_context", False):
            complete_plan = inputs.pop("plan", None)
            if not isinstance(complete_plan, dict) or inputs.get("contract") != complete_plan:
                raise ValueError("Whole-project review requires the exact approved plan contract")
            # The public brief already defines required behavior. Keep its full
            # criteria and assumptions once, leaving room for complete source.
            inputs["contract"] = {key: value for key, value in complete_plan.items()
                                  if key in {"goal", "acceptance_criteria", "assumptions"}}
            inputs["review_contract_projection"] = {
                "policy": "whole-public-contract-review-v1", "plan_digest": digest(complete_plan),
                "omitted_fields": sorted(complete_plan.keys() - inputs["contract"].keys()),
                "reason": "Implementation narrative is omitted; review the complete current source against the public brief.",
            }
        return inputs

    def code_review(self, *arguments, **keywords):
        if not self.scoped_repairs:
            return super().code_review(*arguments, **keywords)
        previous = getattr(self, "_public_review_context", False)
        self._public_review_context = True
        try:
            # The inherited packer and dispatch both call model_inputs, so the
            # admitted prompt is the same projected contract the reviewer sees.
            return super().code_review(*arguments, **keywords)
        finally:
            self._public_review_context = previous

    def quarantine_reference_failures(self, suite, diagnostics):
        if not self.scoped_repairs:
            return super().quarantine_reference_failures(suite, diagnostics)
        self.stage = "build_tests_quarantine"
        self.stages[self.stage] = {"status": "review_required", "quarantine_performed": False,
                                   "failed_tests": [{"path": item["path"], "names": item["names"]}
                                                    for item in diagnostics]}
        raise IndependentSuiteRepairExhausted(
            "Independent test repair budget exhausted; scoped acceptance requires every test without quarantine")

    def repair_time_available(self, stage, check="verification"):
        if not self.scoped_repairs:
            return super().repair_time_available(stage, check)
        floors = self.contract.get("repair_time_floor_seconds")
        deadline = getattr(self.runtime, "deadline", 0)
        if not floors or not deadline:
            return True
        if deadline == math.inf:
            self.output.setdefault("repair_time_checks", []).append({
                "stage": stage, "remaining_seconds": None, "no_overall_deadline": True, "allowed": True})
            return True
        settings = (self.bundle.get("role_profiles") or self.bundle["generation"])["repairer"]
        thinking = str(settings["think"])
        selection = self._observed_seconds.get("whole_repair_select", 0)
        editing = self._observed_seconds.get("whole_repair_write", 0)
        repair = max(floors.get(thinking, max(floors.values())), selection + editing)
        verification = max(self.contract["check_time_floor_seconds"][check], self._observed_seconds.get(check, 0))
        remaining = deadline - time.monotonic()
        self.output.setdefault("repair_time_checks", []).append({
            "stage": stage, "think": thinking, "estimate_seconds": round(repair + verification),
            "selection_seconds": selection, "edit_seconds": editing,
            "remaining_seconds": round(remaining), "allowed": remaining >= repair + verification})
        return remaining >= repair + verification

    def compile_boundaries(self, plan):
        if not self.scoped_repairs:
            return super().compile_boundaries(plan)
        paths = self.output["plan"]["implementation_files"]
        checkpoints = []
        for slot, checkpoint in enumerate(self.compile_checkpoints, 1):
            component = {path for path in paths if path.startswith(checkpoint["source_root"] + "/")}
            if not component:
                raise ValueError("Compile checkpoint has no files in the whole-project plan")
            checkpoints.append((slot, checkpoint, component))
        return {0: checkpoints}

    def compile_component(self, *args, repair_round=None, **kwargs):
        if not self.scoped_repairs:
            return super().compile_component(*args, repair_round=repair_round, **kwargs)
        slot = args[4]
        self_round = getattr(self, "_self_compile_round", None)
        if self_round is not None and repair_round is not None:
            repair_round = self_round
            kwargs["repair_phase"] = "self"
            key = f"whole_compile_self_{self_round}_{slot}"
        else:
            key = f"whole_compile_{'initial' if repair_round is None else repair_round}_{slot}"
        contract = self.contract
        self.contract = {**contract, "max_compile_repairs": max(0, contract["max_compile_repairs"] - self.counters[key])}
        from .compile_progress import additional_repairs
        extra = additional_repairs(self.bundle)
        if extra:
            spent = self.counters[key]
            kwargs.update(progress_limit=max(0, extra - max(0, spent - contract["max_compile_repairs"])),
                          attempt_offset=spent)
        self._compile_budget_key = key
        try:
            return super().compile_component(*args, repair_round=repair_round, **kwargs)
        finally:
            self.contract = contract
            self._compile_budget_key = None

    def _compile_initial(self, artifact, suite, grouping):
        if self.scoped_repairs:
            for index, checkpoints in self.compile_boundaries(grouping).items():
                for slot, checkpoint, paths in checkpoints:
                    artifact = self.compile_component(artifact, suite, grouping, index, slot, checkpoint, paths)
                    if artifact is None:
                        return None
            self._compiled_artifacts.add(digest(artifact.model_dump()))
        return artifact

    def compile_after_repair(self, previous, artifact, suite, grouping, selected, focus, *, exact_edits=False):
        if not self.scoped_repairs:
            return super().compile_after_repair(previous, artifact, suite, grouping, selected, focus,
                                                exact_edits=exact_edits)
        if digest(artifact.model_dump()) in self._compiled_artifacts:
            return artifact
        result = super().compile_after_repair(previous, artifact, suite, grouping, selected, focus,
                                             exact_edits=exact_edits)
        if result is not None:
            self._compiled_artifacts.add(digest(result.model_dump()))
            return result
        checks = self.output["verification"].get("compile_checkpoints", [])
        # A timeout, mismatched receipt or unknown completion must stop. Only a
        # conclusive compiler failure may roll back a known compiling candidate.
        if (checks and checks[-1]["status"] == "failed"
                and digest(previous.model_dump()) in self._compiled_artifacts):
            record = {"kind": "compile", "rejected_digest": digest(artifact.model_dump()),
                      "retained_digest": digest(previous.model_dump()), "stage": self.stage,
                      "error": self.output.pop("error", None)}
            self.evidence["rollbacks"].append(record)
            self._regression_feedback = record
            return previous
        return None

    def _select_repair_paths(self, stage, artifact, feedback, editable_paths=None):
        feedback = repair_feedback(artifact, feedback, getattr(self, "_repair_history", []))
        inventory = {file.path: file for file in artifact.files
                     if editable_paths is None or file.path in editable_paths}
        if not inventory:
            raise ValueError("No current implementation paths are available for this repair")
        request = feedback.get('stage_recovery_request')
        if request is not None:
            paths = self.recovery.selection(request, artifact)
            if not set(paths) <= set(inventory):
                raise ValueError('Recovery request exceeds the approved editable source scope')
            return paths, feedback
        target = dict(feedback.get("repair_target") or {})
        if "source_paths" in target:
            target["source_paths"] = [path for path in target["source_paths"] if path in inventory]
        evidence = {**feedback, **({"repair_target": target} if target else {})}
        limit = self.contract["repair_selected_bytes"]
        sizes = {path: len(file.content.encode("utf-8")) for path, file in sorted(inventory.items())}
        eligible = tuple(path for path, size in sizes.items() if size <= limit)
        if not eligible:
            raise ValueError("No eligible repair file fits the approved UTF-8 source limit")
        model_type = scoped_repair_selection(eligible)
        tree = [{"path": path, "bytes": size, "sha256": digest(inventory[path].content)} for path, size in sizes.items()]
        selection_stage = stage + "_select"
        derived = compiler_diagnostic_selection(inventory.values(), evidence, byte_limit=limit)
        if derived is not None:
            pending = derived["pending_paths"]
            gradle_findings = derived.get("gradle_kotlin_dsl_findings")
            evidence_kind = ({"gradle_kotlin_dsl_findings": gradle_findings} if gradle_findings else {})
            javac_paths = {item["path"] for item in derived["matches"]
                           if item["compiler_stage"] in {"gradle-compile", "gradle-build"}
                           and item["path"].endswith(".java")} & set(derived["paths"])
            java_findings = java_date_format_findings(
                {path: inventory[path].content for path in javac_paths}, getattr(self, "dependency_reference", None))
            if java_findings:
                evidence_kind["java_date_format_findings"] = java_findings
            from .frontend_diagnostics import literal_findings
            if findings := literal_findings({path: inventory[path].content for path in derived['paths']}, evidence):
                evidence_kind['typescript_literal_findings'] = findings
            selection = model_type.model_validate({"files": derived["paths"], "diagnosis": {
                "diagnosis": (["The Gradle compiler failed on Kotlin DSL and the current scripts contain Groovy syntax."]
                              if gradle_findings else
                              ["The failed compiler explicitly locates errors in the selected current source files."]),
                "changes": (["Correct the entire reported DSL family across the selected scripts; preserve build semantics."]
                            if gradle_findings else
                            ["Correct only the reported compiler errors in these files; preserve public behavior and tests."]),
                "verification": ["Repeat the same pinned compiler checkpoint, then the unchanged acceptance gates."],
            }})
            paths = selection.files
            selected_source(artifact.files, paths, limit)
            deferred = {"pending_paths": pending} if pending else {}
            proof = {"policy": COMPILER_SELECTION_POLICY, "model_call": False,
                     "implementation_digest": digest(artifact.model_dump()), "inventory_digest": digest(tree),
                     "failure_feedback_digest": digest(evidence), "max_selected_utf8_bytes": limit,
                     "matches": derived["matches"], "selection": selection.model_dump(), **deferred, **evidence_kind}
            self.stage = selection_stage
            self.runtime.store.save_artifact(self.state["run_id"], self.stage, "raw", proof)
            self.stages[self.stage] = {"selection_policy": COMPILER_SELECTION_POLICY, "model_call": False,
                                       "evidence_digest": digest(proof), "selected_paths": paths, **deferred,
                                       "diagnosis": selection.diagnosis.model_dump()}
            return paths, {**evidence, "diagnosis": selection.diagnosis.model_dump(),
                           "compiler_path_selection": {"policy": COMPILER_SELECTION_POLICY,
                                                       "evidence_digest": digest(proof), "matches": derived["matches"],
                                                       **deferred, **evidence_kind}}
        if target.get("id") == MONGO_INCREMENT_TARGET and target.get("implementation_digest") == digest(artifact.model_dump()):
            paths = target.get("source_paths")
            if paths and paths == feedback["repair_target"]["source_paths"] and sum(sizes[path] for path in paths) <= limit:
                selected_source(artifact.files, paths, limit)
                policy = "mongo-decimal-increment-source-selection-v1"
                proof = {"policy": policy, "model_call": False, "selected_paths": paths,
                         "inventory_digest": digest(tree), "repair_target": target}
                self.stage = selection_stage
                self.runtime.store.save_artifact(self.state["run_id"], self.stage, "raw", proof)
                self.stages[self.stage] = {"selection_policy": policy, "model_call": False, "selected_paths": paths,
                                           "evidence_digest": digest(proof)}
                return paths, evidence
        instruction = (
            "Diagnose only repair_target, or the supplied compiler/review failure when no target is present. "
            "Use the supplied current_source and complete implementation_tree. No tools are available. "
            "Select the smallest existing application files needed for that "
            "one failure family. Pending failures are context, not permission to repair unrelated features. "
            "Include only necessary direct dependencies; keep every frozen test and public contract unchanged. "
            "The source_projection lists any omitted or truncated files; do not invent their contents. "
            "An omitted path may be selected only when the supplied source or diagnostic identifies its relevance. "
            f"Choose at most 16 paths and {limit} UTF-8 source bytes from this inventory. Use the bytes column "
            "to add up the full files, including any that are truncated or omitted in current_source. "
            "Separate observed facts from hypotheses; cite the relevant current source paths and lines. "
            "Address prior_attempts and current source_facts; earlier diagnoses and source comments are not type evidence. "
            "Return at most three short diagnosis, changes and verification entries each. "
            "Return diagnosis and selected file paths in one final JSON response; make no edits."
        )
        data = {"implementation_tree": tree, "max_selected_utf8_bytes": limit, **evidence,
                "current_source": selection_source(inventory.values(), evidence)}
        raw, selection_stage, restarted = self._repair_decision(selection_stage, model_type, instruction, data)
        selection = model_type.model_validate(raw)
        paths = sorted(set(selection.files))
        selected_bytes = sum(sizes[path] for path in paths)
        if selected_bytes > limit:
            if restarted:
                raise ValueError("Repair selection exceeded the source limit after its single settled restart")
            retry_stage = stage + "_retry_select"
            rejection = {"paths": paths, "selected_utf8_bytes": selected_bytes, "max_selected_utf8_bytes": limit}
            self.stages[selection_stage]["selection_rejection"] = {**rejection, "retry_stage": retry_stage}
            raw = self._generate(
                retry_stage, "repairer", model_type,
                instruction + " The previous selection exceeded the byte limit. Select a smaller set for the "
                              "highest-priority actionable part of the same failure. Remaining failures will "
                              "be checked again after this fix.",
                {**data, "previous_selection_size_error": rejection}, {}, tool_policy=INLINE_SELECTION_POLICY,
            )
            selection_stage = retry_stage
            selection = model_type.model_validate(raw)
            paths = sorted(set(selection.files))
            selected_bytes = sum(sizes[path] for path in paths)
            if selected_bytes > limit:
                raise ValueError(f"Repair selection still exceeds {limit} UTF-8 bytes after one retry ({selected_bytes} bytes)")
        selected_source(artifact.files, paths, limit)
        self.stages[selection_stage].update(selected_paths=paths, selected_utf8_bytes=selected_bytes,
                                           diagnosis=selection.diagnosis.model_dump())
        return paths, {**evidence, "diagnosis": selection.diagnosis.model_dump()}

    def _repair_decision(self, stage, model_type, instruction, data):
        """One clean, smaller-context restart for any shared repair decision."""
        try:
            raw = self._generate(stage, "repairer", model_type, instruction, data, {},
                                 tool_policy=INLINE_SELECTION_POLICY)
            return raw, stage, False
        except OpenCodeSettledResponseError as error:
            proof = error.proof
            if (error.reason != "structured-output-limit" or stage.endswith("_retry_select")
                    or (proof.get("completed_requests") != 1 and not (
                        proof.get("primary_requests") == 1 and type(proof.get("compaction_requests")) is int
                        and 1 <= proof['compaction_requests'] <= 8
                        and proof.get("completed_requests") == 1 + proof['compaction_requests']))
                    or proof.get("scratch_cleanup_confirmed") is not True
                    or not re.fullmatch(r"[a-f0-9]{64}", proof.get("native_receipts_digest", ""))
                    or not proof.get("session_id")):
                raise
            retry_stage = stage.removesuffix("_select") + "_retry_select"
            self.stages.setdefault(stage, {})["selection_rejection"] = {
                "reason": error.reason, "settled_response_proof": proof, "retry_stage": retry_stage}
            source = data["current_source"]
            while sum(len(row["content"].encode("utf8")) for row in source) > SELECTION_RESTART_SOURCE_BYTES:
                source = trim_selection_source(source)
            retry_data = {**data, "current_source": source,
                "previous_selection_rejection": "Completed at output limit; no selection was accepted. Return a compact final decision."}
            raw = self._generate(retry_stage, "repairer", model_type, instruction, retry_data, {},
                                 tool_policy=INLINE_SELECTION_POLICY)
            return raw, retry_stage, True

    def repair_selected(self, stage, artifact, suite, plan, problem, feedback, *,
                        editable_paths=None, control_selection=None, exact_edits=False):
        if not self.scoped_repairs:
            return super().repair_selected(stage, artifact, suite, plan, problem, feedback,
                editable_paths=editable_paths, control_selection=control_selection, exact_edits=exact_edits)
        if self._compile_budget_key is not None:
            self.counters[self._compile_budget_key] += 1
            self.evidence["budget_counters"] = dict(self.counters)
        paths, feedback = self._select_repair_paths(stage, artifact, feedback, editable_paths)
        return self._patch(stage, "repairer", artifact, suite, feedback, paths=paths, compile_patch=False)

    def verification_candidate(self, artifact, suite, baseline, report):
        retained = self._retained_verification_candidate(artifact, suite, baseline, report)
        if retained is not artifact:
            return retained
        from .whole_project_gates import candidate_gates
        return candidate_gates(self, artifact, suite, baseline)

    def _retained_verification_candidate(self, artifact, suite, baseline, report):
        if not self.scoped_repairs or not execution_settled(baseline):
            return artifact
        required = ((self.environment or {}).get("verification") or {}).get("required_checks", [])
        observation = verification_observation(report, required)
        if observation is None:
            return artifact
        if self._best_verification is not None:
            previous, previous_observation = self._best_verification
            regression = verification_regressions(previous_observation, observation)
            if regression and digest(previous.model_dump()) != digest(artifact.model_dump()):
                record = {"kind": "verification", "stage": self.stage, **regression,
                          "rejected_digest": digest(artifact.model_dump()),
                          "retained_digest": digest(previous.model_dump()),
                          "rejected_receipt_digest": observation["receipt_digest"]}
                self.evidence["rollbacks"].append(record)
                self._regression_feedback = record
                return previous
        self._best_verification = (artifact, observation)
        self.evidence["promotions"].append({"stage": self.stage, "artifact_digest": digest(artifact.model_dump()),
                                             **observation})
        from .whole_project_checkpoint import save_checkpoint
        save_checkpoint(self, artifact, suite)
        return artifact

    def _worker_identity(self, role):
        from .supplemental_test_capacity import projected_test_bytes
        identity = self.workers[role].identity()
        settings = (self.bundle.get("role_profiles") or self.bundle["generation"])[role]
        if (identity != self.bundle["whole_project_workers"][role] or identity.get("name") != "opencode"
                or identity.get("role") != role or identity.get("model") != self.bundle["roles"][role]
                or identity.get("context") != settings["context"] or identity.get("thinking") != settings["think"]
                or identity.get("source_bytes", LEGACY_SOURCE_BYTES) != self.source_bytes
                or identity.get("test_bytes", DEFAULT_TEST_BYTES) != projected_test_bytes(self.bundle, role)
                or identity.get("auto_compaction") is not True):
            raise ValueError(f"The approved whole-project OpenCode worker differs for {role}")
        return identity, settings

    def _opencode(self, stage, role, operation, visible, **arguments):
        from .supplemental_test_capacity import validate_visible_test_capacity
        validate_visible_test_capacity(self.bundle, role, visible)
        self.stage = stage
        runtime, run_id = self.runtime, self.state["run_id"]
        identity, settings = self._worker_identity(role)
        if getattr(self, 'recovery', None) is not None:
            self.recovery.dispatch(stage, role, arguments)
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
                    elif isinstance(error, OpenCodeSettledResponseError):
                        runtime.store.finish_call(run_id, stage, {"dispatch_status": "completed_rejected",
                            "rejection_reason": error.reason, "settled_response_proof": error.proof})
                    elif getattr(error, "backend_cleanup_confirmed", False):
                        failure = {"worker_error": public_error(error), "backend_cleanup_confirmed": True}
                        if (role == "planner" and stage in {"build_plan", "build_plan_retry"}
                                and isinstance(error, OpenCodeOutputLimitError) and error.steps == 1
                                and getattr(error, "scratch_cleanup", {}).get("scratch_cleanup_confirmed") is True):
                            failure.update(finish_reason="length", scratch_cleanup_confirmed=True)
                        runtime.store.finish_call(run_id, stage, failure)
                    else:
                        runtime._save_call_failure(run_id, stage, error)
                        if runtime.store.has_unknown(run_id):
                            raise RuntimeError("OpenCode completion is unknown") from error
                    checkpoint = getattr(runtime, '_checkpoint_operation', None)
                    if callable(checkpoint) and runtime.store.artifact(run_id, stage, 'raw').is_file():
                        checkpoint(run_id, stage, 'native')
                    raise
                runtime.store.finish_call(run_id, stage, result)
                checkpoint = getattr(runtime, '_checkpoint_operation', None)
                if callable(checkpoint):
                    checkpoint(run_id, stage, 'native')
                runtime._guard(run_id)
            finally:
                runtime.inference_lock.release()
        if result.get('dispatch_status') == 'completed_rejected':
            if "settled_response_proof" in result:
                raise OpenCodeSettledResponseError(result["rejection_reason"], result["settled_response_proof"])
            raise ValueError('The settled native response was rejected; it cannot be reused as accepted content')
        if result.get("finish_reason") == "length" and "worker_error" in result:
            raise OpenCodeOutputLimitError(1)
        if "worker_error" in result:
            raise ValueError(result["worker_error"])
        receipt = result.get("receipt", {})
        resident = receipt.get("resident", {})
        expected = {"worker": identity, "role": role, "stage": stage, "context": settings["context"],
                    "thinking": settings["think"], "model_digest": identity["model_digest"]}
        if arguments.get("tool_policy") is not None:
            expected["tool_policy"] = arguments["tool_policy"]
        if arguments.get("tool_policy") in REFERENCE_GENERATION_LIMITS:
            expected["generation_limits"] = reference_generation_limits(
                arguments["tool_policy"], identity.get("completion_token_ceiling"))
        if (any(receipt.get(key) != value for key, value in expected.items())
                or resident.get("digest") != identity["model_digest"]
                or resident.get("context_length") != settings["context"]):
            raise ValueError("OpenCode receipt differs from the approved whole-project worker")
        elapsed = receipt.get("elapsed_seconds")
        if (self.scoped_repairs and role == "repairer" and type(elapsed) in (int, float)
                and math.isfinite(elapsed) and elapsed >= 0):
            key = "whole_repair_select" if operation == "generate" else "whole_repair_write"
            self._observed_seconds[key] = max(self._observed_seconds.get(key, 0), elapsed)
        self.stages[stage] = {"role": role, "model": model, "worker": "opencode",
                              "worker_receipt_digest": digest(receipt)}
        return result

    def _generate(self, stage, role, model_type, instruction, data, visible, *, tool_policy=None):
        self.stage = stage
        method_patch = isinstance(model_type, type) and issubclass(model_type, ReferenceMethodPatch)
        method_policy = data.get("method_patch_policy")
        method_tool_policy = {METHOD_PATCH_POLICY: None, METHOD_PATCH_POLICY_V2: INLINE_METHOD_PATCH_POLICY,
                              METHOD_PATCH_POLICY_V3: INLINE_METHOD_PATCH_POLICY_V3,
                              METHOD_PATCH_POLICY_V4: INLINE_METHOD_PATCH_POLICY_V4}.get(method_policy)
        if method_patch and (
                not self.scoped_repairs or not METHOD_PATCH_STAGE.fullmatch(stage) or role != "test_author" or visible
                or method_policy not in {METHOD_PATCH_POLICY, METHOD_PATCH_POLICY_V2, METHOD_PATCH_POLICY_V3, METHOD_PATCH_POLICY_V4}
                or model_type.model_json_schema() != reference_method_patch_type(data["targets"], policy=method_policy).model_json_schema()
                or tool_policy != method_tool_policy):
            raise ValueError("Reference method patches require their scoped test-author repair stage")
        if "method_patch_policy" in data and not method_patch:
            raise ValueError("Reference method patch policy requires its exact response schema")
        diagnosis = isinstance(model_type, type) and issubclass(model_type, ReferenceDiagnosis)
        diagnosis_policy = data.get("diagnosis_policy")
        diagnosis_tool_policy = {DIAGNOSIS_POLICY: INLINE_DIAGNOSIS_POLICY,
                                 DIAGNOSIS_POLICY_V2: INLINE_DIAGNOSIS_POLICY_V2}.get(diagnosis_policy)
        if diagnosis and (
                not self.scoped_repairs or not DIAGNOSIS_STAGE.fullmatch(stage) or role != "test_author" or visible
                or diagnosis_policy not in {DIAGNOSIS_POLICY, DIAGNOSIS_POLICY_V2} or tool_policy != diagnosis_tool_policy
                or model_type.model_json_schema() != reference_diagnosis_type(data).model_json_schema()):
            raise ValueError("Reference diagnosis requires its scoped test-author diagnosis stage")
        if "diagnosis_policy" in data and not diagnosis:
            raise ValueError("Reference diagnosis policy requires its exact response schema")
        if method_policy in {METHOD_PATCH_POLICY_V3, METHOD_PATCH_POLICY_V4}:
            prior = data.get("reference_diagnosis", {})
            proof = prior.get("proof", {})
            if (prior.get("stage") != stage.replace("build_tests_defect_", "build_tests_diagnose_", 1)
                    or proof.get("policy") != (DIAGNOSIS_POLICY_V2 if method_policy == METHOD_PATCH_POLICY_V4 else DIAGNOSIS_POLICY)
                    or proof.get("diagnosis_digest") != digest(prior.get("value"))
                    or proof.get("previous_suite_digest") != data.get("previous_suite_digest")
                    or proof.get("failure_digest") != digest(data["diagnostics"][0])
                    or proof.get("case_names") != sorted(data["required_tests"])):
                raise ValueError("Reference method v3 requires its matching validated causal diagnosis")
            if (method_policy == METHOD_PATCH_POLICY_V4
                    and proof.get("history_projection_digest") != digest(data.get("reference_attempt_history"))):
                raise ValueError("Reference method history differs from its validated diagnosis")
        coverage_patch = isinstance(model_type, type) and issubclass(model_type, IndependentCoveragePatch)
        if coverage_patch and (
                not self.scoped_repairs or stage != "build_tests_retry" or role != "test_author" or visible
                or data.get("coverage_patch_policy") != COVERAGE_PATCH_POLICY
                or model_type.model_json_schema() != coverage_patch_type(data["coverage_validation"]).model_json_schema()):
            raise ValueError("Independent coverage patches require the scoped test-author correction stage")
        if "coverage_patch_policy" in data and not coverage_patch:
            raise ValueError("Coverage patch policy requires its exact response schema")
        tokenizers = getattr(self.runtime, "whole_project_prompt_tokenizers", {})
        tokenizer = tokenizers.get(role)
        approved_tokenizer = self.bundle.get("whole_project_prompt_tokenizers", {}).get(role)
        if (tokenizer.identity() if tokenizer is not None else None) != approved_tokenizer:
            raise ValueError("The whole-project tokenizer differs from the approved model profile")
        if tool_policy not in (None, INLINE_SELECTION_POLICY, INLINE_SMOKE_POLICY, INLINE_AUTHOR_RESTART_POLICY,
                               *REFERENCE_GENERATION_LIMITS):
            raise ValueError("Unapproved whole-project structured tool policy")
        if tool_policy in {INLINE_METHOD_PATCH_POLICY, INLINE_METHOD_PATCH_POLICY_V3, INLINE_METHOD_PATCH_POLICY_V4} and not method_patch:
            raise ValueError("Inline method repair requires its exact patch schema and stage")
        if tool_policy in {INLINE_DIAGNOSIS_POLICY, INLINE_DIAGNOSIS_POLICY_V2} and not diagnosis:
            raise ValueError("Inline diagnosis requires its exact diagnosis schema and stage")
        if tool_policy == INLINE_SMOKE_POLICY and (role != "implementer" or stage not in SMOKE_STAGES or visible):
            raise ValueError("Inline smoke generation requires its approved stage and empty worktree")
        author_restart = tool_policy == INLINE_AUTHOR_RESTART_POLICY
        if author_restart and (not self.scoped_repairs or stage != AUTHOR_RESTART_STAGE or role != "test_author"
                               or model_type is not self.suite_type or visible):
            raise ValueError("Independent author restart requires its exact suite schema and empty worktree")
        inline = tool_policy is not None
        bounded_method = tool_policy in REFERENCE_GENERATION_LIMITS
        reference_repair = tool_policy == INLINE_SMOKE_POLICY and stage in SMOKE_REFERENCE_REPAIR_STAGES
        # These reserves guarantee room before dispatch. A provider with a larger
        # completion ceiling can use additional context left by the rendered prompt.
        output_reserve = (WHOLE_PROJECT_COMPLETION_TOKENS if author_restart else
                          REFERENCE_GENERATION_LIMITS[tool_policy]["max_completion_tokens"] if bounded_method else
                          SMOKE_OUTPUT_TOKENS if tool_policy == INLINE_SMOKE_POLICY else SELECTION_OUTPUT_TOKENS)
        data = dict(data)
        while True:
            if inline and not reference_repair and not bounded_method and not author_restart:
                data["source_projection"] = projection_details(data["implementation_tree"], data["current_source"],
                                                              policy=tool_policy)
            inputs = self.model_inputs(data)
            if role == "test_author" and self.scoped_repairs and isinstance(inputs.get("plan"), dict):
                inputs["plan"], inputs["test_author_plan_projection"] = public_test_author_plan(inputs["plan"])
                inputs["numbered_acceptance_criteria"] = numbered_criteria(inputs["plan"]["acceptance_criteria"])
            if tool_policy == INLINE_SMOKE_POLICY and isinstance(inputs.get("plan"), dict):
                inputs["plan"], inputs["plan_projection"] = public_smoke_plan(inputs["plan"])
                inputs = project_smoke_inputs(inputs, stage)
            if tool_policy == INLINE_SELECTION_POLICY and isinstance(inputs.get("plan"), dict):
                complete_plan = inputs["plan"]
                # Path selection needs the public behavior and current source,
                # not the planner's lengthy proposed implementation narrative.
                inputs["plan"] = {key: value for key, value in complete_plan.items()
                                  if key in {"goal", "acceptance_criteria", "assumptions"}}
                inputs["plan_projection"] = {"plan_digest": digest(complete_plan),
                                             "omitted_fields": sorted(complete_plan.keys() - inputs["plan"].keys())}
            payload = build_model_payload(model_type, instruction, inputs)
            try:
                admitted = validate_project_prompt(payload, self.bundle, role, tokenize=tokenizer)
            except ValueError as error:
                if (not inline or str(error) != "Complete project prompt, schema and framing "
                        "exceed the frozen role context; inference was not dispatched"):
                    raise
            else:
                context = (self.bundle.get("role_profiles") or self.bundle["generation"])[role]["context"]
                if not inline or admitted + output_reserve <= context:
                    break
            if reference_repair:
                raise ValueError("Complete smoke reference correction and output reserve exceed the approved context; inference was not dispatched")
            if bounded_method:
                raise ValueError("Complete reference method correction and output reserve exceed the approved context; inference was not dispatched")
            if author_restart:
                raise ValueError("Independent author restart and its output reserve exceed the approved context")
            data["current_source"] = trim_selection_source(data["current_source"])
        options = {"tool_policy": tool_policy} if tool_policy is not None else {}
        result = self._opencode(stage, role, "generate", visible, payload=payload, **options)
        if "test_author_plan_projection" in inputs:
            self.stages.setdefault(stage, {})["test_author_plan_projection"] = inputs["test_author_plan_projection"]
        if inline:
            if not bounded_method and not author_restart:
                self.stages[stage]["source_projection"] = inputs["source_projection"]
            self.stages[stage]["tool_policy"] = tool_policy
        if tokenizer is not None:
            self.stages[stage]["context_admission"] = {"reserved_prompt_tokens": admitted,
                                                        "tokenizer": approved_tokenizer}
            if inline:
                self.stages[stage]["context_admission"]["reserved_completion_tokens"] = output_reserve
        value = result.get("value")
        if not isinstance(value, dict):
            raise ValueError("OpenCode did not return a structured result")
        return value

    def ask(self, stage, role, model_type, instruction, **data):
        if getattr(self, 'recovery', None) is not None:
            self.recovery.dispatch(stage, role, data)
        if role == "test_author" or (role == "planner" and role in self.workers):
            if self.scoped_repairs and role == "test_author":
                instruction += (REFERENCE_REPAIR_GUIDANCE
                    if isinstance(model_type, type) and issubclass(model_type, (ReferenceMethodPatch, ReferenceDiagnosis)) else AUTHOR_GUIDANCE)
                if isinstance(model_type, type) and issubclass(model_type, IndependentCoveragePatch):
                    instruction += (
                        " For this additive patch, the retained tests supply the already-covered criteria. "
                        "Return coverage only for missing criteria and files only for new tests; never repeat "
                        "the retained suite or its manifest fields.")
            selected_policy = {METHOD_PATCH_POLICY_V2: INLINE_METHOD_PATCH_POLICY,
                               METHOD_PATCH_POLICY_V3: INLINE_METHOD_PATCH_POLICY_V3,
                               METHOD_PATCH_POLICY_V4: INLINE_METHOD_PATCH_POLICY_V4}.get(data.get("method_patch_policy"))
            if data.get("diagnosis_policy") == DIAGNOSIS_POLICY:
                selected_policy = INLINE_DIAGNOSIS_POLICY
            elif data.get("diagnosis_policy") == DIAGNOSIS_POLICY_V2:
                selected_policy = INLINE_DIAGNOSIS_POLICY_V2
            if stage == AUTHOR_RESTART_STAGE:
                selected_policy = INLINE_AUTHOR_RESTART_POLICY
            options = {"tool_policy": selected_policy} if selected_policy else {}
            return self._generate(stage, role, model_type, instruction, data, {}, **options)
        return super().ask(stage, role, model_type, instruction, **data)

    def _repair_reference_test_files(self, suite, diagnostics, policy):
        if not self.scoped_repairs:
            return super()._repair_reference_test_files(suite, diagnostics, policy)
        from .project_environment import frontend_dom_repair_instructions
        stage = self.bounded("build_tests_defect", "max_test_defect_repairs")
        for index, failure in enumerate(sorted(diagnostics, key=lambda item: item["path"]), 1):
            call_stage = f"{stage}_file_{index}"
            path = failure["path"]
            budget = self.contract["test_bytes"] - sum(
                len(file.content.encode("utf-8")) for file in suite.files if file.path != path)
            history = self._reference_history()
            inputs = reference_method_patch_inputs(suite, failure, byte_limit=budget, policy=METHOD_PATCH_POLICY_V4,
                                                    history=history)
            instruction = (
                "Repair the named reference-test failures using definition patches. Return only changes for "
                "authorized targets that actually need different code; do not return a whole file or repeat "
                "unchanged definitions. Copy only the target name and its original_digest metadata into "
                "target and expected_original_digest. That digest is a precondition on the OLD source; it is "
                "not a checksum of your replacement and must not constrain the new code. Read original_source "
                "to diagnose the failure, then write different corrected code in replacement_source. "
                "Use the validated reference_diagnosis to change the actual failed operation and retain its "
                "required behavior. Editing only comments, whitespace or assertion messages does not correct "
                "an operation. Check that replacement_source implements the diagnosed executable change. "
                "reference_attempt_history contains literal earlier failures and proposed definitions. Do not "
                "repeat an operation already observed to fail without new supplied evidence explaining why it "
                "will now work. An applied proposal means the composer accepted the edit, not that it passed. "
                "Copying original_source unchanged is a rejected no-op. Supply one complete changed definition "
                "with its original leading indentation, signature and decorators. "
                "The harness retains every byte outside those definitions and all test names/coverage. "
                "Passing test methods are not editable. read_only_context and read_only_support_files are "
                "context only, not targets. Make corrections inside the named tests or explicitly authorized "
                "helpers; new local helpers/imports may be placed inside a changed definition when needed. "
                "Do not add top-level definitions or statements. Keep the composed file within "
                "max_replacement_utf8_bytes. The complete suite must pass the trusted reference before it freezes."
            )
            dom_brief = (self.frontend_dom_test_brief + frontend_dom_repair_instructions(self.environment)
                if any(marker in target["original_source"] for target in inputs["targets"]
                       for marker in ("#workbench-dom", "run_vitest", "vitest")) else "")
            instruction += dom_brief
            if feedback := self.reference_test_repair_feedback.get(path):
                inputs["previous_rejection"] = feedback
            if notes := self.reference_test_repair_notes.get(path):
                inputs["public_reference_observations"] = notes
            diagnosis_stage = call_stage.replace("build_tests_defect_", "build_tests_diagnose_", 1)
            diagnosis_inputs = reference_diagnosis_inputs(suite, failure, byte_limit=budget,
                                                          policy=DIAGNOSIS_POLICY_V2, history=history)
            for key in ("previous_rejection", "public_reference_observations"):
                if key in inputs:
                    diagnosis_inputs[key] = inputs[key]
            diagnosis_instruction = (
                "Diagnose every named reference-test failure before a separate code correction. Return no "
                "replacement code or pass claim. For each required test, identify the observed failed "
                "operation, explain its cause using supplied failure/source evidence IDs, and state the "
                "concrete executable operation change needed while preserving the public expected behavior. "
                "The complete current test file and every other suite file are read-only context: inspect "
                "passing cases, shared fixtures, helper behavior and execution order when explaining a failure. "
                "Only targets are editable by the later patch step. Evidence IDs refer to the exact retained "
                "failure excerpts or file content; explain the cause in your own words rather than copying "
                "source or computing hashes. Include this case's own failure ID and the source IDs that "
                "support its cause. Distinguish changes to the failing request, fixture or UI operation from "
                "changes only to comments, whitespace, assertion messages or an unrelated later operation. "
                "Use reference_attempt_history to compare the exact prior attempted operations with their "
                "recorded failures; cite relevant prior evidence IDs and explain that comparison in "
                "prior_attempt_assessment. Do not revert to a previously failed operation without new evidence. "
                "A helper throwing before a fallback does not prove that the fallback executed. Do not infer "
                "unobserved DOM selectors, controls or execution paths. Applied edits are not pass results. "
                "A diagnosis is a hypothesis, not proof that a test passed. The entire suite must rerun "
                "against the trusted reference after any patch."
            ) + dom_brief
            # A dispatched call with unknown completion must never be treated as a
            # validation rejection or trigger another inference.
            diagnosis = self.ask(diagnosis_stage, "test_author", reference_diagnosis_type(diagnosis_inputs),
                                 diagnosis_instruction, **diagnosis_inputs)
            self.stages[diagnosis_stage]["defects"] = [{"path": path, "names": failure["names"]}]
            self.stages[diagnosis_stage]["method_patch_scope"] = inputs["scope_projection"]
            try:
                binding = reference_diagnosis_binding(suite, failure, diagnosis, stage=diagnosis_stage,
                                                       byte_limit=budget, policy=DIAGNOSIS_POLICY_V2, history=history)
            except ValueError as error:
                rejection = public_error(error)
                self.stages[diagnosis_stage]["diagnosis_rejected"] = rejection
                self.reference_test_repair_feedback[path] = rejection
                continue
            self.stages[diagnosis_stage]["reference_diagnosis"] = binding["proof"]
            inputs["reference_diagnosis"] = binding
            # Unknown/transport failures never enter the settled patch validation branch.
            raw = self.ask(call_stage, "test_author", reference_method_patch_type(inputs["targets"], policy=METHOD_PATCH_POLICY_V4),
                           instruction, **inputs)
            self.stages[call_stage]["defects"] = [{"path": path, "names": failure["names"]}]
            self.stages[call_stage]["method_patch_scope"] = inputs["scope_projection"]
            self.stages[call_stage]["reference_diagnosis"] = {"stage": diagnosis_stage, **binding["proof"]}
            previous_suite_digest = digest(suite.model_dump())
            rejection = None
            try:
                revised, proof = compose_reference_method_patch(suite, failure, raw, byte_limit=budget,
                                                                policy=METHOD_PATCH_POLICY_V4)
            except (ValueError, SyntaxError) as error:
                rejection = public_error(error)
                self.stages[call_stage]["replacement_rejected"] = rejection
                self.reference_test_repair_feedback[path] = rejection
            else:
                suite = revised
                self.stages[call_stage]["method_patch"] = {**proof, "diagnosis_stage": diagnosis_stage,
                    "diagnosis_digest": binding["proof"]["diagnosis_digest"],
                    "history_projection_digest": binding["proof"]["history_projection_digest"]}
                self.reference_test_repair_feedback.pop(path, None)
            observation = getattr(self, "_reference_observations", {}).get(path)
            if observation is not None:
                record = {**observation, "attempt": reference_attempt_record(call_stage, inputs, raw,
                    previous_suite_digest, digest(suite.model_dump()), rejection)}
                self.evidence["reference_attempt_history"] = history_with_record(self._reference_history(), record)
        return suite

    def _settled_plan_response(self):
        """Return the first planner call only when its journal confirms a settled response."""
        run_id = self.state["run_id"]
        calls = [call for call in self.runtime.store.get(run_id)["calls"] if call["stage"] == "build_plan"]
        if len(calls) != 1:
            return None
        call = calls[0]
        if (call.get("role") != "planner" or call.get("status") != "terminal" or call.get("finished") is None
                or (call.get("backend_diagnostics") or {}).get("native_completion") in
                {"unknown", "not_started_unknown"}):
            return None
        raw = self.runtime.store.cached(run_id, "build_plan")
        return raw if settled_group_response(raw) else None

    def _completed_plan_response(self, value):
        raw = self._settled_plan_response()
        if raw is None or "worker_error" in raw:
            return False
        if "planner" in self.workers:
            return raw.get("value") == value and _stopped_opencode_result(raw)
        try:
            return _strict_json(self.runtime._content(raw)) == value
        except (KeyError, TypeError, ValueError):
            return False

    def _validated_plan(self, value):
        plan = WholeProjectPlan.model_validate(value)
        if getattr(self, 'recovery', None) is not None:
            missing = set((self.environment or {}).get('required_application_files', [])) - set(plan.implementation_files)
            if missing:
                raise ValueError('Plan omitted declared required application files: ' + ', '.join(sorted(missing)))
        return plan

    def _author_plan(self):
        inputs = {"max_utf8_source_bytes": self.source_bytes}
        try:
            value = self.ask("build_plan", "planner", WholeProjectPlan, PLAN_BRIEF, **inputs)
        except OpenCodeOutputLimitError:
            raw = self._settled_plan_response() if self.scoped_repairs and "planner" in self.workers else None
            if (raw is None or raw.get("finish_reason") != "length" or "worker_error" not in raw
                    or raw.get("backend_cleanup_confirmed") is not True
                    or raw.get("scratch_cleanup_confirmed") is not True):
                raise
            self.stages["build_plan"] = {"role": "planner", "model": self.bundle["roles"]["planner"],
                                         "finish_reason": "length"}
            instruction = (" The previous attempt reached its output cap. Start over from the supplied task. "
                           "Keep every required path, and use short single-sentence entries for the other fields.")
        else:
            try:
                return self._validated_plan(value)
            except ValueError as error:
                if not self.scoped_repairs or not isinstance(value, dict) or not self._completed_plan_response(value):
                    raise
                errors = (error.errors(include_input=False, include_context=False, include_url=False)
                          if isinstance(error, ValidationError) else [{'loc': ('implementation_files',), 'msg': str(error)}])
                feedback = {
                    "validation_error": public_error(error)[:800], "error_count": len(errors),
                    "errors": [{"path": ".".join(map(str, item["loc"]))[:200],
                                "message": item["msg"][:400]} for item in errors[:8]],
                    "omitted_errors": max(0, len(errors) - 8), "rejected_plan_digest": digest(value),
                }
                self.stages["build_plan"]["validation_error"] = feedback
                inputs.update(previous_plan_validation=feedback, rejected_plan=value)
                instruction = " Correct the supplied rejected_plan using previous_plan_validation."
        corrected = self.ask(
            "build_plan_retry", "planner", WholeProjectPlan,
            PLAN_BRIEF + instruction + " Return the complete plan. This is the only plan correction attempt.", **inputs,
        )
        return self._validated_plan(corrected)

    def _implement(self, paths):
        stage = "implement"
        instruction = ("Write the complete application in this one session. Implement every approved path. "
                       "Follow the plan and serve the API under its declared prefix. Write source files through "
                       "the workspace tools, and ensure build configuration and imports agree across files. "
                       "Return only a short summary after all files are on disk. Workbench runs tests next.")
        inputs = self.model_inputs({"implementation_files": paths, "max_utf8_source_bytes": self.source_bytes})
        result = self._opencode(
            stage, "implementer", "run", {}, allow_create=True, editable_paths=paths,
            instruction=instruction, inputs=inputs,
        )
        files = result.get("files")
        if not isinstance(files, dict) or not files.keys() <= set(paths):
            raise ValueError("Whole-project implementation changed the approved path inventory")
        missing = [path for path in paths if path not in files]
        if missing:
            if not _stopped_opencode_result(result):
                raise ValueError("Incomplete implementation lacks a settled worker receipt")
            retry_stage = "implement_inventory_retry"
            self.stages[stage]["inventory_retry"] = {"stage": retry_stage, "missing_paths": missing,
                                                      "retained_paths": sorted(files)}
            if files:
                instruction = ("Finish the implementation by creating every approved missing path. The existing files "
                               "are read-only context. Follow their interfaces, the full plan and the original brief. "
                               "Write complete source through the workspace tools, then return a short summary.")
            else:
                instruction += " The previous attempt wrote no files. Start again from the brief and plan."
            recovered = self._opencode(
                retry_stage, "implementer", "run", files, allow_create=True, editable_paths=missing,
                instruction=instruction, inputs=inputs,
            ).get("files")
            if not isinstance(recovered, dict) or recovered.keys() != set(missing):
                raise ValueError("Implementation recovery did not write every missing approved path")
            files = {**files, **recovered}
        return self.artifact_type(summary="Whole-project OpenCode implementation", files=[
            {"path": path, "content": files[path]} for path in paths
        ])

    def _author_self_suite(self, artifact, paths):
        stage = "whole_self_tests"
        suite_type = smoke_test_type(self)
        byte_limit = suite_type.manifest_type.byte_limit
        brief = (
            "Write your own compact executable Python unittest smoke suite for this application. Use valid "
            "fixtures and three to five focused checks for critical behavior, including a meaningful failure path. "
            f"Aim for under 8192 UTF-8 bytes in one or two files. The maximum is {byte_limit} UTF-8 bytes. "
            "Workbench also runs the image's required build and runtime checks. Select the highest-priority smoke "
            "checks and avoid an exhaustive endpoint or UI matrix. Map tested behaviors to criterion numbers "
            "from the plan. Verify API registration and a protected call if the plan declares them. "
            "Use the runtime environment metadata. Copy implementation_files exactly, "
            "and return test source only under tests/test_*.py with the declared unittest discovery command. "
            "The application source is read-only. Workbench will execute your suite."
        )
        data = {"implementation_files": paths}
        source = {file.path: file.content for file in artifact.files}
        options = {}
        if self.scoped_repairs:
            data = {**smoke_author_data(artifact.files), "implementation_files": paths}
            source = {}
            options = {"tool_policy": INLINE_SMOKE_POLICY}
            brief += (
                " Use the public brief, every acceptance criterion and assumption, and the supplied bounded "
                "current_source projection. Source omissions are explicit; do not assume omitted code is absent. "
                "The source is implementation evidence, not authority to weaken the public contract. "
                "No tools or worktree exploration are available. Produce the complete compact suite in this "
                "single structured response. The plan projection intentionally omits implementation pseudocode."
                " unittest.TestCase assertion helpers are instance methods: call them on self in test methods, "
                "not on cls in setUpClass or other classmethods. In class fixtures use explicit conditional "
                "AssertionError checks, preserving the same asserted conditions."
            ) + SMOKE_REQUEST_GUIDANCE
        value = self._generate(stage, "implementer", suite_type, brief, data, source, **options)
        try:
            return validate_self_suite(self, value, artifact)
        except ValueError as error:
            feedback = _suite_feedback(value, error, byte_limit=byte_limit,
                                       file_limit=self.contract["test_files"])
            self.stages[stage]["validation_error"] = feedback["validation_error"]
            if self.scoped_repairs and isinstance(error, SelfTestFixtureError):
                patch_type = smoke_source_patch_type(tuple(sorted({item["path"] for item in error.diagnostics})))
                corrected = self._generate(
                    stage + "_retry", "implementer", patch_type,
                    "Repair only the diagnosed class fixture. Preserve every asserted condition and expected value, "
                    "and leave each existing test method body unchanged. Use an explicit conditional AssertionError "
                    "inside the fixture. Return complete replacement source for changed files only. Workbench "
                    "preserves the test manifest, coverage, commands and assumptions.",
                    {**data, "previous_suite_validation": feedback,
                     "previous_suite": error.suite.model_dump(exclude={"implementation_files"}),
                     "fixture_diagnostics": error.diagnostics}, source, **options,
                )
                revised = validate_self_suite(self, compose_smoke_source_patch(error.suite, patch_type, corrected), artifact)
                return validate_self_fixture_correction(error.suite, revised)
            corrected = self._generate(
                stage + "_retry", "implementer", suite_type,
                brief + " Use the validation feedback to write a corrected complete smoke suite.",
                {**data, "previous_suite_validation": feedback}, source, **options,
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
        suite = self.suite_type.model_validate(value)
        if self.scoped_repairs:
            validate_coverage_indices(suite, self.output["plan"]["acceptance_criteria"])
        return suite

    def _author_independent_suite(self, paths):
        stage = "build_tests"
        brief = (self.test_brief + " This is an independent acceptance suite for the specification. "
                 "The approved implementation file inventory is supplied; return it unchanged.")
        data = {"implementation_files": paths}
        reference_draft = (self.bundle.get("whole_project_seed") or {}).get("independent_reference_draft")
        if reference_draft is not None:
            if not self.scoped_repairs or digest(reference_draft["suite"]) != reference_draft["suite_digest"]:
                raise ValueError("Retained reference-test draft changed before validation")
            self.stages[stage] = {"reused_unfrozen_reference_draft": reference_draft["suite_digest"]}
            suite = self._parse_independent_suite(reference_draft["suite"], paths, stage)
            if digest(suite.model_dump()) != reference_draft["suite_digest"]:
                raise ValueError("Retained reference-test draft required an undeclared normalization")
            return suite
        retained = (self.bundle.get("whole_project_seed") or {}).get("independent_test_draft")
        if retained is not None:
            if not self.scoped_repairs:
                raise ValueError("Retained independent drafts require the scoped whole-project policy")
            raw = retained["suite"]
            if digest(raw) != retained["source_draft_digest"]:
                raise ValueError("Retained independent draft changed before correction")
            self.stages[stage] = {"reused_untrusted_draft": retained["source_draft_digest"]}
        else:
            try:
                raw = self.ask(stage, "test_author", self.suite_type, brief, **data)
            except OpenCodeSettledResponseError as error:
                if not self.scoped_repairs or error.reason not in {
                        "structured-output-invalid", "structured-output-limit", "unverified-wire-format"}:
                    raise
                self.stages.setdefault(stage, {})["recovery"] = {
                    "decision": "restart", "reason": error.reason, "next_stage": AUTHOR_RESTART_STAGE,
                    "settled_response_proof_digest": digest(error.proof),
                }
                stage = AUTHOR_RESTART_STAGE
                raw = self.ask(stage, "test_author", self.suite_type,
                               brief + " Return the complete independent suite as one JSON object now.",
                               **data)
        try:
            suite = self._parse_independent_suite(raw, paths, stage)
        except ValueError as error:
            if retained is not None and not isinstance(error, IndependentCoverageError):
                raise ValueError("Retained independent draft is not a coverage-index correction") from error
            feedback = _suite_feedback(raw, error, byte_limit=self.contract["test_bytes"],
                                       file_limit=self.contract["test_files"])
            self.stages[stage]["validation_error"] = feedback["validation_error"]
            coverage_error = error if isinstance(error, IndependentCoverageError) else None
            if coverage_error is not None:
                criteria = self.output["plan"]["acceptance_criteria"]
                self.stages[stage]["coverage_validation"] = coverage_error.diagnostics
                stage = "build_tests_retry"
                patch_inputs = coverage_patch_inputs(
                    coverage_error.suite, criteria, byte_limit=self.contract["test_bytes"],
                    file_limit=self.contract["test_files"])
                corrected = self.ask(
                    stage, "test_author", coverage_patch_type(coverage_error.diagnostics),
                    "Correct only the independent suite's coverage-index gaps. Return files, coverage and remaps, "
                    "not a complete suite. existing_files and existing_coverage are read-only context; the harness "
                    "retains their bytes and every manifest field exactly. Return one remap for each unknown row, "
                    "using its zero-based coverage_index (the original coverage array row, not the criterion number) "
                    "and exact one-based from_criterion; one-based to_criterion must be the "
                    "already-covered approved criterion that the existing behavior actually tests. Never relabel "
                    "an existing test as missing coverage. Add new executable tests with meaningful assertions "
                    "for every missing criterion in new canonical tests/test_*.py files, and supply their exact "
                    "Class.test_method coverage entries. Stay within max_added_utf8_bytes and max_added_files. "
                    "Do not return old files, old coverage entries, commands, assumptions or implementation paths. "
                    "Existing test defects will be handled separately by syntax, fixture and reference validation."
                    + getattr(self, "frontend_dom_test_brief", ""),
                    **patch_inputs,
                )
                revised = compose_coverage_patch(coverage_error.suite, corrected, criteria)
                self.stages[stage]["coverage_correction"] = {
                    "policy": COVERAGE_PATCH_POLICY,
                    "previous_suite_digest": digest(coverage_error.suite.model_dump()),
                    "patch_digest": digest(corrected),
                    "corrected_suite_digest": digest(revised.model_dump()),
                    "missing_criteria": coverage_error.diagnostics["missing_criteria"],
                    "unknown_criteria": coverage_error.diagnostics["unknown_criteria"],
                }
                return revised
            stage = "build_tests_retry"
            corrected = self.ask(
                stage, "test_author", self.suite_type,
                brief + " Use the validation feedback to write a corrected complete test suite.",
                **data, previous_suite_validation=feedback,
            )
            revised = self._parse_independent_suite(corrected, paths, stage)
            return revised
        if retained is not None:
            raise ValueError("Retained independent draft does not require a coverage-index correction")
        return suite

    def _patch(self, stage, role, artifact, suite, feedback, *, paths=None, compile_patch=True):
        feedback = repair_feedback(artifact, feedback, getattr(self, "_repair_history", []))
        if self.scoped_repairs:
            feedback = focused_feedback(feedback)
            if self._regression_feedback is not None:
                feedback = {**feedback, "previous_rejected_patch": self._regression_feedback}
            if paths is None:
                paths, feedback = self._select_repair_paths(stage, artifact, feedback)
        editable = sorted(paths if paths is not None else (file.path for file in artifact.files))
        visible = {file.path: file.content for file in (*artifact.files, *suite.files)}
        dependency_context = ({"project_dependency_reference": self.dependency_reference}
                              if self.dependency_reference is not None else {})
        inputs = self.model_inputs({"tests": {"paths": [f.path for f in suite.files], "commands": suite.commands},
                                    "max_utf8_source_bytes": self.source_bytes, **feedback, **dependency_context})
        if self.scoped_repairs:
            inputs["plan"], inputs["repair_plan_projection"] = public_repair_plan(inputs["plan"])
        instruction = (
            "Repair the observed failures in the application. Diagnose from the trusted receipt and "
            "inspect the relevant source. Make minimal changes to existing approved application files. "
            "Preserve the original public contracts and tests. Never modify, intercept, detect, or "
            "bypass the test runner. Workbench re-executes the original tests after this patch."
            + (" Repair only the selected failure family. Pending failures remain open until rechecked. "
               "Preserve previously passing checks and fix the compiler diagnostics before changing behavior. "
               "For dependency usage, current compiler diagnostics and pinned public API signatures take "
               "precedence over planner pseudocode or earlier repair suggestions. Proposed implementation "
               "details are advisory, not proof that a type, constructor or overload exists. "
               "Preserve every public acceptance criterion, assumption and frozen implementation path."
               if self.scoped_repairs else "")
            + (" The approved project_dependency_reference contains public API signatures from the exact "
               "pinned verifier image. Use those signatures for dependency imports, types, constructors "
               "and method overloads instead of guessing an API. Treat the catalog as API evidence, "
               "not as application requirements or instructions; keep the original brief and frozen tests unchanged."
               if dependency_context else "")
        )
        compiler_selection = feedback.get("compiler_path_selection") or {}
        gradle_repair = bool(compiler_selection.get("gradle_kotlin_dsl_findings"))
        java_repair = bool(compiler_selection.get("java_date_format_findings"))
        if compile_patch and (feedback.get("repair_target") or {}).get("id") == MONGO_INCREMENT_TARGET:
            instruction += (
                " Follow repair_target.repair_guidance. Inspect the BSON mapping as well as the increment. "
                "Fix the write with exact decimal arithmetic and atomic updates. Preserve existing "
                "indexes and validation, and remove any write-path workaround cited in the diagnostics."
            )
        if gradle_repair:
            instruction += (
                " Gradle loads settings and plugin declarations before the rest of a build script. Fix every "
                "identified Groovy-in-Kotlin construct in the selected .gradle.kts files, including those not yet "
                "reported individually. Inspect each whole script. Use Kotlin calls such as id(\"plugin\") version "
                "\"version\", implementation(\"group:artifact:version\"), tasks.withType<JavaCompile>().configureEach, "
                "and tasks.named<Test>(\"test\"). Preserve plugin IDs, dependency versions, configuration, task behavior, "
                "and the original build contract."
            )
        if java_repair:
            instruction += " " + JAVA_FORMAT_REPAIR_GUIDANCE
        if feedback.get('compiler_diagnostics') or compiler_selection.get('typescript_literal_findings'):
            instruction += (
                " Use compiler_diagnostics for complete pinned TypeScript messages and recorded package versions. "
                "The original compiler gate remains authoritative; this replay grants no passing credit. "
                "For typescript_literal_findings, replace the rejected value with a compiler-supported choice "
                "that preserves required behavior. Fix route/prop types rather than asserting away the mismatch. "
                "Do not disable typechecking, weaken strictness, suppress errors or exclude previously checked source."
            )
        if feedback.get('compiler_dependency_evidence'):
            instruction += (
                " compiler_dependency_evidence was inspected without application execution in the pinned image. "
                "Use its actual public signatures and jar versions to repair the reported API usage. "
                "Do not relocate an absent type to a guessed package or invent a replacement annotation or method. "
                  "Use diagnostic-member projections and pinned JDK enum signatures when supplied. Match the "
                  "declared owner, argument and return types, not only a similar member name. An enum constant "
                  "from a different interface family is not interchangeable. A generic-return helper is not "
                  "automatically a Throwable. Preserve imports still referenced by unchanged source. "
                  "When public_constructors_complete is true, use only declared public constructor arities for "
                  "plain new-calls. An empty list means no public constructor; changing argument counts does not "
                  "invent an overload. Consider supported public factories/instances or a valid extension, "
                  "preserving required behavior such as exact timestamp precision. "
                "Preserve the required behavior; evidence of a class existing does not prove semantic correctness."
            )

        def checked_edits(result, projected):
            files = result.get("files")
            if (not isinstance(files, dict) or not files.keys() <= set(editable)
                    or any(not isinstance(text, str) or text == projected[path] for path, text in files.items())):
                raise ValueError("OpenCode repair returned no changes or exceeded its approved file scope")
            return files

        result = self._opencode(
            stage, role, "run", visible, editable_paths=editable, allow_create=False,
            instruction=instruction, inputs=inputs,
        )
        files = checked_edits(result, visible)
        draft = {**visible, **files}
        selected_draft = {path: draft[path] for path in editable}
        remaining = {}
        if gradle_repair and (findings := gradle_kotlin_dsl_findings(selected_draft)):
            remaining["remaining_gradle_kotlin_dsl"] = findings
        if java_repair and (findings := java_date_format_findings(selected_draft, self.dependency_reference)):
            remaining["remaining_java_date_format"] = findings
        if feedback.get('compiler_dependency_evidence'):
            from .compiler_api import inspect_failure, missing_import_findings
            proposed = self.artifact_type(summary=artifact.summary, files=[
                {'path': file.path, 'content': files.get(file.path, file.content)} for file in artifact.files])
            inspected = inspect_failure(self, stage + '_api_after', proposed, feedback)
            if inspected is not None and (findings := missing_import_findings(proposed.files, inspected)):
                remaining['remaining_dependency_imports'] = findings
                remaining['compiler_dependency_evidence'] = inspected
            from .java_source_evidence import patch_findings
            prior = feedback['compiler_dependency_evidence']
            facts = [prior]
            if (inspected is not None and inspected.get('image_id') == prior.get('image_id')
                    and inspected.get('classpath_roots') == prior.get('classpath_roots')):
                facts.append(inspected)
            findings = {}
            for evidence in facts:
                if evidence.get('policy') != 'pinned-java-api-probe-v2':
                    continue
                for finding in patch_findings([f for f in artifact.files if f.path in editable],
                        [f for f in proposed.files if f.path in editable],evidence):
                    key = (finding['path'],finding['line'],finding['kind'],finding.get('owner'),
                           finding.get('constant'),finding.get('import'),finding.get('argument_count'))
                    findings[key] = finding
            if findings:
                remaining['remaining_java_api_source'] = list(findings.values())[:64]
                remaining['compiler_dependency_evidence'] = inspected or prior
        if (not files or remaining) and _stopped_opencode_result(result):
            retry_stage = stage + "_edit_retry"
            self.stages[stage]["edit_retry"] = retry_stage
            retry_note = " The previous attempt made no changes. Apply the repair in the workspace before returning."
            if remaining:
                self.stages[stage].update(remaining)
                retry_note = (" The workspace includes the previous attempt. Correct every remaining source finding "
                              "before returning. Preserve changes that already address the failure.")
                inputs = {**inputs, **remaining}
            retried = self._opencode(
                retry_stage, role, "run", draft, editable_paths=editable, allow_create=False,
                instruction=instruction + retry_note, inputs=inputs,
            )
            files = {path: text for path, text in {**files, **checked_edits(retried, draft)}.items()
                     if text != visible[path]}
        if not files:
            raise ValueError("OpenCode repair returned no changes or exceeded its approved file scope")
        patch = self.artifact_type(summary="Whole-project OpenCode repair", files=[
            {"path": path, "content": text} for path, text in files.items()
        ])
        self.stages[stage]["replaced_paths"] = list(files)
        if self.scoped_repairs:
            self.stages[stage]["selected_paths"] = editable
        result = replace_selected(artifact, patch, editable, allow_subset=True, artifact_type=self.artifact_type)
        history = getattr(self, "_repair_history", [])
        history.append(repair_record(stage, artifact, result, feedback))
        self._repair_history = history[-16:]
        self.stages[stage]["repair_attempt_evidence"] = history[-1]
        if self.scoped_repairs and compile_patch:
            grouping = SimpleNamespace(contract=self.output["plan"], groups=[])
            result = self.compile_after_repair(artifact, result, suite, grouping, set(editable),
                                              {"repair_target": feedback["repair_target"]}
                                              if feedback.get("repair_target") else None, exact_edits=True)
        self.stage = stage
        return result

    def _self_check(self, artifact, stage, index):
        result = execute_self_test(self, artifact, self.self_suite, stage, index)
        result["implementation_digest"] = digest(artifact.model_dump())
        self.stages[stage] = {"status": result["status"], "project_digest": result["project_digest"],
                              "suite_digest": result["suite_digest"]}
        if "repair_target" in result:
            self.stages[stage]["repair_target"] = result["repair_target"]
        if result['status'] == 'passed' and getattr(self, 'recovery', None) is not None:
            self.recovery.verified(stage, result['execution'])
        return result

    def _save_self_candidate(self, artifact, *, repair_stage=None, predecessor=None):
        """Preserve settled source and the fixed smoke suite, without claiming a pass."""
        attempt = self.counters["whole_self_repair"]
        stage = f"whole_self_candidate_{attempt}"
        value = {"policy": "whole-self-candidate-v1", "run_id": self.state["run_id"],
                 "approval_digest": digest(self.bundle), "plan_digest": digest(self.output["plan"]),
                 "artifact": artifact.model_dump(), "artifact_digest": digest(artifact.model_dump()),
                 "suite": self.self_suite.model_dump(), "suite_digest": digest(self.self_suite.model_dump()),
                 "self_repair_attempts": attempt, "repair_stage": repair_stage,
                 "predecessor_artifact_digest": predecessor,
                 "reference_validation": self.evidence.get("self_reference_validation")}
        self.runtime.store.save_artifact(self.state["run_id"], stage, "raw", value)
        record = {key: value[key] for key in ("artifact_digest", "suite_digest", "self_repair_attempts",
                                             "repair_stage", "predecessor_artifact_digest")}
        record.update(stage=stage, digest=digest(value))
        self.evidence.setdefault("self_candidates", []).append(record)
        self.evidence["last_self_candidate"] = record
        self.stages[stage] = {"status": "candidate", **record}
        self.runtime.store.snapshot_result(self.state["run_id"], {"output": self.output, "stages": self.stages})

    def _verify_self_suite(self, artifact):
        """All required checks must pass before independent test authorship starts."""
        predecessor = repair_stage = None
        while True:
            attempt = self.counters["whole_self_repair"]
            self._save_self_candidate(artifact, repair_stage=repair_stage, predecessor=predecessor)
            result = self._self_check(artifact, f"whole_self_verify_{attempt}", 8000 + attempt)
            self.evidence["self_checks"].append(result)
            if result["status"] == "passed":
                return artifact
            if attempt >= MAX_SELF_REPAIRS or not self.authorize_source_repair(artifact, self.self_suite, result):
                self.output["artifact"] = artifact.model_dump()
                self.output["error"] = {"stage": self.stage, "message": result.get("fixture_failure", {}).get(
                    "message", "Self-test verification did not pass every required check"
                    + (" after the self-repair budget" if attempt >= MAX_SELF_REPAIRS else ""))}
                return None
            next_attempt = attempt + 1
            repair_stage = "whole_self_repair" if next_attempt == 1 else f"whole_self_repair_{next_attempt}"
            if not self.repair_time_available(repair_stage):
                self.output["artifact"] = artifact.model_dump()
                self.output["error"] = {"stage": self.stage, "message": "Not enough time remains for self repair and verification"}
                return None
            self.counters["whole_self_repair"] = next_attempt
            self.evidence["budget_counters"] = dict(self.counters)
            self.runtime.store.snapshot_result(self.state["run_id"], {"output": self.output, "stages": self.stages})
            predecessor = digest(artifact.model_dump())
            self._self_compile_round = next_attempt
            try:
                revised = self._patch(repair_stage, self.self_repair_role, artifact, self.self_suite,
                                      _smoke_repair_feedback(result))
            finally:
                self._self_compile_round = None
            if revised is None:
                self.output["artifact"] = artifact.model_dump()
                return None
            artifact = revised

    def before_acceptance(self, artifact):
        if self.scoped_repairs and self.output["verification"].get("quarantined"):
            self.output["error"] = {"stage": self.stage,
                                    "message": "Scoped acceptance cannot complete with quarantined independent tests"}
            return False, None
        checks = self.evidence["final_self_checks"]
        index = len(checks)
        stage = "whole_self_verify_final" if not index else f"whole_self_verify_final_{index}"
        workspace = (8100 if self.scoped_repairs else 8002) + index
        result = self.evidence["final_self_check"] = self._self_check(artifact, stage, workspace)
        checks.append(result)
        if result["status"] == "passed":
            ready, candidate = self._regression_acceptance(artifact)
            if not ready:
                return ready, candidate
            from .whole_project_gates import final_gates
            return final_gates(self, candidate)
        if (self.output["repair_attempts"] < self.limits["max_repair_attempts"]
                and self.authorize_source_repair(artifact, self.self_suite, result)):
            stage = self.bounded("repair", "max_repair_attempts")
            self.output["repair_attempts"] += 1
            return False, self._patch(stage, "repairer", artifact, self.self_suite,
                                      _smoke_repair_feedback(result))
        self.output["error"] = {"stage": self.stage, "message": result.get("fixture_failure", {}).get(
            "message", "Final self-test verification did not pass")}
        return False, None

    def _regression_acceptance(self, artifact):
        """A separate immutable regression suite supplements every existing acceptance gate."""
        binding = self.bundle.get("whole_project_regression_suite")
        if binding is None:
            return True, artifact
        if not self.scoped_repairs:
            raise ValueError("Supplemental regression verification requires the scoped whole-project policy")
        from .whole_project_regression import regression_cases_passed, validate_regression_suite
        suite = validate_regression_suite(binding, self.bundle, self.output["plan"]["implementation_files"])
        checks = self.evidence.setdefault("regression_checks", [])
        index = len(checks)
        if index > self.limits['max_repair_attempts']:
            raise ValueError("Supplemental regression verification exceeded its finite repair namespace")
        stage = f"whole_regression_verify_{index}"
        before_digest = digest(artifact.model_dump())
        result = execute_self_test(self, artifact, suite, stage, 8200 + index)
        if result["status"] == "passed" and not regression_cases_passed(result["execution"], suite):
            result["status"] = "review_required"
        result.update(stage=stage, implementation_digest=before_digest,
                      binding_digest=digest(binding), image_id=self.bundle["sandbox"]["image_id"],
                      receipt_digest=digest(result["execution"]),
                      checks_digest=digest(result["sandbox_stage_results"]))
        checks.append(result)
        self.evidence["final_regression_check"] = result
        self.stages[stage] = {key: result[key] for key in (
            "status", "project_digest", "suite_digest", "implementation_digest", "binding_digest",
            "image_id", "receipt_digest", "checks_digest")}
        # A supplemental failure must retain the current settled source even if
        # the following writer is interrupted or the remaining budget is zero.
        self.output["artifact"] = artifact.model_dump()
        self.runtime.store.snapshot_result(self.state["run_id"], {"output": self.output, "stages": self.stages})
        if result["status"] == "passed":
            if getattr(self, 'recovery', None) is not None:
                self.recovery.verified(stage, result['execution'])
            return True, artifact
        if (self.output["repair_attempts"] < self.limits["max_repair_attempts"]
                and self.authorize_source_repair(artifact, suite, result)
                and self.repair_time_available(f"repair_{self.counters['repair'] + 1}")):
            repair_stage = self.bounded("repair", "max_repair_attempts")
            self.output["repair_attempts"] += 1
            replacement = self._patch(repair_stage, "repairer", artifact, suite,
                                      _smoke_repair_feedback(result))
            if replacement is not None:
                record = {"stage": repair_stage, "verification_stage": stage,
                          "predecessor_artifact_digest": before_digest,
                          "artifact_digest": digest(replacement.model_dump()),
                          "suite_digest": result["suite_digest"], "binding_digest": digest(binding)}
                self.evidence.setdefault("regression_repairs", []).append(record)
                self.output["artifact"] = replacement.model_dump()
                self.runtime.store.snapshot_result(self.state["run_id"], {"output": self.output, "stages": self.stages})
            # The inherited loop must rerun independent verification and full
            # review, then self-tests and this same regression suite.
            return False, replacement
        self.output["error"] = {"stage": stage, "message": result.get("fixture_failure", {}).get(
            "message", "Immutable supplemental regression verification did not pass")}
        return False, None

    def repair(self, artifact, suite, baseline, plan, review=None, *, focus=None):
        stage = self.bounded("repair", "max_repair_attempts")
        self.output["repair_attempts"] += 1
        evidence = {"failed_execution": failure_feedback(baseline, include_stage_results=True)} if baseline else {}
        recovery_request = getattr(self, '_verification_recovery_request', None)
        if recovery_request is not None:
            evidence['stage_recovery_request'] = recovery_request
            self._verification_recovery_request = None
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

    def _restore_test_repair(self, checkpoint):
        suite = self.suite_type.model_validate(checkpoint["suite"])
        result = checkpoint["reference_failure"]
        prior = checkpoint["verification"]
        counters = checkpoint["counters"]
        limits = {"build_tests_syntax": "max_test_syntax_repairs", "build_tests_fixture": "max_test_fixture_repairs",
                  "build_tests_coverage": "max_test_coverage_repairs"}
        if (not isinstance(counters, dict) or counters.keys() - limits.keys()
                or any(type(count) is not int or not 0 < count <= self.limits[limits[label]]
                       for label, count in counters.items())
                or not isinstance(prior, dict) or prior.keys() != {"controls", "test_preflights", "self_checks"}
                or not prior["controls"] or not self.reference
                or prior["controls"][-1].get("reference_digest") != self.reference["digest"]
                or prior["controls"][-1].get("receipt_digest") != digest(result)
                or suite.implementation_files != self.output["plan"]["implementation_files"]
                or not actionable_failure(result) or validate_baseline(result, suite)):
            raise ValueError("Saved checkpoint does not contain a matching, actionable reference failure")
        problems = reference_test_failures(suite, result)
        if not problems:
            raise ValueError("Saved reference failure has no repairable tests")
        self.counters.update(counters)
        self.output["verification"].update(controls=list(prior["controls"]), test_preflights=list(prior["test_preflights"]))
        self.evidence["self_checks"] = list(prior["self_checks"])
        return suite, result, problems

    def _recovered_test_suite(self, saved_suite, provenance):
        from .project_build import REFERENCE_TEST_REPAIR, validate_reference_test_replacement

        candidate = self.bundle["reference_test_repair_candidate"]
        if (self.bundle.get("reference_test_repair") != REFERENCE_TEST_REPAIR or not isinstance(candidate, dict)
                or candidate.keys() != {"source_run_id", "reference_failure_digest", "suite", "suite_digest",
                                        "original_response_sha256", "syntax_diagnostic"}
                or candidate["source_run_id"] != provenance["source_run_id"]
                or candidate["reference_failure_digest"] != provenance["source_reference_failure_digest"]
                or candidate["suite_digest"] != digest(candidate["suite"])
                or not re.fullmatch(r"[a-f0-9]{64}", candidate["original_response_sha256"])):
            raise ValueError("Recovered test response differs from the approved saved trial")
        suite = self.suite_type.model_validate(candidate["suite"])
        if suite.model_dump(exclude={"files"}) != saved_suite.model_dump(exclude={"files"}):
            raise ValueError("Recovered test response changed the test manifest contract")
        before = {file.path: file for file in saved_suite.files}
        after = {file.path: file for file in suite.files}
        path = candidate["syntax_diagnostic"]["path"]
        if before.keys() != after.keys() or {name for name in before if before[name] != after[name]} != {path}:
            raise ValueError("Recovered test response must replace exactly its diagnosed file")
        from .build_coverage import python_test_names
        names = python_test_names({path: before[path].content})[path]
        validate_reference_test_replacement(before[path], after[path], names, self.contract["test_bytes"])
        self.evidence["recovered_test_response"] = {name: value for name, value in candidate.items() if name != "suite"}
        return suite

    def execute(self):
        from .whole_project_gates import VerificationGateStop
        try:
            status = self._execute()
            if status != 'completed' and getattr(self, 'recovery', None) is not None:
                error = self.output.get('error') or {'stage': self.stage, 'message': 'Stage did not meet its required gate'}
                self.recovery.terminal(error['stage'], error['message'])
            return status
        except (IndependentSuiteRepairExhausted, VerificationGateStop) as error:
            self.output["error"] = {"stage": self.stage, "message": str(error)}
            if getattr(self, 'recovery', None) is not None:
                self.recovery.terminal(self.stage, public_error(error))
            return "review_required"
        except Exception as error:
            if getattr(self, 'recovery', None) is not None:
                self.recovery.terminal(self.stage, public_error(error))
            raise

    def _execute(self):
        if 'fresh_project' in self.bundle:
            from .whole_project_setup import validate_fresh_run
            validate_fresh_run(self.bundle)
        required_paths = (self.environment or {}).get("required_application_files", [])
        if "whole_project_checkpoint" in self.bundle:
            if not self.scoped_repairs or "whole_project_seed" in self.bundle:
                raise ValueError("A whole-project checkpoint requires its scoped repair policy and no draft seed")
            from .whole_project_checkpoint import restore_checkpoint, save_checkpoint
            plan, artifact, suite = restore_checkpoint(self)
            if validate_smoke_reference(self, artifact, self.self_suite, allow_repair=False) is None:
                return "review_required"
            grouping = SimpleNamespace(contract=plan.model_dump(), groups=[])
            artifact = self._compile_initial(artifact, self.self_suite, grouping)
            if artifact is None:
                return "review_required"
            suite = self.check_suite(suite)
            self.evidence["independent_suite_digest"] = self.output["suite_digest"]
            self.runtime.store.save_artifact(self.state["run_id"], "build_frozen", "raw", {
                **self.stages["build_frozen"], "plan_digest": digest(plan.model_dump()),
                "suite": suite.model_dump(), "suite_digest": self.output["suite_digest"],
            })
            save_checkpoint(self, artifact, suite, "tests")
            return self.verify_and_repair(artifact, suite, grouping)
        from .compile_continuation import KEY as COMPILE_CONTINUATION, restore as restore_compiler
        seeded = "whole_project_seed" in self.bundle or COMPILE_CONTINUATION in self.bundle
        if COMPILE_CONTINUATION in self.bundle:
            plan, artifact = restore_compiler(self)
        elif seeded:
            plan, artifact, provenance = validate_whole_project_seed(
                self.bundle["whole_project_seed"], self.artifact_type, required_paths,
            )
            self.evidence["draft_provenance"] = provenance
            if self.scoped_repairs and "source_self_repair_attempts" in provenance:
                self.counters["whole_self_repair"] = provenance["source_self_repair_attempts"]
                self.evidence["budget_counters"] = dict(self.counters)
        else:
            plan = self._author_plan()
        self.output["plan"] = plan.model_dump()
        paths = plan.implementation_files
        if not seeded:
            if not set(required_paths) <= set(paths):
                raise ValueError("Whole-project plan omitted required application files")
            artifact = self._implement(paths)
        self.evidence.update(draft_digest=digest(artifact.model_dump()), implementation_paths=paths)
        if self.scoped_repairs:
            artifact = self._compile_initial(artifact, SimpleNamespace(files=[], commands=[]),
                                             SimpleNamespace(contract=plan.model_dump(), groups=[]))
            if artifact is None:
                return "review_required"
        seed = self.bundle.get("whole_project_seed") or {}
        checkpoint = seed.get("independent_test_repair")
        if checkpoint is not None:
            suite, reference_failure, problems = self._restore_test_repair(checkpoint)
        if "self_suite" in seed:
            smoke_run = seed["provenance"].get("source_self_tests_run_id")
            self.stage = "whole_self_tests" if smoke_run else "whole_self_tests_retry"
            self.self_suite = validate_self_suite(self, seed["self_suite"], artifact)
            self.stages[self.stage] = {"reused_from": smoke_run or seed["provenance"]["source_run_id"]}
        else:
            self.self_suite = self._author_self_suite(artifact, paths)
        checked_smoke = validate_smoke_reference(self, artifact, self.self_suite,
                                                 allow_repair="self_suite" not in seed)
        if checked_smoke is None:
            return "review_required"
        self.self_suite = checked_smoke
        self.evidence["self_suite_digest"] = digest(self.self_suite.model_dump())
        self.evidence["self_suite"] = self.self_suite.model_dump()
        if checkpoint is not None:
            if "reference_test_repair_candidate" in self.bundle:
                suite = self._recovered_test_suite(suite, provenance)
            else:
                suite = self.repair_reference_failures(suite, problems, reference_failure)
        else:
            if self.scoped_repairs:
                artifact = self._verify_self_suite(artifact)
                if artifact is None:
                    return "review_required"
            else:
                result = self._self_check(artifact, "whole_self_verify_0", 8000)
                self.evidence["self_checks"].append(result)
                if result["status"] == "failed":
                    artifact = self._patch("whole_self_repair", self.self_repair_role, artifact, self.self_suite,
                                           {"failed_execution": result["feedback"]})
                    if artifact is None:
                        return "review_required"
                    result = self._self_check(artifact, "whole_self_verify_1", 8001)
                    self.evidence["self_checks"].append(result)
                if result["status"] == "review_required":
                    self.output["error"] = {"stage": self.stage, "message": "Self-test execution did not produce a trusted receipt"}
                    return "review_required"
            suite = self._author_independent_suite(paths)
        suite = self.check_suite(suite)
        if suite.implementation_files != paths:
            raise ValueError("Test-suite repair changed the approved implementation path inventory")
        self.evidence["independent_suite_digest"] = self.output["suite_digest"]
        self.runtime.store.save_artifact(self.state["run_id"], "build_frozen", "raw", {
            **self.stages["build_frozen"], "plan_digest": digest(self.output["plan"]),
            "suite": suite.model_dump(), "suite_digest": self.output["suite_digest"],
        })
        if self.scoped_repairs:
            from .whole_project_checkpoint import save_checkpoint
            save_checkpoint(self, artifact, suite, "tests")
        return self.verify_and_repair(artifact, suite, SimpleNamespace(contract=plan.model_dump(), groups=[]))
