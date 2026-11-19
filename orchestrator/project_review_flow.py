"""Source-grounded native review with one bounded, auditable format correction."""

from copy import deepcopy

from pydantic import ValidationError

from .build_workflow import build_model_payload
from .project_review import REVIEW_BRIEF, ReviewCitationError, numbered_review_source, review_chunks, style_findings
from . import project_review_anchors as anchors
from .store import digest, encoded


POLICY = "bounded-native-source-anchor-review-v1"
FEEDBACK_RESERVE_BYTES = 32768
_OLD_CITATION = (
    "For every finding, use the displayed line number and copy a verbatim excerpt from "
    "that single line after its ' | ' prefix, then explain the impact and describe a concrete fix. "
    "Before submitting, check the excerpt on the cited line. "
)
if REVIEW_BRIEF.count(_OLD_CITATION) != 1:
    raise RuntimeError("Review citation instruction changed")
INITIAL_BRIEF = REVIEW_BRIEF.replace(_OLD_CITATION, (
    "For every finding, select one exact anchor_id from the response schema enum. "
    "source_anchors lists each supplied file's anchor_prefix and eligible displayed line numbers. "
    "An ordinary address is <anchor_prefix>:L<line>; only lines listed in segmented_lines also "
    "require :S<segment>, referring to consecutive 500-character windows on that same full source line. "
    "The harness expands that address to its exact path, line and literal excerpt. Do not copy or "
    "rewrite code, or return path/line/evidence fields. Explain impact and a concrete fix in your own prose. "
)) + (
    " Hard character limits: title 120, description 900, fix 600. Aim below them: title 80, description 600, "
    "fix 350. Twelve findings is a maximum, not a target. Each title must describe the same defect "
    "as its body. Do not classify explicitly permitted contract behavior or hypothetical future code "
    "as a present defect. The entire actual numbered source remains available, including long lines."
)
CORRECTION_BRIEF = INITIAL_BRIEF + (
    " Your previous normally completed native response failed validation. Its exact value and errors "
    "are supplied in review_validation_feedback. Return a complete corrected native anchor review "
    "against this same full source and registry. Fix schema, length or anchor defects without merely "
    "deleting or downgrading substantive grounded findings. No source or test edits are authorized."
)


def validation_errors(error):
    if isinstance(error, ValidationError):
        return {"kind": "schema", "message": str(error),
                "errors": error.errors(include_url=False, include_context=False, include_input=False)}
    if isinstance(error, (anchors.SourceAnchorError, ReviewCitationError)):
        return {"kind": "source_anchor" if isinstance(error, anchors.SourceAnchorError) else "citation",
                "message": str(error), "finding_index": error.finding_index}
    raise TypeError("Only explicit native review validation failures permit correction")


