"""Verify independent tests using deterministic single edits in the approved sandbox."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import time

from .build_models import BuildFile, BuildProject
from .store import digest

COMMANDS = ("python -m unittest discover -s tests -v", "python3 -m unittest discover -s tests -v")


def mutation_key(artifact, mutation):
    """Bind a review to exact project contents and edit, never a sampling ordinal."""
    value = {"artifact": artifact.model_dump(),
             "mutation": {k: mutation[k] for k in ("path", "line", "family", "original", "replacement")}}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def timeout_signature(result, allow_inner=False):
    """Require cleanup and unittest evidence; inner errors are opt-in for v3."""
    commands = result.get("commands", [])
    if result.get("status") != "failed" or len(commands) != 1:
        return None
    row = commands[0]
    if row.get("cancelled") or row.get("cleanup_confirmed") is not True:
        return None
    if not row.get("timed_out"):
        return inner_timeout_signature(row) if allow_inner else None
    if row.get("exit_code") is not None:
        return None
    # A verbose test started and never printed a result on its line.
    log = row.get("stderr", "")
    matches = re.findall(r"(?m)^(test_\w+ \([^\n]+\))(?:\n[^\n]+)? \.\.\. [ \t]*\Z", log)
    return matches[-1] if matches else None


def inner_timeout_signature(row):
    """Recognize complete unittest error blocks, never a stray timeout log line."""
    if row.get("exit_code") != 1:
        return None
    log = row.get("stderr", "")
    counts = re.findall(r"(?m)^Ran (\d+) tests? in ", log)
    failures = re.findall(r"(?m)^FAILED \(errors=(\d+)\)$", log)
    if len(counts) != 1 or int(counts[0]) < 1 or len(failures) != 1:
        return None
    blocks = re.findall(r"(?ms)^ERROR: (test_\w+ \([^\n]+\))\n-+\n(.*?)(?=^={10,}|^-{10,}|\Z)", log)
    if not blocks or len(blocks) != int(failures[0]):
        return None
    signatures = []
    for case, trace in blocks:
        lines = trace.strip().splitlines()
        if not lines or not lines[0].startswith("Traceback (most recent call last):"):
            return None
        match = re.fullmatch(r"subprocess\.TimeoutExpired: Command (.+) timed out after (\d+(?:\.\d+)?) seconds", lines[-1])
        if not match or float(match[2]) <= 0:
            return None
        signatures.append((case, match[1], float(match[2])))
    return ("inner-subprocess", tuple(signatures))


def confirm_timeout(initial, control_before, repeat, control_after, timeout, allow_inner=False):
    """Two matching mutant timeouts bracketed by passing, comfortably fast controls."""
    signature = timeout_signature(initial, allow_inner)
    if not signature or timeout_signature(repeat, allow_inner) != signature:
        return False
    control_limit = timeout / 3
    if isinstance(signature, tuple):
        control_limit = min(control_limit, *(s[2] for s in signature[1]))
    for control in (control_before, control_after):
        elapsed = control.get("elapsed_seconds")
        if (outcome(control) != "survived" or type(elapsed) not in (int, float)
                or not 0 <= elapsed <= control_limit):
            return False
    return True


def summary(observations):
    counts = {name: sum(m["outcome"] == name for m in observations)
              for name in ("detected", "detected_timeout", "survived", "inconclusive")}
    counts["sampled"] = len(observations)
    counts["applicable"] = len(observations)
    counts["passed"] = counts["applicable"] > 0 and all(
        m["outcome"] in {"detected", "detected_timeout"} for m in observations)
    return counts


def mutants(artifact, limit):
    """Select deterministic single edits across operator families, not model claims."""
    groups = {name: [] for name in ("drop_operand", "first_item", "boundary", "constant")}
    for item in artifact.files:
        if item.path.startswith("tests/") or not item.path.endswith(".py"):
            continue
        tree = ast.parse(item.content)
        raw = item.content.encode("utf-8")
        lines = raw.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))

        def add(node, replacement, family):
            if len(groups[family]) >= limit:
                return
            start = offsets[node.lineno - 1] + node.col_offset
            end = offsets[node.end_lineno - 1] + node.end_col_offset
            original = raw[start:end].decode("utf-8")
            changed = (raw[:start] + replacement.encode("utf-8") + raw[end:]).decode("utf-8")
            compile(changed, item.path, "exec")  # Parse/compile only, never execute.
            groups[family].append({"path": item.path, "line": node.lineno, "family": family,
                                   "original": original, "replacement": replacement, "content": changed})

        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
                add(node, "(" + ast.get_source_segment(item.content, node.left) + ")", "drop_operand")
            elif isinstance(node, (ast.For, ast.comprehension)):
                segment = ast.get_source_segment(item.content, node.iter)
                add(node.iter, "list(" + segment + ")[:1]", "first_item")
            elif isinstance(node, ast.Compare) and len(node.ops) == 1:
                swaps = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt}
                if type(node.ops[0]) in swaps:
                    changed = copy.deepcopy(node)
                    changed.ops = [swaps[type(node.ops[0])]()]
                    add(node, ast.unparse(changed), "boundary")
            elif isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and type(node.value.value) is int:
                add(node.value, str(node.value.value + 1), "constant")
    result, seen = [], set()
    while any(groups.values()) and len(result) < limit:
        for group in groups.values():
            if not group or len(result) >= limit:
                continue
            candidate = group.pop(0)
            identity = (candidate["path"], candidate["content"])
            if identity not in seen:
                seen.add(identity)
                result.append({"id": len(result) + 1, **candidate})
    return result


def outcome(result):
    """Only actual assertion failures count as detection; errors/timeouts don't."""
    commands = result.get("commands", [])
    if len(commands) != 1:
        return "inconclusive"
    row = commands[0]
    if row.get("timed_out") or row.get("cancelled") or row.get("cleanup_confirmed") is not True:
        return "inconclusive"
    log = row.get("stdout", "") + "\n" + row.get("stderr", "")
    counts = re.findall(r"(?m)^Ran (\d+) tests? in ", log)
    if len(counts) != 1 or int(counts[0]) < 1:
        return "inconclusive"
    if result.get("status") == "passed" and row.get("exit_code") == 0 and re.search(r"(?m)^OK\s*$", log):
        return "survived"
    failure = re.search(r"(?m)^FAILED \(failures=(\d+)\)\s*$", log)
    if result.get("status") == "failed" and row.get("exit_code") == 1 and failure and int(failure[1]) > 0:
        return "detected"
    return "inconclusive"


