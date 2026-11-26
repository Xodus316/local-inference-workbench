"""Hash-bound whole-project checkpoints. Resumes re-execute every acceptance gate."""

from copy import deepcopy
import math
import re
from uuid import UUID

from .store import digest


POLICY = "whole-project-checkpoint-v1"
REPAIR_POLICY = "verified-scoped-repair-v1"
_SHA = re.compile(r"[a-f0-9]{64}")
_COMPILE_COUNTER = re.compile(r"whole_compile_(initial|\d+)_(\d+)")
_SELF_COMPILE_COUNTER = re.compile(r"whole_compile_self_(\d+)_(\d+)")
_BINDINGS = (
    "request", "sandbox", "roles", "generation", "role_profiles", "build", "build_contract",
    "project_environment", "whole_project_workflow", "whole_project_source_bytes",
    "whole_project_repair_policy", "whole_project_engine_digest", "whole_project_prompt_tokenizers",
    "reference_test_repair", "run_timeout_seconds", "project_dependency_reference", "whole_project_regression_suite",
    "whole_project_test_author_policy", "whole_project_test_author_migration",
    "same_model_role_isolation",
    "project_review_policy", "whole_project_gate_policy", "fresh_project",
    "whole_project_compile_progress_policy",
    "whole_project_stage_recovery_policy",
    "whole_project_java_api_probe",
    "whole_project_frontend_diagnostics",
    "whole_project_candidate_source_contract",
    "whole_project_recovery_limits",
)


def binding_digest(bundle):
    """Only the owned HyperQwen bridge's ephemeral address may change on resume."""
    workers = deepcopy(bundle.get("whole_project_workers", {}))
    for worker in workers.values():
        if worker.get("backend") == "hyperqwen-vllm":
            worker.pop("endpoint", None)
    return digest({**{key: bundle.get(key) for key in _BINDINGS
                      if key not in {"run_timeout_seconds", "project_dependency_reference", "whole_project_regression_suite",
                                     "whole_project_test_author_policy", "whole_project_test_author_migration",
                                     "same_model_role_isolation", "project_review_policy", "whole_project_gate_policy", "fresh_project",
                                     "whole_project_compile_progress_policy", "whole_project_stage_recovery_policy",
                                     "whole_project_java_api_probe", "whole_project_frontend_diagnostics", "whole_project_candidate_source_contract",
                                     "whole_project_recovery_limits"}
                      or key in bundle}, "workers": workers,
                   "reference_digest": (bundle.get("project_reference") or {}).get("digest")})


