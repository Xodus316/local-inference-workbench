"""Validate selected replacements and produce a reviewable diff."""
import difflib
import json
from . import repository as repo

def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def object_schema(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


TEXT = {"type": "string"}
SCHEMAS = {
    "investigate": object_schema({"plan": TEXT, "risks": TEXT}),
    "patch": object_schema(
        {
            "summary": TEXT,
            "edits": {
                "type": "array",
                "items": object_schema({"path": TEXT, "content": TEXT}),
            },
        }
    ),
    "review": object_schema({"approved": {"type": "boolean"}, "summary": TEXT}),
}


def validate_result(stage, value, scope):
    expected = set(SCHEMAS[stage]["properties"])
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Model result has invalid fields")
    for key in expected - {"edits", "approved"}:
        if (
            not isinstance(value[key], str)
            or not value[key].strip()
            or len(value[key]) > 20000
        ):
            raise ValueError("Model result has invalid text")
    if stage == "review" and type(value["approved"]) is not bool:
        raise ValueError("Review must contain a boolean decision")
    if stage == "patch":
        if len(canonical(value)) > 12000:
            raise ValueError(
                "Proposed replacements exceed the 12000-byte review budget"
            )
        edits = value["edits"]
        if not isinstance(edits, list) or not 1 <= len(edits) <= len(scope["files"]):
            raise ValueError("Patch must contain bounded edits")
        allowed = {item["path"]: item["content"] for item in scope["files"]}
        seen, size = set(), 0
        for edit in edits:
            if (
                not isinstance(edit, dict)
                or set(edit) != {"path", "content"}
                or not isinstance(edit["path"], str)
            ):
                raise ValueError("Invalid edit fields")
            name = repo.relative(edit["path"])
            content = edit["content"]
            if (
                name not in allowed
                or name in seen
                or not isinstance(content, str)
                or "\0" in content
            ):
                raise ValueError(
                    "Patch escapes the selected files or contains invalid content"
                )
            size += len(content.encode())
            if (
                len(content.encode()) > repo.MAX_FILE
                or size > repo.MAX_CONTEXT
                or content == allowed[name]
            ):
                raise ValueError("Patch is unchanged or exceeds its bound")
            seen.add(name)
    return value


def patch_diff(scope, patch):
    originals = {f["path"]: f["content"] for f in scope["files"]}
    parts = []
    for edit in patch["edits"]:
        old, new, name = originals[edit["path"]], edit["content"], edit["path"]
        # Git-compatible headers preserve spaces, Unicode and empty new files.
        a, b = (
            json.dumps("a/" + name, ensure_ascii=False),
            json.dumps("b/" + name, ensure_ascii=False),
        )
        parts.append(f"diff --git {a} {b}\n")
        if old is None:
            parts.append("new file mode 100644\n")
            if new == "":
                parts.append("index 0000000..e69de29\n")
        lines = difflib.unified_diff(
            (old or "").splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="/dev/null" if old is None else a,
            tofile=b,
        )
        for line in lines:
            parts.append(
                line
                if line.endswith("\n")
                else line + "\n\\ No newline at end of file\n"
            )
    return "".join(parts)