def check(runtime, state, project, suite, baseline):
    """Measure whether frozen tests detect sampled edits. Surviving edits need review."""
    run_id = state["run_id"]
    bundle = runtime.bundle(state)
    config = bundle["build"]
    limit = config["max_test_mutations"]
    project_digest = digest(project.model_dump())
    report = {
        "policy": "python-unittest-mutations-v1",
        "enabled": bool(limit),
        "project_digest": project_digest,
        "suite_digest": digest(suite.model_dump()),
        "status": "review_required",
        "summary": summary([]),
        "mutations": [],
    }
    if outcome(baseline) != "survived":
        return {**report, "reason": "baseline_execution_unconfirmed"}
    if len(suite.commands) != 1 or suite.commands[0] not in COMMANDS:
        return {**report, "reason": "unsupported_test_command"}
    try:
        edits = mutants(project, limit)
    except (ValueError, SyntaxError, TypeError):
        return {**report, "reason": "mutation_generation_requires_review"}
    if not edits:
        return {**report, "reason": "no_applicable_mutations"}

    timeout = config.get("mutation_timeout_seconds", 30)
    # A repaired implementation or expanded test suite gets its own project slots.
    base_slot = int(project_digest[:16], 16) * 1000 + 100

    def execute(candidate, slot):
        runtime._guard(run_id)
        started = time.monotonic()
        attempt = base_slot + slot
        directory = runtime.build_executor.materialize(run_id, attempt, candidate)
        result = runtime.build_executor.test(
            run_id, attempt, directory, suite.commands, bundle["sandbox"]["image_id"],
            runtime.events[run_id], min(runtime.deadline, started + timeout),
            read_only_project=True,
        )
        runtime._guard(run_id)
        return {**result, "elapsed_seconds": time.monotonic() - started}

    for edit in edits:
        changed_files = [BuildFile(path=f.path, content=edit["content"]) if f.path == edit["path"]
                         else f for f in project.files]
        record = {key: value for key, value in edit.items() if key != "content"}
        record["mutation_key"] = mutation_key(project, edit)
        try:
            variant = BuildProject(summary=project.summary, files=changed_files)
        except ValueError:
            record.update(outcome="inconclusive", reason="mutation_exceeds_project_bounds")
        else:
            slot = edit["id"] * 4
            initial = execute(variant, slot)
            record["outcome"] = outcome(initial)
            record["execution"] = initial
            if timeout_signature(initial, allow_inner=True):
                before = execute(project, slot + 1)
                repeat = execute(variant, slot + 2)
                after = execute(project, slot + 3)
                record["timeout_check"] = {"control_before": before, "repeat": repeat, "control_after": after}
                if confirm_timeout(initial, before, repeat, after, timeout, allow_inner=True):
                    record["outcome"] = "detected_timeout"
        report["mutations"].append(record)
    report["summary"] = summary(report["mutations"])
    if report["summary"]["passed"]:
        return {**report, "status": "passed", "reason": "all_sampled_mutations_detected"}
    return {**report, "reason": "surviving_or_inconclusive_mutations_require_review"}
