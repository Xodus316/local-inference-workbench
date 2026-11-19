"""Harness-only trusted reference implementation for validating generated tests.

An operator can bind a directory containing a known-good implementation of the
specification. Before independent tests are frozen, the workflow runs them
against this reference in the same pinned image. A test that the reference
fails is a test defect, not an implementation defect, and goes back to the
test author.

The reference never enters a model prompt: not its source, not its paths, not
its logs. Only failing test identities and test-side unittest excerpts are
returned to the test author.
"""
from __future__ import annotations

from pathlib import Path

from .store import digest

POLICY = "harness-reference-v1"
SKIPPED_DIRECTORIES = frozenset({
    ".git", ".gradle", ".idea", ".vscode", "build", "dist", "node_modules", "out", "target", "__pycache__",
})
# Dependency locks, wrapper scripts and local environment files are supplied by
# the pinned image or are not part of the implementation contract.
SKIPPED_FILES = frozenset({"package-lock.json", "gradlew", "gradlew.bat", ".env", ".env.local"})
SKIPPED_PREFIXES = ("api/gradle/wrapper/", "tests/")


def reference_files(root):
    """Return the text files of a reference implementation as Build files, sorted by path."""
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("The trusted reference must be a directory")
    files = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in SKIPPED_DIRECTORIES for part in relative.parts[:-1]) or not path.is_file():
            continue
        name = relative.as_posix()
        if relative.name in SKIPPED_FILES or name.startswith(SKIPPED_PREFIXES):
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue  # binary files are never implementation source here
        files.append({"path": name, "content": text.replace("\r\n", "\n")})
    if not files:
        raise ValueError("The trusted reference contains no text files")
    return files


def pack_reference(root):
    """Bind a reference directory for approval: its files and their digest."""
    from .project_build import ProjectArtifact

    files = reference_files(root)
    ProjectArtifact.model_validate({"summary": "Trusted reference", "files": files})
    return {"policy": POLICY, "files": files, "digest": digest(files),
            "file_count": len(files), "bytes": sum(len(f["content"].encode("utf-8")) for f in files)}


def validate_reference(value):
    """Check an approved bundle's packed reference before any use."""
    if value is None:
        return None
    if (not isinstance(value, dict) or value.get("policy") != POLICY or not isinstance(value.get("files"), list)
            or value.get("digest") != digest(value["files"])):
        raise ValueError("The approved trusted reference is malformed or its files changed")
    return value