def validate_checkpoint(value, bundle):
    from .whole_project import WholeProjectPlan, whole_project_types
    from .project_build import project_test_types

    fields = {"policy", "source_run_id", "source_approval_digest", "binding_digest", "phase", "plan",
              "artifact", "suite", "self_suite", "suite_digest", "artifact_digest", "counters",
              "repair_attempts", "compile_repair_attempts", "unverified_criteria", "quarantined"}
    if (not isinstance(value, dict) or value.keys() != fields or value["policy"] != POLICY
            or bundle.get("whole_project_repair_policy") != REPAIR_POLICY
            or value["binding_digest"] != binding_digest(bundle)
            or not isinstance(value["source_approval_digest"], str)
            or not _SHA.fullmatch(value["source_approval_digest"])
            or value["phase"] not in {"tests", "verification", "review"}):
        raise ValueError("Whole-project checkpoint differs from the approved code, models, task or environment")
    UUID(value["source_run_id"])
    artifact_type, _ = whole_project_types(bundle)
    suite_type, _ = project_test_types(bundle)
    plan = WholeProjectPlan.model_validate(value["plan"])
    artifact = artifact_type.model_validate(value["artifact"])
    suite = suite_type.model_validate(value["suite"])
    smoke = suite_type.model_validate(value["self_suite"])
    paths = set(plan.implementation_files)
    if (paths != {file.path for file in artifact.files} or paths != set(suite.implementation_files)
            or paths != set(smoke.implementation_files)
            or not set((bundle.get("project_environment") or {}).get("required_application_files", [])) <= paths
            or value["suite_digest"] != digest(suite.model_dump())
            or value["artifact_digest"] != digest(artifact.model_dump())):
        raise ValueError("Whole-project checkpoint source, tests or inventory changed")
    counters = value["counters"]
    if (not isinstance(counters, dict)
            or any(not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", key)
                   or type(count) is not int or count < 0 for key, count in counters.items())
            or any(type(value[key]) is not int or value[key] < 0
                   for key in ("repair_attempts", "compile_repair_attempts"))
            or counters.get("repair", 0) != value["repair_attempts"]
            or value["repair_attempts"] > bundle["build"]["max_repair_attempts"]):
        raise ValueError("Whole-project checkpoint has invalid repair counters")
    compile_total = 0
    from .stage_recovery import validate_costs
    validate_costs(bundle, counters)
    from .compile_progress import additional_repairs
    compile_limit = bundle["build_contract"]["max_compile_repairs"] + additional_repairs(bundle)
    if not 0 <= counters.get("whole_self_repair", 0) <= 3:
        raise ValueError("Whole-project checkpoint has invalid self-repair budget")
    for key, count in counters.items():
        if not key.startswith("whole_compile_"):
            continue
        contract = bundle["build_contract"]
        self_match = _SELF_COMPILE_COUNTER.fullmatch(key)
        if self_match is not None:
            if (not 1 <= int(self_match[1]) <= counters.get("whole_self_repair", 0)
                    or not 1 <= int(self_match[2]) <= contract["max_compile_checkpoints"]
                    or count > compile_limit):
                raise ValueError("Whole-project checkpoint has invalid self compiler budgets")
            compile_total += count
            continue
        match = _COMPILE_COUNTER.fullmatch(key)
        if (match is None or not 1 <= int(match[2]) <= contract["max_compile_checkpoints"]
                or (match[1] != "initial" and int(match[1]) > value["repair_attempts"])
                or count > compile_limit):
            raise ValueError("Whole-project checkpoint has invalid per-component compile budgets")
        compile_total += count
    if compile_total != value["compile_repair_attempts"]:
        raise ValueError("Whole-project checkpoint compile counters do not account for every spent attempt")
    if value["unverified_criteria"] or value["quarantined"]:
        raise ValueError("Restore complete reference-validated coverage before resuming acceptance")
    return plan, artifact, suite, smoke


def save_checkpoint(workflow, artifact, suite, phase="verification"):
    if workflow.bundle.get("whole_project_repair_policy") != REPAIR_POLICY:
        return None
    # An incomplete suite is recorded by build_frozen, never promoted to a resumable acceptance checkpoint.
    if workflow.unverified_criteria or (workflow.output.get("verification") or {}).get("quarantined"):
        return None
    checkpoint = {
        "policy": POLICY, "source_run_id": workflow.state["run_id"],
        "source_approval_digest": digest(workflow.bundle), "binding_digest": binding_digest(workflow.bundle),
        "phase": phase, "plan": workflow.output["plan"], "artifact": artifact.model_dump(),
        "suite": suite.model_dump(), "self_suite": workflow.self_suite.model_dump(),
        "suite_digest": digest(suite.model_dump()), "artifact_digest": digest(artifact.model_dump()),
        "counters": dict(workflow.counters), "repair_attempts": workflow.output["repair_attempts"],
        "compile_repair_attempts": workflow.output.get("compile_repair_attempts", 0),
        "unverified_criteria": [], "quarantined": [],
    }
    validate_checkpoint(checkpoint, workflow.bundle)
    records = workflow.evidence.setdefault("checkpoints", [])
    stage = f"whole_checkpoint_{len(records) + 1}"
    workflow.runtime.store.save_artifact(workflow.state["run_id"], stage, "raw", checkpoint)
    records.append({"stage": stage, "digest": digest(checkpoint), "phase": phase,
                    "artifact_digest": checkpoint["artifact_digest"], "suite_digest": checkpoint["suite_digest"]})
    # Bind the checkpoint index to the durable journal now. An unexpected later
    # exception must not leave a valid checkpoint file with no recorded lineage.
    workflow.runtime.store.snapshot_result(workflow.state["run_id"],
        {"output": workflow.output, "stages": workflow.stages})
    return stage


