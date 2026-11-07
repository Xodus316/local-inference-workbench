"""Bounded, filter-free Git snapshots and detached worktrees (stdlib only)."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path, PurePosixPath

MAX_FILE = 60_000
MAX_CONTEXT = 120_000
MAX_CHECKOUT = 100_000_000


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def relative(name: str, *, evidence=True) -> str:
    if not isinstance(name, str) or not name or len(name) > 500:
        raise ValueError("Supply a bounded relative file path")
    parts = name.split("/")
    if (
        PurePosixPath(name).is_absolute()
        or any(p in {"", ".", ".."} for p in parts)
        or any(ord(c) < 32 for c in name)
        or "\\" in name
        or ":" in name
        or any(p.lower() == ".git" or p.rstrip(". ") != p for p in parts)
        or any(p.split(".", 1)[0].lower() in {"con","prn","aux","nul", *(f"com{i}" for i in range(1,10)), *(f"lpt{i}" for i in range(1,10))} for p in parts)
    ):
        raise ValueError("Unsafe repository path")
    if evidence and any(
        p.lower()
        in {
            ".ssh",
            ".aws",
            ".codex",
            ".agents",
            "node_modules",
            "__pycache__",
            "credentials",
            "id_rsa",
            "id_ed25519",
            "api-token.txt",
        }
        or p.lower().startswith(".env")
        or p.lower().endswith((".pem", ".key", ".p12", ".pfx"))
        for p in parts
    ):
        raise ValueError("Credential and protected paths cannot be selected")
    return name


def file_at(root: Path, name: str) -> Path:
    path = root / relative(name, evidence=False)
    if root.is_symlink() or any(
        p.is_symlink() for p in [path, *path.parents] if p == root or root in p.parents
    ):
        raise ValueError("Symlinks are excluded from repository editing")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("File escapes the checkout")
    return path


def read_text(root: Path, name: str) -> str | None:
    path = file_at(root, name)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > MAX_FILE:
        raise ValueError(
            "Selected files must be regular text files of at most 60000 bytes"
        )
    with os.fdopen(
        os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb"
    ) as handle:
        raw = handle.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE or b"\0" in raw:
        raise ValueError("Selected file is binary or exceeds its bound")
    return raw.decode("utf-8")


def git_command(root: Path, *args: str) -> list[str]:
    return [
        "git",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        f"safe.directory={root}",
        "-C",
        str(root),
        *args,
    ]


def git_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_PAGER="cat",
        GIT_LITERAL_PATHSPECS="1",
        GIT_OPTIONAL_LOCKS="0",
    )
    return env


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        git_command(root, *args),
        env=git_env(),
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode or len(result.stdout) > 4_000_000:
        raise ValueError(
            "Git operation failed; check the repository and available disk space"
        )
    return result.stdout


def freeze(root: Path, names: list[str]):
    if Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise ValueError("Select the Git repository root")
    head = git(root, "rev-parse", "HEAD").decode().strip()
    tracked = set(git(root, "ls-files", "-z", "--", *names).decode().split("\0"))
    files = []
    for name in names:
        content = read_text(root, name)
        if content is not None and name not in tracked:
            raise ValueError(
                "Existing selected files must be tracked; new paths must not exist"
            )
        files.append(
            {
                "path": name,
                "content": content,
                "sha256": None if content is None else digest(content.encode()),
            }
        )
    if sum(len((f["content"] or "").encode()) for f in files) > MAX_CONTEXT:
        raise ValueError("Selected context exceeds 120000 bytes")
    return {"repository": str(root), "head": head, "files": files}


def verify_source(snapshot):
    root = Path(snapshot["repository"])
    if git(root, "rev-parse", "HEAD").decode().strip() != snapshot["head"]:
        raise ValueError("Repository HEAD changed; prepare a new run")
    for item in snapshot["files"]:
        content = read_text(root, item["path"])
        if content != item["content"]:
            raise ValueError("A selected source file changed; prepare a new run")


def checkout(snapshot, destination: Path):
    """Materialize blobs ourselves: worktree checkout must never execute filters/hooks."""
    root = Path(snapshot["repository"])
    entries = []
    tree = git(root, "ls-tree", "-r", "-z", snapshot["head"])
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        name = relative(raw_name.decode(), evidence=False)
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ValueError(
                "This first version requires a repository without tracked symlinks or submodules"
            )
        entries.append((name, mode, oid))
    if len(entries) > 10000:
        raise ValueError("Repository exceeds 10000 tracked files")
    git(
        root,
        "worktree",
        "add",
        "--detach",
        "--no-checkout",
        str(destination),
        snapshot["head"],
    )
    total, hashes = 0, {}
    with subprocess.Popen(
        git_command(root, "cat-file", "--batch"),
        env=git_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as proc:
        try:
            for name, mode, oid in entries:
                proc.stdin.write((oid + "\n").encode())
                proc.stdin.flush()
                header = proc.stdout.readline(200).decode().strip().split()
                if len(header) != 3 or header[0] != oid or header[1] != "blob":
                    raise ValueError("Invalid Git object receipt")
                size = int(header[2])
                total += size
                if size < 0 or total > MAX_CHECKOUT:
                    raise ValueError("Repository checkout exceeds 100 MB")
                raw = proc.stdout.read(size)
                if len(raw) != size or proc.stdout.read(1) != b"\n":
                    raise ValueError("Incomplete Git object")
                path = file_at(destination, name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                path.chmod(0o755 if mode == "100755" else 0o644)
                hashes[name] = digest(raw)
        finally:
            proc.stdin.close()
            proc.terminate()
    git(destination, "read-tree", snapshot["head"])
    # Overlay selected working changes, exactly as shown in the frozen scope.
    for item in snapshot["files"]:
        if item["content"] is not None:
            path = file_at(destination, item["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item["content"].encode())
            hashes[item["path"]] = item["sha256"]
        elif item["path"] in hashes:
            raise ValueError(
                "A selected new path exists in HEAD; restore or select an existing file"
            )
    return hashes


def verify_checkout(root: Path, hashes: dict):
    for name, expected in hashes.items():
        path = file_at(root, name)
        if (
            not path.is_file()
            or path.stat().st_size > MAX_CHECKOUT
            or digest(path.read_bytes()) != expected
        ):
            raise ValueError("The isolated checkout changed; prepare a new run")
