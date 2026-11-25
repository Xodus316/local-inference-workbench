"""Literal reference failures and native proposals retained without success credit."""

from copy import deepcopy
import re
from uuid import UUID

from .repository import relative
from .store import digest, encoded


HISTORY_POLICY = "whole-reference-attempt-history-v1"
HISTORY_PROJECTION_POLICY = "reference-attempt-history-projection-v1"
MAX_HISTORY_BYTES = 2 * 1024 * 1024
MAX_HISTORY_RECORDS = 256
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_CONTROL = re.compile(r"build_control_(?:[0-9]|[1-5][0-9]|6[0-3])\Z")
_PATCH = re.compile(r"build_tests_defect_(?:[1-9]|1[0-6])_file_(?:[1-9]|1[0-5])\Z")


def empty_reference_history():
    return {"policy": HISTORY_POLICY, "records": [], "digest": digest([])}


def validate_reference_history(value):
    if (not isinstance(value, dict) or value.keys() != {"policy", "records", "digest"}
            or value["policy"] != HISTORY_POLICY or not isinstance(value["records"], list)
            or len(value["records"]) > MAX_HISTORY_RECORDS
            or len(encoded(value).encode("utf8")) > MAX_HISTORY_BYTES
            or value["digest"] != digest(value["records"])):
        raise ValueError("Reference attempt history must have bounded exact records and its matching digest")
    seen = set()
    for record in value["records"]:
        if not isinstance(record, dict) or record.keys() != {
                "source_run_id", "reference_stage", "reference_receipt_digest", "suite_digest", "failure", "attempt"}:
            raise ValueError("Unexpected reference attempt history record")
        if not isinstance(record["source_run_id"], str):
            raise ValueError("Reference history requires a source run UUID")
        UUID(record["source_run_id"])
        if (not isinstance(record["reference_stage"], str) or not _CONTROL.fullmatch(record["reference_stage"])
                or any(not isinstance(record[key], str) or not _HASH.fullmatch(record[key])
                       for key in ("reference_receipt_digest", "suite_digest"))):
            raise ValueError("Reference history requires exact reference receipt and suite bindings")
        failure = record["failure"]
        if (not isinstance(failure, dict) or failure.keys() != {"path", "names", "message", "failures"}
                or not isinstance(failure["path"], str) or relative(failure["path"]) != failure["path"]
                or not failure["path"].startswith("tests/") or not failure["path"].endswith(".py")
                or not isinstance(failure["message"], str) or not isinstance(failure["names"], list)
                or not 1 <= len(failure["names"]) <= 80
                or any(not isinstance(name, str) or not name or len(name) > 300 for name in failure["names"])
                or len(set(failure["names"])) != len(failure["names"])
                or not isinstance(failure["failures"], list)
                or len(failure["failures"]) != len(failure["names"])):
            raise ValueError("Reference history requires the exact named test-side failure evidence")
        expected = {name.rsplit(".", 1)[-1] for name in failure["names"]}
        if any(not isinstance(row, dict) or row.keys() != {"test", "excerpt"}
               or row["test"] not in expected or not isinstance(row["excerpt"], str) or not row["excerpt"].strip()
               for row in failure["failures"]):
            raise ValueError("Reference history cannot invent or omit a failure excerpt")
        if {row["test"] for row in failure["failures"]} != expected:
            raise ValueError("Reference history failure names differ from its observed cases")
        key = (record["source_run_id"], record["reference_stage"], failure["path"])
        if key in seen:
            raise ValueError("Repeated reference history observation")
        seen.add(key)
        attempt = record["attempt"]
        if attempt is None:
            continue
        if (not isinstance(attempt, dict) or attempt.keys() != {
                "stage", "patch_digest", "proposal", "outcome", "previous_suite_digest", "result_suite_digest",
                "rejection", "changes"} or not isinstance(attempt["stage"], str) or not _PATCH.fullmatch(attempt["stage"])
                or attempt["outcome"] not in {"applied", "rejected"} or not isinstance(attempt["proposal"], dict)
                or attempt["patch_digest"] != digest(attempt["proposal"])
                or any(not isinstance(attempt[key], str) or not _HASH.fullmatch(attempt[key])
                       for key in ("previous_suite_digest", "result_suite_digest"))
                or not isinstance(attempt["changes"], list) or len(attempt["changes"]) > 80
                or (attempt["outcome"] == "applied" and (attempt["rejection"] is not None
                    or attempt["previous_suite_digest"] == attempt["result_suite_digest"]))
                or (attempt["outcome"] == "rejected" and (not isinstance(attempt["rejection"], str)
                    or not attempt["rejection"] or attempt["previous_suite_digest"] != attempt["result_suite_digest"]))):
            raise ValueError("Reference history attempt must describe only actual composer acceptance or rejection")
        proposals = attempt["proposal"].get("changes", [])
        for change in attempt["changes"]:
            if (not isinstance(change, dict) or change.keys() != {
                    "target", "original_digest", "original_source", "replacement_digest", "replacement_source"}
                    or any(not isinstance(change[key], str) for key in change)
                    or change["original_digest"] != digest(change["original_source"])
                    or change["replacement_digest"] != digest(change["replacement_source"])
                    or not isinstance(proposals, list)
                    or not any(isinstance(row, dict) and row.get("target") == change["target"]
                               and row.get("replacement_source") == change["replacement_source"] for row in proposals)):
                raise ValueError("Reference history definition text differs from its native proposal or source digest")
        if attempt["outcome"] == "applied" and (
                not attempt["changes"] or len(attempt["changes"]) != len(proposals)
                or len({row["target"] for row in attempt["changes"]}) != len(attempt["changes"])):
            raise ValueError("Applied reference history must retain every changed definition")
    return deepcopy(value)