def restore_checkpoint(workflow):
    checkpoint = workflow.bundle["whole_project_checkpoint"]
    plan, artifact, suite, smoke = validate_checkpoint(checkpoint, workflow.bundle)
    workflow.output.update(plan=plan.model_dump(), repair_attempts=checkpoint["repair_attempts"],
                           compile_repair_attempts=checkpoint["compile_repair_attempts"])
    workflow.counters.update(checkpoint["counters"])
    workflow.self_suite = smoke
    workflow.evidence.update(self_suite=smoke.model_dump(), self_suite_digest=digest(smoke.model_dump()),
                            resumed_checkpoint={"source_run_id": checkpoint["source_run_id"],
                                                "digest": digest(checkpoint), "phase": checkpoint["phase"]})
    # No old verification or review is credited: current reference, source and frozen suite run again.
    return plan, artifact, suite


def import_checkpoint(row, value, *, checkpoint_digest, elapsed_seconds, timeout_seconds):
    """Validate a terminal journal projection; host must first verify it against the closed store."""
    bundle = row.get("bundle") or {}
    if (row.get("status") not in {"failed", "review_required", "cancelled"} or not row.get("approved")
            or row.get("digest") != digest(bundle) or (row.get("approval") or {}).get("digest") != row["digest"]
            or any(call.get("status") not in {"terminal", "not_dispatched"} for call in row.get("calls", []))
            or digest(value) != checkpoint_digest or value.get("source_run_id") != row["id"]
            or value.get("source_approval_digest") != row["digest"]):
        raise ValueError("Resume requires an unchanged checkpoint from a settled, approved run")
    output = (row.get("result") or {}).get("output") or {}
    if (output.get("verification") or {}).get("quarantined"):
        raise ValueError("A source run with quarantined tests cannot supply an acceptance checkpoint")
    records = (output.get("verification") or {}).get("whole_project", {}).get("checkpoints", [])
    if not records or records[-1].get("digest") != checkpoint_digest:
        raise ValueError("Resume requires the latest recorded whole-project checkpoint")
    result = deepcopy(value)
    # A model attempt may fail after the last source checkpoint; never regain its spent allowance.
    result["repair_attempts"] = max(result["repair_attempts"], output.get("repair_attempts", 0))
    result["compile_repair_attempts"] = max(result["compile_repair_attempts"], output.get("compile_repair_attempts", 0))
    spent = (output.get("verification") or {}).get("whole_project", {}).get("budget_counters", {})
    if (not isinstance(spent, dict) or any(not isinstance(key, str) or type(count) is not int or count < 0
                                          for key, count in spent.items())):
        raise ValueError("Terminal whole-project budget counters are invalid")
    for key, count in spent.items():
        result["counters"][key] = max(result["counters"].get(key, 0), count)
    result["counters"]["repair"] = result["repair_attempts"]
    validate_checkpoint(result, bundle)
    if type(elapsed_seconds) not in (int, float) or not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
        raise ValueError("The original whole-project elapsed time is invalid")
    if timeout_seconds is None:
        approval = row.get("approval") or {}
        if ("run_timeout_seconds" not in bundle or bundle["run_timeout_seconds"] is not None
                or "deadline" not in approval or approval["deadline"] is not None):
            raise ValueError("Unlimited checkpoint resume requires the original explicit deadline approval")
        return result, None
    if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or timeout_seconds - elapsed_seconds < 10):
        raise ValueError("The original whole-project time allowance is exhausted")
    return result, math.floor(timeout_seconds - elapsed_seconds)
