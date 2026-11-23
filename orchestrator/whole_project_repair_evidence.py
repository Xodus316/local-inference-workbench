"""Current-source facts and bounded repair memory shared by every source repair."""
import difflib
import json

from .store import digest
from .whole_project_mongo import _mapping_findings


POLICY = "source-facts-and-repair-history-v1"


def failure_observation(feedback):
    """Compare complete case identities, never volatile IDs or excerpted assertions."""
    result = []
    for command in feedback.get("failed_execution", {}).get("commands", []):
        summary = command.get("sandbox_stage_results") or {}
        if summary.get("cleanup_confirmed") is not True:
            return None
        cases = []
        for failure in summary.get("failures", []):
            rows = failure.get("test_failures")
            if rows is not None:
                if (failure.get("test_failures_complete") is not True
                        or failure.get("omitted_test_failures") != 0
                        or failure.get("test_failure_count") != len(rows)
                        or not all(isinstance(row.get("test"), str) for row in rows)):
                    return None
                cases.append({"check": failure["stage"], "tests_run": failure.get("tests_run"),
                              "failed_tests": sorted(row["test"] for row in rows)})
        result.append({"checks": {name: value.get("status") for name, value in sorted(summary.get("checks", {}).items())},
                       "cases": cases})
    return result if result else None


def source_facts(artifact):
    """Static risks are hypotheses; comments and an operand type do not prove stored BSON types."""
    files = {file.path: file.content for file in artifact.files}
    facts = []
    for service, entity_path, entity, field in sorted(_mapping_findings(artifact.files))[:8]:
        anchors = []
        for path in (entity_path, service):
            for number, line in enumerate(files[path].splitlines(), 1):
                if field in line and not line.lstrip().startswith(("//", "*")):
                    anchors.append({"path": path, "line": number, "source": line.strip()[:320]})
                    if len(anchors) == 8:
                        break
            if len(anchors) == 8:
                break
        facts.append({"kind": "mongo-decimal-mapping-risk", "source_paths": [entity_path, service],
                      "observed": f"{entity}.{field} is BigDecimal without an explicit BSON target type and is updated with $inc.",
                      "hypothesis": "Spring Data may persist this field as a BSON string. An operand-only Decimal128 change does not establish numeric storage or fix existing string values. Inspect creation, mapping and update together; preserve atomicity and exact decimals.",
                      "stored_bson_type_observed": False, "source_anchors": anchors})
    return facts


def source_digest(artifact):
    return digest({file.path: digest(file.content) for file in artifact.files})


def repair_feedback(artifact, feedback, history):
    current = digest(artifact.model_dump())
    current_source = source_digest(artifact)
    observation = failure_observation(feedback)
    attempts, remaining = [], 16384
    # Include a contiguous chain, including intervening compiler corrections.
    for attempt in reversed(history):
        if (attempt.get('after_source_digest') != current_source if 'after_source_digest' in attempt
                else attempt['after_digest'] != current):
            continue
        changes, diff_remaining = [], 2048
        for change in attempt["changed_files"][:16]:
            shown = change["diff"].encode("utf8")[:diff_remaining].decode("utf8", "ignore")
            diff_remaining -= len(shown.encode("utf8"))
            changes.append({**change, "diff": shown, "diff_truncated": change["diff_truncated"] or shown != change["diff"]})
        projected = {key: attempt[key] for key in ("stage", "before_digest", "after_digest")}
        projected.update(changed_files=changes, omitted_changed_files=max(0, len(attempt["changed_files"]) - 16),
            failure_before_digest=digest(attempt["failure_before"]),
            same_complete_failure_observation=observation is not None and observation == attempt["failure_before"])
        size = len(json.dumps(projected, ensure_ascii=False).encode("utf8"))
        if size > remaining:
            break
        attempts.append(projected)
        remaining -= size
        current = attempt["before_digest"]
        current_source = attempt.get('before_source_digest')
        if len(attempts) == 4:
            break
    return {**feedback, "repair_evidence": {"policy": POLICY,
        "implementation_digest": digest(artifact.model_dump()), "source_facts": source_facts(artifact),
        "prior_attempts": list(reversed(attempts)),
        "guidance": "Treat earlier diagnoses as hypotheses. Use source anchors rather than comments to establish behavior. A repeated failing case set does not prove the cause is unchanged; identify what the previous edit did and did not address before proposing a different correction."}}


def repair_record(stage, before, after, feedback):
    old = {file.path: file.content for file in before.files}
    changes, remaining = [], 8192
    for file in after.files:
        if old.get(file.path) == file.content:
            continue
        patch = "".join(difflib.unified_diff(old.get(file.path, "").splitlines(True),
                         file.content.splitlines(True), fromfile=file.path, tofile=file.path, n=2))
        shown = patch.encode("utf8")[:remaining].decode("utf8", "ignore")
        remaining -= len(shown.encode("utf8"))
        changes.append({"path": file.path, "before_sha256": digest(old.get(file.path, "")),
                        "after_sha256": digest(file.content), "diff": shown,
                        "diff_truncated": shown != patch})
    return {"stage": stage, "before_digest": digest(before.model_dump()),
            "after_digest": digest(after.model_dump()), "changed_files": changes,
            'before_source_digest': source_digest(before), 'after_source_digest': source_digest(after),
            "failure_before": failure_observation(feedback)}
