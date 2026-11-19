"""Bound repair selection to current source and a single structured decision."""

from collections import Counter
import json
from pathlib import PurePosixPath
import re


INLINE_SELECTION_POLICY = "inline-no-tools-v2"
SELECTION_OUTPUT_TOKENS = 32768
SELECTION_SOURCE_BYTES = 131072
COMPILER_SELECTION_POLICY = "compiler-diagnostic-paths-v1"
_COMPILER_CHECKS = frozenset({"gradle-compile", "gradle-build", "frontend-typecheck", "frontend-build"})
_LOCATION = re.compile(
    r"(?<!\S)(?P<path>[^\s\"'<>()]+?)(?::(?P<line>[1-9][0-9]{0,6})(?::(?P<column>[1-9][0-9]{0,6}))?"
    r"(?=[:\s]|$)|\((?P<ts_line>[1-9][0-9]{0,6}),(?P<ts_column>[1-9][0-9]{0,6})\))"
)


def compiler_diagnostic_selection(files, feedback, *, byte_limit, file_limit=16):
    """Resolve explicit compiler locations against the eligible current inventory.

    This does not infer dependency paths or use source_paths hints. Ambiguous
    diagnostics leave selection to the no-tool fallback. When the complete
    scope is too large, prefer the earliest reported paths that fit and
    retain the rest for the next compiler checkpoint.
    """
    inventory = {file.path: file for file in files}
    matches = {}
    for command in (feedback.get("failed_execution") or {}).get("commands", []):
        summary = command.get("sandbox_stage_results") or {}
        if (type(command.get("exit_code")) is not int or command["exit_code"] == 0
                or summary.get("cleanup_confirmed") is not True or summary.get("status") != "failed"):
            continue
        for failure in summary.get("failures", []):
            check = failure.get("stage")
            if check not in _COMPILER_CHECKS or failure.get("status") != "failed":
                continue
            diagnostic = failure.get("diagnostic_excerpt") or failure.get("message", "")
            if not isinstance(diagnostic, str):
                continue
            for location in _LOCATION.finditer(diagnostic):
                reported = location["path"]
                path = reported[7:] if reported.startswith("file:///") else reported
                relative = path[1:] if path.startswith("/") else path
                if (not relative or any(char in relative for char in "\\:%?#\0")
                        or any(part in {"", ".", ".."} for part in relative.split("/"))):
                    return None
                candidates = [name for name in inventory if relative == name or relative.endswith("/" + name)
                              or (not path.startswith("/") and name.endswith("/" + relative))]
                if len(candidates) != 1:
                    return None
                actual = candidates[0]
                line = int(location["line"] or location["ts_line"])
                column = int(location["column"] or location["ts_column"] or 0) or None
                matches[(check, actual, line, column)] = {
                    "compiler_stage": check, "reported_path": reported, "path": actual,
                    "line": line, "column": column,
                }
                if len(matches) > 64:
                    return None
    paths = dict.fromkeys(item["path"] for item in matches.values())
    selected = []
    for path in paths:
        if len(selected) >= file_limit:
            break
        size = len(inventory[path].content.encode("utf-8"))
        if size <= byte_limit:
            selected.append(path)
            byte_limit -= size
    if not selected:
        return None
    return {"paths": sorted(selected), "matches": list(matches.values()),
            "pending_paths": sorted(paths.keys() - set(selected))}


def selection_source(files, evidence, *, byte_limit=SELECTION_SOURCE_BYTES):
    """Prioritize diagnostic paths and their source dependencies without inventing paths.

    The inventory remains complete outside this projection. Every content string
    comes from the candidate; a large projection explicitly reports omitted files.
    """
    by_path = {file.path: file for file in files}
    diagnostic = json.dumps(evidence, ensure_ascii=False).replace("\\\\", "/")
    basenames = Counter(PurePosixPath(path).name for path in by_path)
    mentioned = set()
    for path in by_path:
        name = PurePosixPath(path).name
        if path in diagnostic or (basenames[name] == 1 and re.search(
                r"(?<![\w.-])" + re.escape(name) + r"(?![\w.-])", diagnostic)):
            mentioned.add(path)
    identifiers = {token for token in re.findall(r"\b[A-Za-z_$][A-Za-z0-9_$]{3,}\b", diagnostic)
                   if any(letter.isupper() for letter in token[1:]) or "_" in token}
    identified = {path for path, file in by_path.items() if path not in mentioned and any(
        re.search(r"(?<![\w])" + re.escape(token) + r"(?![\w])", file.content) for token in sorted(identifiers))}
    current = "\n".join(by_path[path].content for path in sorted(mentioned | identified))
    dependencies = set()
    for path in by_path.keys() - mentioned - identified:
        name = PurePosixPath(path).name
        symbol = name.split(".", 1)[0]
        if path in current or name in current or (len(symbol) >= 3 and re.search(
                r"(?<![\w])" + re.escape(symbol) + r"(?![\w])", current)):
            dependencies.add(path)
    order = sorted(by_path, key=lambda path: (
        0 if path in mentioned else 1 if path in identified else 2 if path in dependencies else 3, path))
    projected, remaining = [], byte_limit
    for path in order:
        content = by_path[path].content
        size = len(content.encode("utf-8"))
        if size <= remaining:
            projected.append({"path": path, "content": content})
            remaining -= size
        elif not projected:
            # Keep evidence for the first diagnostic file even if it alone is
            # oversized, while clearly labelling the incomplete source.
            projected.append({"path": path, "content": content.encode("utf-8")[:remaining].decode("utf-8", "ignore"),
                              "truncated": True})
            remaining = 0
    return projected


def projection_details(tree, source, *, policy=INLINE_SELECTION_POLICY):
    shown = {file["path"] for file in source}
    return {"policy": policy, "included_paths": [file["path"] for file in source],
            "omitted_paths": [file["path"] for file in tree if file["path"] not in shown],
            "truncated_paths": [file["path"] for file in source if file.get("truncated")],
            "utf8_bytes": sum(len(file["content"].encode("utf-8")) for file in source)}


def trim_selection_source(source):
    """Remove least relevant whole files first; never leave a metadata-only decision."""
    if len(source) > 1:
        return source[:-1]
    if source and len(source[0]["content"].encode("utf-8")) > 1024:
        data = source[0]["content"].encode("utf-8")
        return [{**source[0], "content": data[:len(data) // 2].decode("utf-8", "ignore"), "truncated": True}]
    raise ValueError("Repair selection evidence and current source do not fit the approved context; inference was not dispatched")


def selection_event_guard(callback):
    """Stop an unexpected exploration loop while it is running, before collection."""
    starts = 0

    def observe(event):
        nonlocal starts
        if callback is not None:
            callback(event)
        if event.get("type") == "step_start":
            starts += 1
        if event.get("type") in {"tool_use", "compaction"} or starts > 1:
            raise ValueError("Inline structured generation permits one model step and no tool calls")

    return observe
