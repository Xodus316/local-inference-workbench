"""Fresh-approval imports of validated offline-project checkpoints; never resume a run.

The caller resolves an authentic local journal row and exact artifact bytes.
Projection callbacks adapt an approved host bundle/call without replacing its
original approval identity. This module has no filesystem, provider or executor.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import math
import re
import uuid
from typing import Literal

from pydantic import Field, model_validator

from . import build_coverage as coverage
from .build_discovery import normalize_authored_test_paths, validate_discovery_suite
from .build_models import BuildPlan
from .build_workflow import fixture_diagnostics, validate_paths
from .ollama import _strict_json
from .schemas import Record
from .store import digest, encoded

POLICY = "offline-project-reuse-v1"
KNOWN_TERMINAL = {"completed", "failed", "cancelled", "stopped"}


class ReuseSelection(Record):
    through: Literal["plan", "tests", "group_plan", "implementation"]
    completed_groups: int = Field(default=0, ge=0, le=32)

    @model_validator(mode="after")
    def explicit_prefix(self):
        if (self.through == "implementation") != (self.completed_groups > 0):
            raise ValueError("Only implementation reuse selects a nonempty contiguous group prefix")
        return self


@dataclass(frozen=True)
class ProjectCheckpoint:
    plan: object
    suite: object | None
    group_plan: object | None
    groups: tuple
    provenance: dict


def _require(value, message):
    if not value:
        raise ValueError(message)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _revision(value):
    _require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value), "Exact SDK revision required for reuse provenance")
    return value


def _compatibility(bundle):
    from .project_build import REVIEWED_POLICY, project_enabled
    from .project_environment import validate_project_environment
    _require(project_enabled(bundle), "Checkpoint reuse requires the explicit offline project profile")
    request = bundle["request"]
    _require(not bundle.get("web_research") and not request.get("research"), "Checkpoint reuse cannot change online inputs")
    image = bundle["sandbox"].get("image_id")
    _require(isinstance(image, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", image), "An immutable environment image is required")
    role_names = ("planner", "test_author", "implementer", "repairer")
    if request["build_profile"] == REVIEWED_POLICY:
        role_names += ("reviewer",)
    roles = {role: bundle["roles"][role] for role in role_names}
    generation = bundle.get("role_profiles") or bundle.get("generation") or {}
    _require(all(isinstance(generation.get(role), dict) and generation[role] for role in roles), "Every reused role requires its exact frozen generation profile")
    return {"request": {key: copy.deepcopy(request.get(key)) for key in ("prompt", "sources", "control_files", "build_profile")},
            "image_id": image, "project_environment": validate_project_environment(bundle),
            "build_contract": copy.deepcopy(bundle["build_contract"]), "build": copy.deepcopy(bundle["build"]),
            "roles": roles, "generation": {role: copy.deepcopy(generation[role]) for role in roles},
            "backend": copy.deepcopy(bundle.get("backend")), "web_research": False}


def _validate_checkpoint(value, bundle):
    from .project_build import (ProjectArtifact, ProjectProject, ProjectTestSuite, group_artifact_type,
                                selected_source, validate_group_plan)
    from .project_environment import validate_project_environment, validate_suite_environment
    _require(set(value) == {"plan", "suite", "group_plan", "groups"}, "Unexpected checkpoint fields")
    plan = BuildPlan.model_validate(value["plan"])
    suite, grouping, groups = None, None, []
    if value["suite"] is not None:
        suite = ProjectTestSuite.model_validate(value["suite"])
        validate_paths(suite, ProjectArtifact, ProjectProject)
        corrected, fixes = coverage.correct_test_references(suite, qualify_classes=True)
        _require(not fixes and corrected.model_dump() == suite.model_dump(), "Frozen test references require changes under current validation")
        coverage.validate_test_coverage(suite, plan.acceptance_criteria)
        validate_discovery_suite(suite)
        from .project_fixture_checks import diagnostics
        environment = validate_project_environment(bundle)
        _require(not fixture_diagnostics(suite) and not diagnostics(
            suite, python_version=(environment or {}).get("python_version")), "Frozen test fixtures fail current validation")
        validate_suite_environment(suite, environment)
    if value["group_plan"] is not None:
        _require(suite is not None, "A group checkpoint requires frozen independent tests")
        grouping = validate_group_plan(value["group_plan"], suite.implementation_files)
    _require(isinstance(value["groups"], list) and len(value["groups"]) <= 32, "Bounded contiguous groups required")
    files = []
    for index, raw in enumerate(value["groups"]):
        _require(grouping is not None and index < len(grouping.groups), "Reused group is outside its frozen partition")
        artifact = group_artifact_type(bundle).model_validate(raw)
        group = grouping.groups[index]
        _require({f.path for f in artifact.files} == set(group.files), "Reused group changed its exact frozen file set")
        if group.context_files:
            selected_source(files, group.context_files, bundle["build_contract"]["context_bytes"])
        interfaces = {g.id: g.interface for g in grouping.groups[:index] if g.id in group.depends_on}
        _require(len(encoded(interfaces).encode()) <= 8192, "Reused dependency interfaces exceed current bounded context")
        files.extend(artifact.files)
        ProjectArtifact(summary="Incremental offline project", files=files)
        groups.append(artifact)
    return ProjectCheckpoint(plan, suite, grouping, tuple(groups), {})


def _components(checkpoint):
    result = {"plan": checkpoint.plan.model_dump()}
    if checkpoint.suite is not None:
        result["suite"] = checkpoint.suite.model_dump()
    if checkpoint.group_plan is not None:
        result["group_plan"] = checkpoint.group_plan.model_dump()
    result.update({f"group_{index}": group.model_dump() for index, group in enumerate(checkpoint.groups, 1)})
    return result


def _compile_boundaries(bundle, group_plan):
    owners = {path: index for index, group in enumerate(group_plan["groups"], 1) for path in group["files"]}
    checkpoints = (bundle.get("project_environment") or {}).get("compile_checkpoints", [])
    for slot, checkpoint in enumerate(checkpoints, 1):
        prefix = checkpoint["source_root"] + "/"
        paths = {path for path in owners if path.startswith(prefix)}
        _require(paths, "Compile checkpoint has no files in the frozen group plan")
        yield slot, max(owners[path] for path in paths), checkpoint, paths


def _compile_request_bytes(bundle, index, checkpoint, paths, files):
    from .project_build import ProjectArtifact
    name = checkpoint["name"]
    component = ProjectArtifact(summary=f"Compile checkpoint {name}",
                                files=[file for file in files if file["path"] in paths])
    _require({file.path for file in component.files} == paths, "Compile checkpoint requires the complete component")
    return encoded({"image_id": bundle["sandbox"]["image_id"], "component_digest": digest(component.model_dump()),
                    "checkpoint": checkpoint, "group_index": index,
                    "command": f"workbench-compile-checkpoint {name}"}).encode("utf-8")


def _validate_terminal_generation(status, receipts, index):
    last = receipts[-1] if receipts else {}
    stage = f"implement_group_{index}"
    _require(status == "failed" and last.get("projected_stage") in {stage, stage + "_scope_retry"}
             and last.get("status") == "terminal" and last.get("native_state") == "stop",
             "Uncompiled reuse requires a failed run ending at the selected normally completed group")
    _require(all(row["finished"] <= last["started"]
                 and not row.get("projected_stage", row["stage"]).startswith("build_compile_repair_")
                 for row in receipts[:-1]), "Uncompiled reuse cannot follow compile repair or later model execution")


def _retain_pre_compile_checkpoint(bundle, group_plan, files, index, status, receipts, read_optional):
    _validate_terminal_generation(status, receipts, index)
    retained = None
    for slot, boundary, checkpoint, paths in _compile_boundaries(bundle, group_plan):
        if boundary < index:
            continue
        for attempt in range(bundle["build_contract"]["max_compile_repairs"] + 1):
            stage = f"build_compile_checkpoint_{slot}_{attempt}"
            request = read_optional(stage, "request")
            _require(read_optional(stage, "raw") is None, "Uncompiled reuse requires no checkpoint execution receipt")
            if request is not None:
                _require(retained is None and boundary == index and attempt == 0,
                         "Uncompiled reuse requires a single first-attempt request at the selected group boundary")
                _require(request == _compile_request_bytes(bundle, index, checkpoint, paths, files),
                         "Retained compile request differs from the approved component and source bytes")
                retained = {"stage": stage, "request_sha256": _sha(request)}
    _require(retained is not None, "Missing first-attempt request for the selected compile checkpoint")
    _require(read_optional("build_verify_0", "request") is None and read_optional("build_verify_0", "raw") is None,
             "Uncompiled reuse cannot follow final verification")
    return retained


def _validate_pre_compile_checkpoint(source, receipts, selection, checkpoint, bundle):
    evidence = source["pre_compile_checkpoint"]
    _require(isinstance(evidence, dict) and set(evidence) == {"stage", "request_sha256"}
             and selection.through == "implementation", "Invalid pre-compile reuse provenance")
    index = selection.completed_groups
    _validate_terminal_generation(source["status"], receipts, index)
    files = [file.model_dump() for group in checkpoint.groups for file in group.files]
    for slot, boundary, configured, paths in _compile_boundaries(bundle, checkpoint.group_plan.model_dump()):
        if boundary == index and evidence["stage"] == f"build_compile_checkpoint_{slot}_0":
            expected = _sha(_compile_request_bytes(bundle, index, configured, paths, files))
            _require(evidence["request_sha256"] == expected, "Pre-compile request provenance changed")
            return
    raise ValueError("Pre-compile reuse provenance does not name the selected group boundary")


def load_project_reuse(bundle, *, current_sdk_revision=None):
    """Revalidate the already approval-bound import using the current code only.

    No model corrections, inference-cache entries, journal writes or executions
    occur. The host still verifies the entire destination approval before use.
    """
    manifest = bundle.get("project_reuse")
    if manifest is None:
        return None
    _require(isinstance(manifest, dict) and set(manifest) == {"policy", "source", "selection", "compatibility_digest",
        "validated_with_sdk_revision", "checkpoint", "artifact_provenance"}, "Invalid checkpoint reuse manifest")
    _require(manifest["policy"] == POLICY and len(encoded(manifest).encode()) <= 1_048_576, "Unsupported or overlarge reuse manifest")
    _revision(manifest["validated_with_sdk_revision"])
    if current_sdk_revision is not None:
        _require(_revision(current_sdk_revision) == manifest["validated_with_sdk_revision"], "Reuse import was frozen by a different current SDK; prepare again")
    source = manifest["source"]
    _require(isinstance(source, dict) and source.get("status") in KNOWN_TERMINAL, "Unknown or nonterminal checkpoint source")
    uuid.UUID(source["run_id"])
    _revision(source["sdk_revision"])
    for name in ("approval_digest", "bundle_digest", "projection_digest", "calls_digest", "result_digest"):
        _require(isinstance(source.get(name), str) and re.fullmatch(r"[0-9a-f]{64}", source[name]), "Missing original source provenance")
    receipts = source.get("native_receipts")
    _require(isinstance(receipts, list) and len(receipts) <= 256, "Original native receipt inventory is required")
    stages = set()
    for receipt in receipts:
        _require(receipt["stage"] not in stages and receipt["status"] in {"terminal", "not_dispatched"}
                 and receipt["native_state"] in {"stop", "length", "not_dispatched"}
                 and re.fullmatch(r"[0-9a-f]{64}", receipt["raw_sha256"]), "Unresolved original native receipt")
        _require(all(type(receipt.get(k)) in (int, float) and math.isfinite(receipt[k])
                     for k in ("started", "finished")) and receipt["finished"] >= receipt["started"], "Original receipt has no known finish")
        stages.add(receipt["stage"])
    _require(manifest["compatibility_digest"] == digest(_compatibility(bundle)), "Reused brief, roles, policies or environment changed")
    selection = ReuseSelection.model_validate(manifest["selection"])
    checkpoint = _validate_checkpoint(manifest["checkpoint"], bundle)
    expected = {"plan"}
    if selection.through != "plan": expected.add("suite")
    if selection.through in {"group_plan", "implementation"}: expected.add("group_plan")
    expected.update(f"group_{i}" for i in range(1, selection.completed_groups + 1))
    values = _components(checkpoint)
    _require(set(values) == expected, "Checkpoint does not match the explicitly approved prefix")
    provenance = manifest["artifact_provenance"]
    _require(isinstance(provenance, list) and len(provenance) == len(values), "Every reused component requires original artifact provenance")
    seen = set()
    for row in provenance:
        _require(isinstance(row, dict) and set(row) == {"component", "source_run_id", "source_approval_digest", "stage", "suffix", "sha256", "value_digest"}, "Invalid artifact provenance")
        name = row["component"]
        _require(name in values and name not in seen and row["value_digest"] == digest(values[name]), "Reused artifact payload or provenance changed")
        uuid.UUID(row["source_run_id"])
        _require(row["suffix"] == "raw" and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", row["stage"]), "Invalid retained artifact identity")
        for key in ("sha256", "source_approval_digest"):
            _require(isinstance(row[key], str) and re.fullmatch(r"[0-9a-f]{64}", row[key]), "Invalid original artifact hash")
        seen.add(name)
    if "pre_compile_checkpoint" in source:
        _validate_pre_compile_checkpoint(source, receipts, selection, checkpoint, bundle)
    return ProjectCheckpoint(checkpoint.plan, checkpoint.suite, checkpoint.group_plan, checkpoint.groups, copy.deepcopy(manifest))


def prepare_project_reuse(source_row, artifact_bytes, destination_bundle, *, source_sdk_revision,
                          current_sdk_revision, through, completed_groups=0,
                          project_bundle=None, project_call=None):
    """Resolve a trusted source into immutable inputs for a NEW approval.

    artifact_bytes(original_stage, suffix) returns exact saved bytes, not paths.
    Private host projections may rename stages/roles, but cannot change dispatch
    identity/status/times. Original bundle/approval are checked before projection.
    """
    source = copy.deepcopy(source_row)
    uuid.UUID(source["id"])
    _require(source.get("approved") in (True, 1) and source["status"] in KNOWN_TERMINAL,
             "Reuse requires an approved known-terminal source; unknown runs cannot be retried")
    original_digest = digest(source["bundle"])
    _require(source["digest"] == original_digest and isinstance(source.get("approval"), dict)
             and source["approval"].get("digest") == original_digest,
             "Original source approval does not match its immutable bundle")
    _revision(source_sdk_revision); _revision(current_sdk_revision)
    selection = ReuseSelection(through=through, completed_groups=completed_groups)
    projected = (project_bundle or (lambda value: value))(copy.deepcopy(source["bundle"]))
    _require(digest(_compatibility(projected)) == digest(_compatibility(destination_bundle)), "Source and fresh destination have different briefs, roles, policies or environments")
    prior = load_project_reuse(projected)
    calls = source.get("calls")
    _require(isinstance(calls, list) and len(calls) <= 256 and (calls or prior is not None), "Bounded original native-call journal or approved inherited checkpoint required")
    mapped, original_stages, raw_values, hashes, receipts, loaded, total = {}, {}, {}, {}, [], {}, 0

    def original_bytes(stage, suffix, maximum=33_000_000):
        nonlocal total
        key = stage, suffix
        if key not in loaded:
            blob = artifact_bytes(stage, suffix)
            _require(type(blob) is bytes and len(blob) <= maximum, "Exact bounded original artifact bytes required")
            total += len(blob)
            _require(total <= 134_217_728, "Source receipt inventory exceeds bound")
            loaded[key] = blob
        _require(len(loaded[key]) <= maximum, "Frozen checkpoint exceeds its bound")
        return loaded[key]

    def optional_bytes(stage, suffix):
        try:
            return original_bytes(stage, suffix, 1_048_576)
        except FileNotFoundError:
            return None

    for call in calls:
        public = (project_call or (lambda value: value))(copy.deepcopy(call))
        for key in ("request_id", "status", "started", "finished"):
            _require(public.get(key) == call.get(key), "Call projection changed native identity or state")
        _require(public["stage"] not in mapped, "Projected source stages collide")
        mapped[public["stage"]] = public
        original_stages[public["stage"]] = call["stage"]
        _require(call["status"] in {"terminal", "not_dispatched"} and all(type(call.get(k)) in (int, float)
                 and math.isfinite(call[k]) for k in ("started", "finished")) and call["finished"] >= call["started"],
                 "Every source call must have independently known terminal state")
        _require(call.get("backend_diagnostics", {}).get("native_completion") not in {"unknown", "not_started_unknown"}, "Source retains native uncertainty")
        value = original_bytes(call["stage"], "raw")
        raw = json.loads(value.decode("utf-8"))
        if call["status"] == "not_dispatched":
            _require(raw.get("dispatch_status") == "not_dispatched", "Non-dispatch receipt does not match its source call")
        else:
            choices = raw.get("choices")
            _require(isinstance(choices, list) and len(choices) == 1 and choices[0].get("finish_reason") in {"stop", "length"}, "Native completion is not proven by the saved raw response")
            native = raw.get("native") or {}
            _require(native.get("terminal_state") in {None, "stop", "length"}
                     and all(native.get(key) is not False for key in ("cleanup_confirmed", "native_done_ack", "handler_finished", "admission_released"))
                     and raw.get("native_completion") != "unknown", "Source native cleanup is uncertain")
        raw_values[public["stage"]], hashes[public["stage"]] = raw, _sha(value)
        receipts.append({"stage": call["stage"], "projected_stage": public["stage"],
            "request_id": call["request_id"], "status": call["status"],
            "started": call["started"], "finished": call["finished"], "raw_sha256": _sha(value),
            "native_state": "not_dispatched" if call["status"] == "not_dispatched" else raw["choices"][0]["finish_reason"]})
    provenance = []

    def retained(component, stage, value, *, model=False):
        if model:
            _require(stage in mapped and mapped[stage]["status"] == "terminal"
                     and raw_values[stage]["choices"][0]["finish_reason"] == "stop", "Reused structured stage did not finish normally")
            artifact_sha = hashes[stage]
        else:
            blob = original_bytes(stage, "raw", 1_048_576)
            artifact_sha = _sha(blob)
        provenance.append({"component": component, "source_run_id": source["id"], "source_approval_digest": original_digest,
            "stage": original_stages[stage] if model else stage, "suffix": "raw", "sha256": artifact_sha, "value_digest": digest(value)})
        return value

    def inherited(component):
        _require(prior is not None and component in _components(prior), "Source has no completed checkpoint for the requested stage")
        row = next(item for item in prior.provenance["artifact_provenance"] if item["component"] == component)
        provenance.append(copy.deepcopy(row))
        return copy.deepcopy(_components(prior)[component])

    def model_value(component, stage):
        if stage not in mapped:
            return inherited(component)
        call = mapped[stage]
        expected_role = "implementer" if stage.startswith("implement_group_") else "planner"
        _require(call["role"] == expected_role and call["model"] == projected["roles"][expected_role], "Reused stage role/model differs from approved source")
        _require(call["status"] == "terminal" and raw_values[stage]["choices"][0]["finish_reason"] == "stop",
                 "Reused structured stage did not finish normally")
        content = raw_values[stage]["choices"][0].get("message", {}).get("content")
        _require(isinstance(content, str), "Reused structured response has no final content")
        value = _strict_json(content)
        from .project_build import GroupPlan, group_artifact_type
        model_type = (BuildPlan if component == "plan" else GroupPlan if component == "group_plan"
                      else group_artifact_type(projected))
        return retained(component, stage, model_type.model_validate(value).model_dump(), model=True)

    def implementation_value(index, group):
        from .project_build import REVIEWED_POLICY, group_artifact_type, group_scope_feedback
        stage = f"implement_group_{index}"
        retry_stage = stage + "_scope_retry"
        if retry_stage not in mapped:
            return model_value(f"group_{index}", stage)
        first, retry = mapped.get(stage, {}), mapped[retry_stage]
        _require(projected["request"]["build_profile"] == REVIEWED_POLICY
                 and first.get("status") == "terminal" and first.get("role") == "implementer"
                 and first.get("model") == projected["roles"]["implementer"]
                 and first["finished"] <= retry["started"], "Scope retry requires its prior approved native call")
        feedback = group_scope_feedback(raw_values[stage], group_artifact_type(projected), group["files"])
        _require(feedback is not None, "Scope retry requires a normally completed strict subset of the frozen files")
        first_request = json.loads(original_bytes(original_stages[stage], "request", 1_048_576).decode("utf-8"))
        retry_request = json.loads(original_bytes(original_stages[retry_stage], "request", 1_048_576).decode("utf-8"))
        first_inputs = _strict_json(first_request["messages"][1]["content"])
        retry_inputs = _strict_json(retry_request["messages"][1]["content"])
        _require(first_inputs["group"] == group and "implementation_scope_feedback" not in first_inputs
                 and retry_inputs == {**first_inputs, "implementation_scope_feedback": feedback}
                 and retry_request["schema"] == first_request["schema"],
                 "Scope retry changed its frozen implementation inputs")
        return model_value(f"group_{index}", retry_stage)

    checkpoint = {"plan": model_value("plan", "build_plan"), "suite": None, "group_plan": None, "groups": []}
    if through != "plan":
        frozen = json.loads(original_bytes("build_frozen", "raw", 1_048_576).decode("utf-8"))
        _require(frozen.get("plan_digest") == digest(checkpoint["plan"]) and frozen.get("suite_digest") == digest(frozen.get("suite")), "Frozen suite does not match its approved plan and digest")
        if "build_tests" not in mapped:
            _require(prior is not None and prior.suite is not None and prior.suite.model_dump() == frozen["suite"], "Inherited independent tests changed")
            _require(prior.provenance["validated_with_sdk_revision"] == current_sdk_revision,
                     "Inherited tests lack original fixture transition bytes for current SDK validation; author fresh tests or reuse only the plan")
        else:
            # Reconstruct final independent source from the actual author/repair
            # receipts. A modified checkpoint with a recomputed suite digest must
            # not silently become a different frozen test suite.
            from .project_build import ProjectTestSuite, REVIEWED_POLICY
            from .build_workflow import normalized_manifest
            authored = None
            for stage, call in mapped.items():
                if stage != "build_tests" and not stage.startswith(("build_tests_syntax_", "build_tests_fixture_", "build_tests_coverage_")):
                    continue
                _require(call["role"] == "test_author" and call["model"] == projected["roles"]["test_author"]
                         and call["status"] == "terminal" and raw_values[stage]["choices"][0]["finish_reason"] == "stop",
                         "Frozen suite source was not completed by the approved independent author")
                value = _strict_json(raw_values[stage]["choices"][0]["message"]["content"])
                if stage == "build_tests":
                    value, _ = normalized_manifest(value, value.get("implementation_files"))
                    if projected["request"]["build_profile"] == REVIEWED_POLICY:
                        value, _ = normalize_authored_test_paths(value)
                    authored = ProjectTestSuite.model_validate(value)
                else:
                    _require(authored is not None, "Independent test repair precedes its author")
                    merged = authored.model_dump()
                    if stage.startswith("build_tests_coverage_"):
                        extra, _ = normalized_manifest(value, authored.implementation_files, additions=True)
                        merged["files"].extend(extra["files"])
                        merged["coverage"].extend(extra["coverage"])
                    else:
                        replacements = {item["path"]: item for item in value["files"]}
                        _require(len(replacements) == len(value["files"]) and set(replacements) <= {f.path for f in authored.files},
                                 "Test repair changed the frozen file interface")
                        if stage.startswith("build_tests_fixture_"):
                            from .build_models import BuildFile
                            from .project_fixture_checks import diagnostics, validate_repair
                            problems = fixture_diagnostics(authored) + diagnostics(authored)
                            _require(set(replacements) == {problem["path"] for problem in problems},
                                     "Fixture reuse repair did not replace exactly the currently diagnosed paths")
                            validate_repair([f for f in authored.files if f.path in replacements],
                                [BuildFile.model_validate(item) for item in value["files"]], problems)
                        merged["files"] = [replacements.get(item["path"], item) for item in merged["files"]]
                    authored = ProjectTestSuite.model_validate(merged)
            if authored is not None:
                authored, _ = coverage.correct_test_references(authored, qualify_classes=True)
            _require(authored is not None and authored.model_dump() == frozen["suite"], "Frozen suite differs from its independent native author/repair receipts")
        checkpoint["suite"] = retained("suite", "build_frozen", frozen["suite"])
    if through in {"group_plan", "implementation"}:
        frozen = json.loads(original_bytes("build_project_frozen", "raw", 1_048_576).decode("utf-8"))
        _require(frozen["test_suite_digest"] == digest(checkpoint["suite"]) and frozen["digest"] == digest(frozen["group_plan"]), "Group plan is not bound to frozen tests")
        attempt = frozen["format_repairs"]
        _require(type(attempt) is int and 0 <= attempt <= projected["build_contract"]["group_plan_format_repairs"], "Invalid original grouping repair provenance")
        stage = "build_project_plan" if attempt == 0 else f"build_project_plan_repair_{attempt}"
        grouped = model_value("group_plan", stage)
        _require(grouped == frozen["group_plan"], "Frozen grouping differs from original normally completed response")
        checkpoint["group_plan"] = grouped
    files = []
    pre_compile_checkpoint = None
    compile_checkpoints = (projected.get("project_environment") or {}).get("compile_checkpoints")
    if completed_groups:
        _require(completed_groups <= len(checkpoint["group_plan"]["groups"]),
                 "Reused group is outside its frozen partition")
    for index in range(1, completed_groups + 1):
        group = implementation_value(index, checkpoint["group_plan"]["groups"][index - 1])
        checkpoint["groups"].append(group)
        files.extend(group["files"])
        try:
            accumulated = json.loads(original_bytes(f"project_after_{index}", "raw", 1_048_576).decode("utf-8"))
        except FileNotFoundError:
            if index != completed_groups or not compile_checkpoints:
                raise
            pre_compile_checkpoint = _retain_pre_compile_checkpoint(
                projected, checkpoint["group_plan"], files, index, source["status"], receipts, optional_bytes)
            continue
        if compile_checkpoints:
            _require(accumulated.get("files") == files,
                     "Implementation prefix changed after generation; reuse the plan and regenerate the corrected groups")
        else:
            _require(accumulated.get("files") == files, "Implementation prefix is not contiguous and unchanged")
    manifest = {"policy": POLICY, "source": {"run_id": source["id"], "approval_digest": original_digest,
        "bundle_digest": original_digest, "projection_digest": digest(projected), "status": source["status"],
        "sdk_revision": source_sdk_revision, "calls_digest": digest(calls), "result_digest": digest(source.get("result")),
        "native_receipts": receipts},
        "selection": selection.model_dump(), "compatibility_digest": digest(_compatibility(destination_bundle)),
        "validated_with_sdk_revision": current_sdk_revision, "checkpoint": checkpoint, "artifact_provenance": provenance}
    if pre_compile_checkpoint is not None:
        manifest["source"]["pre_compile_checkpoint"] = pre_compile_checkpoint
    load_project_reuse({**destination_bundle, "project_reuse": manifest}, current_sdk_revision=current_sdk_revision)
    return copy.deepcopy(manifest)
