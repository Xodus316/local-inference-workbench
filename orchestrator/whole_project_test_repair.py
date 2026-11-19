"""Native-authored definition patches for an unaccepted independent test draft."""

import ast
from collections import Counter
import copy
import re
from types import SimpleNamespace
from typing import Annotated

from pydantic import Field, create_model

from .build_coverage import parse_test_files, python_test_definitions
from .build_models import BuildTestFile
from .project_test_lint import defects
from .schemas import Record
from .store import digest
from .whole_project_reference_history import empty_reference_history, project_reference_history


METHOD_PATCH_POLICY = "whole-reference-method-patch-v1"
METHOD_PATCH_POLICY_V2 = "whole-reference-method-patch-v2"
METHOD_PATCH_POLICY_V3 = "whole-reference-method-patch-v3"
METHOD_PATCH_POLICY_V4 = "whole-reference-method-patch-v4"
INLINE_METHOD_PATCH_POLICY = "inline-reference-method-patch-v2"
INLINE_METHOD_PATCH_POLICY_V3 = "inline-reference-method-patch-v3"
INLINE_METHOD_PATCH_POLICY_V4 = "inline-reference-method-patch-v4"
DIAGNOSIS_POLICY = "whole-reference-diagnosis-v1"
DIAGNOSIS_POLICY_V2 = "whole-reference-diagnosis-v2"
INLINE_DIAGNOSIS_POLICY = "inline-reference-diagnosis-v1"
INLINE_DIAGNOSIS_POLICY_V2 = "inline-reference-diagnosis-v2"
DIAGNOSIS_TOOL_POLICIES = {INLINE_DIAGNOSIS_POLICY, INLINE_DIAGNOSIS_POLICY_V2}
DIAGNOSIS_OUTPUT_TOKENS = 8192
METHOD_PATCH_OUTPUT_TOKENS = 32768
METHOD_PATCH_GENERATION_LIMITS = {"max_completion_tokens": METHOD_PATCH_OUTPUT_TOKENS,
    "includes_reasoning": True, "separate_reasoning_limit": None,
    "max_steps": 1, "tools": False, "auto_compaction": False}
METHOD_PATCH_STAGE = re.compile(r"build_tests_defect_[1-3]_file_(?:[1-9]|1[0-5])\Z")
DIAGNOSIS_STAGE = re.compile(r"build_tests_diagnose_[1-3]_file_(?:[1-9]|1[0-5])\Z")
REFERENCE_GENERATION_LIMITS = {
    INLINE_METHOD_PATCH_POLICY: METHOD_PATCH_GENERATION_LIMITS,
    INLINE_METHOD_PATCH_POLICY_V3: METHOD_PATCH_GENERATION_LIMITS,
    INLINE_METHOD_PATCH_POLICY_V4: METHOD_PATCH_GENERATION_LIMITS,
    INLINE_DIAGNOSIS_POLICY: {**METHOD_PATCH_GENERATION_LIMITS,
                             "max_completion_tokens": DIAGNOSIS_OUTPUT_TOKENS},
    INLINE_DIAGNOSIS_POLICY_V2: {**METHOD_PATCH_GENERATION_LIMITS,
                                "max_completion_tokens": DIAGNOSIS_OUTPUT_TOKENS},
}
REFERENCE_REPAIR_GUIDANCE = (
    " This is pre-freeze correction of an unaccepted test draft that failed the trusted reference. "
    "The authorized test definitions may change here; the public brief's frozen-test restriction applies "
    "after test acceptance and during application source repair. The original public brief remains authoritative "
    "for expected behavior. Retained tests already supply the other acceptance coverage; do not re-author the "
    "suite, renumber criteria or change case identities. Correct only the named failures and authorized helpers. "
    "Preserve real discriminating assertions and every required action; never skip a case or make an action "
    "optional. Use independently registered credentials and unique fixture values. Retain successful tokens "
    "separately from expected-error response bodies. Require expected status and response shape before reading "
    "IDs or other fields. Protected success paths must authenticate and require success, not permit authorization "
    "errors. Treat IDs and tokens as opaque. For shared result lists, check guaranteed ordering across the actual "
    "list, locate this case's created rows by identity, then assert their values; do not assume absolute positions. "
    "Each isolated DOM mount must sign in through its UI; Python API authentication is not a browser session. "
    "Assert fixture rows, counts and values after real actions, not only headings. An ordinary read-back assertion "
    "does not prove a restart; describe only operations the case performs."
)