def project_reference_history(value, failure):
    history = validate_reference_history(value)
    relevant = [index for index, record in enumerate(history["records"])
                if record["failure"]["path"] == failure["path"]
                and set(record["failure"]["names"]) & set(failure["names"])]
    # Rechecking the same retained draft must not evict the failed operations:
    # retain attempted repairs plus the latest observation, not repeated checks.
    candidates = [index for index in relevant if history["records"][index]["attempt"] is not None
                  or index == relevant[-1]]
    selected = set(candidates[-3:])
    omitted = [record for index, record in enumerate(history["records"]) if index not in selected]
    return {"policy": HISTORY_PROJECTION_POLICY, "history_digest": history["digest"],
            "records": [record for index, record in enumerate(history["records"]) if index in selected],
            "selection": "last-three-attempts-or-latest-failure-for-file", "omitted_records": len(omitted),
            "omitted_records_digest": digest(omitted),
            "outcome_semantics": "applied means composer accepted the edit, never reference success; only explicit later failure observations show execution outcomes"}


def history_with_record(history, record):
    value = validate_reference_history(history)
    key = (record["source_run_id"], record["reference_stage"], record["failure"]["path"])
    indexes = [index for index, row in enumerate(value["records"])
               if (row["source_run_id"], row["reference_stage"], row["failure"]["path"]) == key]
    if indexes:
        previous = value["records"][indexes[0]]
        if ({k: v for k, v in previous.items() if k != "attempt"}
                != {k: v for k, v in record.items() if k != "attempt"}
                or previous["attempt"] is not None):
            raise ValueError("Reference observation cannot be rewritten after recording an attempt")
        value["records"][indexes[0]] = deepcopy(record)
    else:
        value["records"].append(deepcopy(record))
    value["digest"] = digest(value["records"])
    return validate_reference_history(value)


def reference_attempt_record(stage, inputs, proposal, previous_suite_digest, result_suite_digest, rejection):
    targets = {row["target"]: row for row in inputs["targets"]}
    changes = []
    for row in proposal.get("changes", []) if isinstance(proposal.get("changes"), list) else []:
        if not isinstance(row, dict) or row.get("target") not in targets or not isinstance(row.get("replacement_source"), str):
            continue
        target = targets[row["target"]]
        changes.append({"target": row["target"], "original_digest": target["original_digest"],
            "original_source": target["original_source"], "replacement_digest": digest(row["replacement_source"]),
            "replacement_source": row["replacement_source"]})
    return {"stage": stage, "patch_digest": digest(proposal), "proposal": deepcopy(proposal),
            "outcome": "rejected" if rejection is not None else "applied", "rejection": rejection,
            "previous_suite_digest": previous_suite_digest, "result_suite_digest": result_suite_digest,
            "changes": changes}