def review_project(flow, number, artifact, project, plan, baseline, *, tests_passed=False, previous_artifact=None):
    """Use the ordinary public dispatch/journal for both supported reviewer paths.

    The provider must settle and parse a normal response before returning a
    value. Transport errors, malformed JSON, cancellation and unknown completion
    propagate outside the validation catch and cannot trigger correction.
    """
    # Local import avoids a cycle when ProjectBuildRun delegates to this module.
    from .project_build import validate_project_prompt

    stage = f"build_code_review_{number}"
    flow.stage = stage
    required = ((flow.environment or {}).get("verification") or {}).get("required_checks", [])
    checks = baseline["sandbox_stage_results"][0]["checks"] if required else {}
    image_checks = {}
    for name in dict.fromkeys([*required, "browser"]):
        value = checks.get(name)
        status = value.get("status") if isinstance(value, dict) else None
        if status in {"passed", "failed", "not_run", "unavailable"}:
            image_checks[name] = status
    verification = {"status": "passed" if tests_passed else "inconclusive", "image_checks": image_checks}
    settings = (flow.bundle.get("role_profiles") or flow.bundle["generation"])["reviewer"]
    output_reserve = flow.contract["review_output_reserve"]

    def inputs(files, registry):
        return {
            "contract": plan.contract, "implementation": numbered_review_source(files),
            "verification": verification, "review_output_contract": anchors.source_anchor_contract_view(registry),
            "source_anchors": anchors.source_anchor_inputs(registry),
        }

    def payload(files, instruction=INITIAL_BRIEF, feedback=None):
        registry = anchors.source_anchor_registry(files)
        model = anchors.source_anchor_review_type(registry)
        data = inputs(files, registry)
        if feedback is not None:
            data["review_validation_feedback"] = feedback
        return build_model_payload(model, instruction, flow.model_inputs(data))

    def admitted(body, feedback_reserve=0):
        try:
            units = validate_project_prompt(body, flow.bundle, "reviewer")
        except ValueError:
            return False
        return units + output_reserve + feedback_reserve <= settings["context"]

    chunks = review_chunks(artifact.files, lambda files: admitted(payload(files), FEEDBACK_RESERVE_BYTES))
    if len(chunks) > flow.contract["max_review_chunks"]:
        raise ValueError("Full source, anchor schema and correction reserve exceed the approved chunk budget")
    receipts, findings, failures, attempts = [], [], [], []
    for index, files in enumerate(chunks, 1):
        initial = f"{stage}_chunk_{index}"
        paths = [file.path for file in files]
        registry = anchors.source_anchor_registry(files)
        model = anchors.source_anchor_review_type(registry)
        visible = flow._review_projection(files, project, plan) if flow.opencode_reviewer is not None else None
        feedback, grounded = None, []
        for correction in range(1 + int(tests_passed)):
            call_stage = initial + "_citation_retry" if correction else initial
            instruction = CORRECTION_BRIEF if correction else INITIAL_BRIEF
            data = inputs(files, registry)
            if feedback is not None:
                data["review_validation_feedback"] = feedback
            registry_stage = call_stage + "_source_anchors"
            flow.runtime.store.save_artifact(flow.state["run_id"], registry_stage, "raw", registry)
            chunk = {"stage": call_stage, "paths": paths}
            # Deliberately outside the validation catch: no hidden transport retry.
            if visible is None:
                value = flow.ask(call_stage, "reviewer", model, instruction, **data)
            else:
                body = build_model_payload(model, instruction, flow.model_inputs(data))
                if not admitted(body):
                    raise ValueError("Complete native review exceeds the approved context before dispatch")
                value = flow._worker_review(call_stage, settings, body, visible)
                chunk["worker_receipt_digest"] = flow.stages[call_stage]["worker_receipt_digest"]
            try:
                review, expansion = anchors.expand_source_anchor_review(value, registry, files)
            except (ValidationError, anchors.SourceAnchorError, ReviewCitationError) as error:
                exact = validation_errors(error)
                failure = {
                    **chunk, "validation_error": str(error), "validation_details": exact,
                    "original_review_digest": digest(value), "validation_policy": POLICY,
                    "source_anchor_registry_digest": digest(registry),
                }
                flow.stages.setdefault(call_stage, {})["validation_failure"] = deepcopy(failure)
                attempts.append(deepcopy(failure))
                valid_rows = anchors.grounded_review_findings(value, registry, files)
                if not correction and tests_passed:
                    feedback = {
                        "policy": POLICY, "original_stage": initial, "original_review": deepcopy(value),
                        "original_review_digest": digest(value), "validation_errors": exact,
                        "original_response_accepted": False, "original_review_format": "source-anchor-native-v1",
                        "source_anchor_registry_digest": digest(registry),
                    }
                    extra = (len(encoded(feedback).encode("utf-8"))
                             + len(CORRECTION_BRIEF.encode("utf-8")) - len(INITIAL_BRIEF.encode("utf-8")) + 128)
                    may_correct = extra <= FEEDBACK_RESERVE_BYTES and admitted(payload(files, CORRECTION_BRIEF, feedback))
                    flow.stages[call_stage]["correction_admission"] = {
                        "feedback_utf8_bytes": extra, "reserved_feedback_bytes": FEEDBACK_RESERVE_BYTES,
                        "admitted": may_correct, "original_review_digest": digest(value),
                    }
                    if may_correct:
                        grounded = valid_rows
                        continue
                    failure["correction_not_dispatched"] = "Exact native response and errors exceed reserved correction context"
                findings.extend(f.model_dump() for f in grounded)
                findings.extend(f.model_dump() for f in valid_rows if f not in grounded)
                failures.append(failure)
            else:
                expanded_stage = call_stage + "_expanded_review"
                expanded = {"canonical_review": review.model_dump(), "expansion": expansion}
                flow.runtime.store.save_artifact(flow.state["run_id"], expanded_stage, "raw", expanded)
                chunk["anchor_expansion"] = {
                    "registry_stage": registry_stage, "registry_digest": digest(registry),
                    "expanded_stage": expanded_stage, "expanded_digest": digest(expanded),
                    "native_review_digest": digest(value), "canonical_review_digest": digest(review.model_dump()),
                }
                if correction:
                    chunk.update(retry_of=initial, findings_retained_from_initial_review=len(grounded),
                                 correction_policy=POLICY, original_review_digest=feedback["original_review_digest"],
                                 correction_feedback_digest=digest(feedback))
                receipts.append(chunk)
                findings.extend(f.model_dump() for f in grounded)
                findings.extend(f.model_dump() for f in review.findings if f not in grounded)
            break
    findings.sort(key=lambda finding: (finding["severity"], finding["path"], finding["line"]))
    blockers = sum(finding["severity"] in flow.contract["review_blocking_severities"] for finding in findings)
    result = {
        "policy": flow.contract["code_review_policy"], "validation_policy": POLICY,
        "status": "incomplete" if failures else ("changes_required" if blockers else "passed"),
        "implementation_digest": digest(artifact.model_dump()), "project_digest": digest(project.model_dump()),
        "chunks": receipts, "findings": findings, "blocking_findings": blockers,
        "style_findings": [f.model_dump() for f in style_findings(artifact.files)], "validation_attempts": attempts,
    }
    if failures:
        result["validation_failures"] = failures
    flow.runtime.store.save_artifact(flow.state["run_id"], stage, "raw", result)
    flow.stages[stage] = {"status": result["status"], "digest": digest(result), "validation_policy": POLICY}
    flow.output["verification"]["code_reviews"].append(result)
    return result


class SourceAnchorReviewMixin:
    code_review = review_project