class ReferenceDefinitionChange(Record):
    target: str = Field(min_length=1, max_length=300)
    original_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: str = Field(min_length=1, max_length=60000,
        description="Complete changed definition, including its original leading indentation and decorators. No other definitions or statements.")


class ReferenceMethodPatch(Record):
    changes: list[ReferenceDefinitionChange] = Field(min_length=1, max_length=80,
        description="Only actually changed authorized definitions; never repeat an unchanged definition.")


class ReferenceDefinitionReplacement(Record):
    target: str = Field(min_length=1, max_length=300)
    expected_original_digest: str = Field(pattern=r"^[a-f0-9]{64}$",
        description="Copy this target's original_digest unchanged. This is a precondition on the OLD source, never a digest of replacement_source.")
    replacement_source: str = Field(min_length=1, max_length=60000,
        description="New corrected definition, not original_source. Include original indentation, signature and decorators, and change the faulty code.")


class ReferenceMethodPatchV2(ReferenceMethodPatch):
    changes: list[ReferenceDefinitionReplacement] = Field(min_length=1, max_length=80)


class ReferenceCaseDiagnosis(Record):
    test_name: str = Field(min_length=1, max_length=300)
    observed_failure: str = Field(min_length=1, max_length=1200)
    cause: str = Field(min_length=1, max_length=2000,
        description="Explain the causal connection between the observed failure and the current executable test/fixture operations.")
    evidence_ids: list[str] = Field(min_length=2, max_length=20,
        description="Harness evidence IDs: include this case's failure and the source files supporting the cause. No invented IDs.")
    required_operation_change: str = Field(min_length=1, max_length=2000,
        description="Describe which executable operation must change and how; comments, assertion messages or restating the failure are not a repair.")
    expected_behavior: str = Field(min_length=1, max_length=1200,
        description="The public-contract behavior and discriminating assertion the repaired test must still exercise.")


class ReferenceDiagnosis(Record):
    cases: list[ReferenceCaseDiagnosis] = Field(min_length=1, max_length=80)


class ReferenceCaseDiagnosisV2(ReferenceCaseDiagnosis):
    prior_attempt_assessment: str = Field(min_length=1, max_length=2000,
        description="Explain relevant prior attempted operations and their recorded outcomes. Do not propose repeating an observed failed operation without new supplied evidence; say when no relevant prior attempt exists.")


class ReferenceDiagnosisV2(ReferenceDiagnosis):
    cases: list[ReferenceCaseDiagnosisV2] = Field(min_length=1, max_length=80)


def reference_method_patch_type(targets, *, policy=METHOD_PATCH_POLICY):
    if policy in {METHOD_PATCH_POLICY_V2, METHOD_PATCH_POLICY_V3, METHOD_PATCH_POLICY_V4}:
        if not targets or len(targets) > 80:
            raise ValueError("Reference repair requires a bounded nonempty definition scope")
        change = create_model("BoundReferenceDefinitionReplacement", __base__=ReferenceDefinitionReplacement,
            target=(str, Field(min_length=1, max_length=300,
                               json_schema_extra={"enum": [item["target"] for item in targets]})),
            expected_original_digest=(str, Field(pattern=r"^[a-f0-9]{64}$",
                description="OLD source precondition: copy the target's original_digest, not a replacement digest.",
                json_schema_extra={"enum": sorted({item["original_digest"] for item in targets})})))
        return create_model("BoundReferenceMethodPatchV2", __base__=ReferenceMethodPatchV2,
                            changes=(list[change], Field(min_length=1, max_length=len(targets))))
    if policy != METHOD_PATCH_POLICY:
        raise ValueError("Unknown reference method patch policy")
    if not targets or len(targets) > 80:
        raise ValueError("Reference repair requires a bounded nonempty definition scope")
    change = create_model("BoundReferenceDefinitionChange", __base__=ReferenceDefinitionChange,
        target=(str, Field(min_length=1, max_length=300,
                           json_schema_extra={"enum": [item["target"] for item in targets]})),
        original_digest=(str, Field(pattern=r"^[a-f0-9]{64}$",
                                   json_schema_extra={"enum": sorted({item["original_digest"] for item in targets})})))
    return create_model("BoundReferenceMethodPatch", __base__=ReferenceMethodPatch,
                        changes=(list[change], Field(min_length=1, max_length=len(targets))))


def _definitions(file):
    tree = parse_test_files({file.path: file.content})[file.path]
    lines = file.content.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    found = {}
    for node in tree.body:
        nodes = [(None, node)] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else []
        if isinstance(node, ast.ClassDef):
            nodes = [(node.name, child) for child in node.body
                     if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for owner, definition in nodes:
            key = f"{owner}.{definition.name}" if owner else definition.name
            if key in found:
                raise ValueError("Reference repair cannot target duplicate definition names")
            start_line = min([definition.lineno, *(item.lineno for item in definition.decorator_list)]) - 1
            end_line = definition.end_lineno - 1
            start = offsets[start_line]
            end = offsets[end_line] + len(lines[end_line].rstrip("\r\n"))
            source = file.content[start:end]
            indent = lines[start_line][:len(lines[start_line]) - len(lines[start_line].lstrip(" \t"))]
            found[key] = {"node": definition, "owner": owner, "start": start, "end": end,
                          "indent": indent, "source": source, "original_digest": digest(source)}
    return tree, found


def _called_helpers(definition, definitions):
    owner = definition["owner"]
    for node in ast.walk(definition["node"]):
        if not isinstance(node, ast.Call):
            continue
        called = None
        if isinstance(node.func, ast.Name):
            called = node.func.id
        elif (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
              and node.func.value.id in {"self", "cls", owner} and owner):
            called = f"{owner}.{node.func.attr}"
        if called in definitions and not definitions[called]["node"].name.startswith("test"):
            yield called


def _scope(file, failure):
    tree, definitions = _definitions(file)
    names = failure.get("names")
    tests = python_test_definitions(tree)
    if (failure.get("path") != file.path or not isinstance(names, list) or not names
            or len(names) != len(set(names)) or any(name not in tests or name not in definitions for name in names)):
        raise ValueError("Reference repair must name exact existing failed tests in one file")
    allowed = {name: {name} for name in names}
    # A fixture is authorized only by an actual same-file test traceback, not by its conventional name alone.
    frames = set()
    for item in failure.get("failures", []):
        for path, function in re.findall(r'File "([^"\n]+)", line \d+, in (\w+)', item.get("excerpt", "")):
            if path.replace("\\", "/").endswith("/" + file.path):
                frames.add(function)
    owners = {definitions[name]["owner"] for name in names}
    for key, item in definitions.items():
        if (item["node"].name in frames and not item["node"].name.startswith("test")
                and item["owner"] in owners | {None}):
            allowed[key] = set(names)
    pending = list(allowed)
    while pending:
        key = pending.pop()
        for helper in _called_helpers(definitions[key], definitions):
            previous = set(allowed.get(helper, ()))
            allowed.setdefault(helper, set()).update(allowed[key])
            if allowed[helper] != previous:
                pending.append(helper)
    if len(allowed) > 80:
        raise ValueError("Reference repair definition scope exceeds its bound")
    return tree, definitions, allowed


def reference_method_patch_inputs(suite, failure, *, byte_limit, policy=METHOD_PATCH_POLICY, history=None):
    if policy == METHOD_PATCH_POLICY_V4:
        inputs = reference_method_patch_inputs(suite, failure, byte_limit=byte_limit, policy=METHOD_PATCH_POLICY_V3)
        inputs["method_patch_policy"] = METHOD_PATCH_POLICY_V4
        inputs["scope_projection"]["policy"] = METHOD_PATCH_POLICY_V4
        inputs["reference_attempt_history"] = project_reference_history(
            history if history is not None else empty_reference_history(), failure)
        return inputs
    if policy == METHOD_PATCH_POLICY_V3:
        inputs = reference_method_patch_inputs(suite, failure, byte_limit=byte_limit, policy=METHOD_PATCH_POLICY_V2)
        original = next(file for file in suite.files if file.path == failure["path"])
        inputs["method_patch_policy"] = METHOD_PATCH_POLICY_V3
        # Read access and edit authority are independent. Global fixtures and passing
        # cases can explain a failure, while the composer still limits changed spans.
        inputs["read_only_context"] = original.content
        inputs["read_only_support_files"] = [file.model_dump() for file in suite.files if file.path != original.path]
        inputs["scope_projection"] = {**inputs["scope_projection"], "policy": METHOD_PATCH_POLICY_V3,
            "omitted_definitions": [], "read_only_suite_files": {
                file.path: digest(file.model_dump()) for file in suite.files},
            "context_policy": "complete-test-suite-read-only-v1"}
        return inputs
    if policy == METHOD_PATCH_POLICY_V2:
        inputs = reference_method_patch_inputs(suite, failure, byte_limit=byte_limit, policy=METHOD_PATCH_POLICY)
        inputs["method_patch_policy"] = METHOD_PATCH_POLICY_V2
        inputs["scope_projection"]["policy"] = METHOD_PATCH_POLICY_V2
        for target in inputs["targets"]:
            target["original_source"] = target.pop("source")
            inputs["read_only_context"] = inputs["read_only_context"].replace(
                f"# definition {target['target']} omitted; retained by harness",
                f"# editable definition {target['target']} omitted here; correct it through an authorized target patch")
        return inputs
    if policy != METHOD_PATCH_POLICY:
        raise ValueError("Unknown reference method patch policy")
    original = next((file for file in suite.files if file.path == failure.get("path")), None)
    if original is None:
        raise ValueError("Reference repair file is absent from the current test draft")
    tree, definitions, allowed = _scope(original, failure)
    targets = [{"target": key, "original_digest": item["original_digest"], "source": item["source"],
                "authorized_by": sorted(allowed[key]), "kind": "test" if key in failure["names"] else "helper"}
               for key, item in definitions.items() if key in allowed]
    # Keep imports/class attributes visible without asking the author to reproduce passing method bodies.
    context = original.content
    for key, item in sorted(definitions.items(), key=lambda pair: pair[1]["start"], reverse=True):
        context = context[:item["start"]] + item["indent"] + f"# definition {key} omitted; retained by harness" + context[item["end"]:]
    imports = {node.module.split(".")[0] for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom) and node.module}
    imports.update(alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                   for alias in node.names)
    support = [file.model_dump() for file in suite.files
               if file.path != original.path and file.path.startswith("tests/")
               and file.path.rsplit("/", 1)[-1].removesuffix(".py") in imports]
    return {"method_patch_policy": METHOD_PATCH_POLICY, "source_file": original.path,
            "previous_suite_digest": digest(suite.model_dump()), "original_file_digest": digest(original.model_dump()),
            "required_tests": list(failure["names"]), "diagnostics": [failure], "targets": targets,
            "read_only_context": context, "read_only_support_files": support,
            "max_replacement_utf8_bytes": byte_limit,
            "scope_projection": {"policy": METHOD_PATCH_POLICY, "original_file_digest": digest(original.model_dump()),
                "authorized_targets": [item["target"] for item in targets],
                "omitted_definitions": [key for key in definitions if key not in allowed]}}


def reference_diagnosis_inputs(suite, failure, *, byte_limit, policy=DIAGNOSIS_POLICY, history=None):
    if policy == DIAGNOSIS_POLICY_V2:
        inputs = reference_diagnosis_inputs(suite, failure, byte_limit=byte_limit)
        projected = project_reference_history(history if history is not None else empty_reference_history(), failure)
        inputs["diagnosis_policy"] = DIAGNOSIS_POLICY_V2
        inputs["scope_projection"]["policy"] = METHOD_PATCH_POLICY_V4
        inputs["reference_attempt_history"] = projected
        inputs["evidence_catalog"].extend({"id": f"prior:{index}", "kind": "prior_reference_attempt",
            "record_digest": digest(record), "has_attempt": record["attempt"] is not None,
            "test_names": sorted(set(record["failure"]["names"]) & set(failure["names"]))}
            for index, record in enumerate(projected["records"]))
        return inputs
    if policy != DIAGNOSIS_POLICY:
        raise ValueError("Unknown reference diagnosis policy")
    inputs = reference_method_patch_inputs(suite, failure, byte_limit=byte_limit, policy=METHOD_PATCH_POLICY_V3)
    inputs.pop("method_patch_policy")
    evidence = []
    for index, name in enumerate(failure["names"]):
        excerpts = [row["excerpt"] for row in failure.get("failures", [])
                    if row.get("test") in {name, name.rsplit(".", 1)[-1]}
                    and isinstance(row.get("excerpt"), str) and row["excerpt"].strip()]
        if not excerpts:
            raise ValueError("Reference diagnosis requires retained failure evidence for every named case")
        evidence.append({"id": f"failure:{index}", "kind": "failure", "test_name": name,
                         "source_file": failure["path"], "excerpts": excerpts})
    evidence.extend({"id": f"source:{index}", "kind": "test_source", "source_file": file.path,
                     "content_digest": digest(file.content)} for index, file in enumerate(suite.files))
    return {**inputs, "diagnosis_policy": DIAGNOSIS_POLICY, "evidence_catalog": evidence}


def reference_diagnosis_type(inputs):
    names = inputs["required_tests"]
    evidence_ids = [row["id"] for row in inputs["evidence_catalog"]]
    if not names or len(names) > 80 or len(names) != len(set(names)):
        raise ValueError("Reference diagnosis requires a bounded exact failing-case set")
    evidence_id = Annotated[str, Field(json_schema_extra={"enum": evidence_ids})]
    policy = inputs["diagnosis_policy"]
    if policy not in {DIAGNOSIS_POLICY, DIAGNOSIS_POLICY_V2}:
        raise ValueError("Unknown reference diagnosis policy")
    base = ReferenceCaseDiagnosisV2 if policy == DIAGNOSIS_POLICY_V2 else ReferenceCaseDiagnosis
    base_response = ReferenceDiagnosisV2 if policy == DIAGNOSIS_POLICY_V2 else ReferenceDiagnosis
    case = create_model("BoundReferenceCaseDiagnosis", __base__=base,
        test_name=(str, Field(json_schema_extra={"enum": names})),
        evidence_ids=(list[evidence_id], Field(min_length=2, max_length=20)))
    return create_model("BoundReferenceDiagnosis", __base__=base_response,
        cases=(list[case], Field(min_length=len(names), max_length=len(names))))


def validate_reference_diagnosis(suite, failure, value, *, byte_limit, policy=DIAGNOSIS_POLICY, history=None):
    inputs = reference_diagnosis_inputs(suite, failure, byte_limit=byte_limit, policy=policy, history=history)
    diagnosis = reference_diagnosis_type(inputs).model_validate(value)
    names = [row.test_name for row in diagnosis.cases]
    if len(names) != len(set(names)) or set(names) != set(failure["names"]):
        raise ValueError("Reference diagnosis must cover every failed case exactly once")
    catalog = {row["id"]: row for row in inputs["evidence_catalog"]}
    for row in diagnosis.cases:
        if len(row.evidence_ids) != len(set(row.evidence_ids)) or set(row.evidence_ids) - catalog.keys():
            raise ValueError("Reference diagnosis must cite unique supplied evidence IDs")
        selected = [catalog[key] for key in row.evidence_ids]
        if (not any(item["kind"] == "failure" and item["test_name"] == row.test_name for item in selected)
                or not any(item["kind"] == "test_source" for item in selected)):
            raise ValueError("Reference diagnosis must cite its own failure and current test source evidence")
        if (policy == DIAGNOSIS_POLICY_V2
                and any(item["kind"] == "prior_reference_attempt" and item["has_attempt"] and row.test_name in item["test_names"]
                        for item in catalog.values())
                and not any(item["kind"] == "prior_reference_attempt" and item["has_attempt"] and row.test_name in item["test_names"]
                            for item in selected)):
            raise ValueError("Reference diagnosis must consider the supplied history for this failed case")
    proof = {"policy": policy, "diagnosis_digest": digest(value),
            "previous_suite_digest": digest(suite.model_dump()), "failure_digest": digest(failure),
            "evidence_catalog_digest": digest(inputs["evidence_catalog"]), "case_names": sorted(names)}
    if policy == DIAGNOSIS_POLICY_V2:
        proof["history_projection_digest"] = digest(inputs["reference_attempt_history"])
    return proof


def reference_diagnosis_binding(suite, failure, value, *, stage, byte_limit, policy=DIAGNOSIS_POLICY, history=None):
    if not DIAGNOSIS_STAGE.fullmatch(stage):
        raise ValueError("Reference diagnosis binding requires its finite diagnosis stage")
    proof = validate_reference_diagnosis(suite, failure, value, byte_limit=byte_limit, policy=policy, history=history)
    selected = {key for row in value["cases"] for key in row["evidence_ids"]}
    inputs = reference_diagnosis_inputs(suite, failure, byte_limit=byte_limit, policy=policy, history=history)
    return {"stage": stage, "value": value, "proof": proof,
            "evidence": [row for row in inputs["evidence_catalog"] if row["id"] in selected]}


def _signature(node):
    node = copy.deepcopy(node)
    node.body = [ast.Pass()]
    return ast.dump(node, include_attributes=False)


def _bypasses(tree):
    markers = {"skipTest", "SkipTest", "expectedFailure", "skip", "skipIf", "skipUnless"}
    return Counter(node.id if isinstance(node, ast.Name) else node.attr for node in ast.walk(tree)
                   if isinstance(node, (ast.Name, ast.Attribute))
                   and (node.id if isinstance(node, ast.Name) else node.attr) in markers)


def compose_reference_method_patch(suite, failure, value, *, byte_limit, policy=METHOD_PATCH_POLICY):
    """Re-derive all edit authority from retained source and the observed failure list."""
    if policy in {METHOD_PATCH_POLICY_V2, METHOD_PATCH_POLICY_V3, METHOD_PATCH_POLICY_V4}:
        patch = ReferenceMethodPatchV2.model_validate(value)
        translated = {"changes": [{"target": change.target, "original_digest": change.expected_original_digest,
                                    "source": change.replacement_source} for change in patch.changes]}
        revised, proof = compose_reference_method_patch(suite, failure, translated, byte_limit=byte_limit,
                                                       policy=METHOD_PATCH_POLICY)
        return revised, {**proof, "policy": policy, "patch_digest": digest(value)}
    if policy != METHOD_PATCH_POLICY:
        raise ValueError("Unknown reference method patch policy")
    patch = ReferenceMethodPatch.model_validate(value)
    original = next((file for file in suite.files if file.path == failure.get("path")), None)
    if original is None:
        raise ValueError("Reference repair file is absent from the current test draft")
    before, definitions, allowed = _scope(original, failure)
    seen, edits = set(), []
    for change in patch.changes:
        if change.target not in allowed or change.target in seen:
            raise ValueError("Reference patch must change unique explicitly authorized definitions only")
        seen.add(change.target)
        old = definitions[change.target]
        if change.original_digest != old["original_digest"]:
            raise ValueError("Reference patch definition digest does not match its target")
        source = change.source.rstrip("\r\n")
        if not source.startswith(old["indent"]) or source[len(old["indent"]):].startswith((" ", "\t", "\n", "\r")):
            raise ValueError("Definition replacement must retain its original leading indentation")
        wrapped = "class __WorkbenchScope:\n" + source if old["owner"] else source
        parsed = parse_test_files({original.path: wrapped})[original.path]
        nodes = parsed.body[0].body if old["owner"] and len(parsed.body) == 1 and isinstance(parsed.body[0], ast.ClassDef) else parsed.body
        if len(nodes) != 1 or not isinstance(nodes[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
            raise ValueError("A definition patch must contain exactly one function and no other statements")
        node = nodes[0]
        if _signature(node) != _signature(old["node"]):
            raise ValueError("Reference patch must preserve definition names, signatures and decorators")
        if ast.dump(node, include_attributes=False) == ast.dump(old["node"], include_attributes=False):
            raise ValueError("Every returned definition must change code addressing the named failures; no-op rejected")
        edits.append((old["start"], old["end"], source))
    edits.sort()
    if any(left[1] > right[0] for left, right in zip(edits, edits[1:])):
        raise ValueError("Reference definition patches overlap")
    content = original.content
    for start, end, source in reversed(edits):
        content = content[:start] + source + content[end:]
    replacement = BuildTestFile(path=original.path, content=content)
    from .project_build import validate_reference_test_replacement
    validate_reference_test_replacement(original, replacement, failure["names"], byte_limit)
    after = parse_test_files({replacement.path: replacement.content})[replacement.path]
    if _bypasses(after) - _bypasses(before):
        raise ValueError("Reference patch introduced a skip or expected-failure bypass")
    existing = {(row["code"], tuple(row["names"])) for row in defects(SimpleNamespace(files=[original]))}
    if any((row["code"], tuple(row["names"])) not in existing
           for row in defects(SimpleNamespace(files=[replacement]))):
        raise ValueError("Reference patch introduced a non-discriminating assertion or another test lint defect")
    revised = type(suite).model_validate({**suite.model_dump(), "files": [
        replacement.model_dump() if file.path == original.path else file.model_dump() for file in suite.files]})
    return revised, {"policy": METHOD_PATCH_POLICY, "patch_digest": digest(value),
        "original_file_digest": digest(original.model_dump()), "replacement_file_digest": digest(replacement.model_dump()),
        "previous_suite_digest": digest(suite.model_dump()), "corrected_suite_digest": digest(revised.model_dump()),
        "authorized_targets": sorted(allowed), "changed_targets": sorted(seen)}
