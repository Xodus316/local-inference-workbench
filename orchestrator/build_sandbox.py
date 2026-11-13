"""Bounded from-scratch builds with container-only test execution."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path, PurePosixPath

from .repository import relative
from .build_models import BuildArtifact


PYTHON_TEST_COMPILE = """
import json
from pathlib import Path
import sys

problems = []
for path in sorted(Path("tests").rglob("*.py")):
    try:
        compile(path.read_bytes(), path.as_posix(), "exec", dont_inherit=True)
    except SyntaxError as error:
        problems.append({"path": path.as_posix(), "line": error.lineno or 1,
                         "message": error.msg[:160]})
result = {"python_version": ".".join(str(part) for part in sys.version_info[:2]),
          "diagnostics": problems}
print("WORKBENCH_TEST_COMPILE " + json.dumps(result, separators=(",", ":")), file=sys.stderr)
sys.exit(1 if problems else 0)
"""


def final_json_record(output, marker):
    """Read a final record emitted by an approved sandbox entry point."""
    lines = output.rstrip().rsplit("\n", 1)
    prefix = marker + " "
    if not lines[-1].startswith(prefix):
        return None
    try:
        value = json.loads(lines[-1][len(prefix):])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(path.stat().st_file_attributes & 0x400)
    except AttributeError:
        return False


def validate_artifact(artifact: BuildArtifact) -> BuildArtifact:
    identities = set()
    for item in artifact.files:
        item.path = relative(item.path)
        identity = item.path.casefold()
        if identity in identities:
            raise ValueError("Build file paths collide after normalization")
        identities.add(identity)
    for identity in identities:
        if any(
            parent.as_posix() in identities
            for parent in PurePosixPath(identity).parents
            if parent.as_posix() != "."
        ):
            raise ValueError("Build file and directory paths collide")
    return artifact


def _hashes(directory: Path) -> dict[str, str]:
    values = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or _reparse(path):
            raise ValueError("Build output contains a reparse point")
        if path.is_file():
            values[path.relative_to(directory).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return values


class DockerBuildExecutor:
    """Runs untrusted tests only in a locked-down, preinstalled Docker image."""

    def __init__(self, data_directory: Path, config):
        self.directory = data_directory / "build-workspaces"
        self.config = config

    def preflight(self):
        if not self.config.enabled or not self.config.image:
            raise ValueError(
                "Build is disabled until an operator configures a sandbox image"
            )
        image_id = None
        for args in (
            ["docker", "info"],
            ["docker", "image", "inspect", "--format", "{{.Id}}", self.config.image],
        ):
            try:
                result = subprocess.run(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise ValueError("Docker sandbox is unavailable") from None
            if result.returncode:
                raise ValueError("Configured Docker sandbox image is not installed")
            if args[1:3] == ["image", "inspect"]:
                image_id = result.stdout.decode("ascii", errors="strict").strip()
        if not image_id or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError("Docker did not return an immutable image ID")
        return {"engine": "docker", "image": self.config.image, "image_id": image_id}

    def materialize(self, run_id: str, attempt: int, artifact: BuildArtifact) -> Path:
        uuid.UUID(run_id)
        project = self.directory / run_id / f"project-{attempt}"
        expected = {
            item.path: hashlib.sha256(item.content.encode("utf-8")).hexdigest()
            for item in validate_artifact(artifact).files
        }
        if project.exists():
            if _hashes(project) != expected:
                raise ValueError(
                    "Saved build attempt differs from the retained model artifact"
                )
            return project
        project.mkdir(parents=True)
        for item in artifact.files:
            destination = project / item.path
            if not destination.resolve(strict=False).is_relative_to(project.resolve()):
                raise ValueError("Build file escapes the disposable workspace")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(item.content, encoding="utf-8", newline="")
        return project

    @staticmethod
    def _bounded_container(args, timeout, name, cancel=None, deadline=None, failure_stderr_bytes=4000):
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        buffers = {"stdout": bytearray(), "stderr": bytearray()}

        def drain(stream, key):
            with stream:
                while True:
                    block = stream.read(4096)
                    if not block:
                        return
                    buffers[key].extend(block)
                    # Preserve verbose unittest identities on a passing run, while bounding hostile output.
                    limit = 131072 if key == "stderr" else 4000
                    if len(buffers[key]) > limit:
                        del buffers[key][:-limit]

        readers = [
            threading.Thread(
                target=drain, args=(process.stdout, "stdout"), daemon=True
            ),
            threading.Thread(
                target=drain, args=(process.stderr, "stderr"), daemon=True
            ),
        ]
        for reader in readers:
            reader.start()
        timed_out = cancelled = False
        cleanup_confirmed = False
        limit = min(
            time.monotonic() + timeout,
            deadline if deadline is not None else float("inf"),
        )
        while (
            process.poll() is None
            and time.monotonic() < limit
            and not (cancel and cancel.is_set())
        ):
            time.sleep(0.1)
        if process.poll() is None:
            cancelled = bool(cancel and cancel.is_set())
            timed_out, exit_code = not cancelled, None
        else:
            exit_code = process.returncode
        try:
            removed = subprocess.run(
                ["docker", "rm", "--force", name], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
            cleanup_confirmed = removed.returncode == 0
            if not cleanup_confirmed:
                # --rm may have won the race. A working daemon must confirm absence.
                remaining = subprocess.run(
                    ["docker", "ps", "--all", "--quiet", "--filter", f"name=^/{name}$"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    timeout=15, check=False)
                cleanup_confirmed = remaining.returncode == 0 and not remaining.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            cleanup_confirmed = False
        if timed_out or cancelled:
            process.kill()
            process.wait(timeout=15)
        for reader in readers:
            reader.join(5)
        if exit_code != 0:
            del buffers["stderr"][:-failure_stderr_bytes]
        return {
            "exit_code": exit_code,
            "timed_out": timed_out,
            "cancelled": cancelled,
            "cleanup_confirmed": cleanup_confirmed,
            "stdout": bytes(buffers["stdout"]).decode("utf-8", errors="replace"),
            "stderr": bytes(buffers["stderr"]).decode("utf-8", errors="replace"),
        }

    def preflight_tests(self, run_id, attempt, manifest, image, cancel, deadline):
        project = self.materialize(run_id, attempt, manifest)
        return self._run_commands(run_id, attempt, project, ["compile independent Python tests"],
                                  image, cancel, deadline, True, compile_tests=True)

    def test(
        self,
        run_id: str,
        attempt: int,
        project: Path,
        commands: list[str],
        image: str | None = None,
        cancel=None,
        deadline=None,
        read_only_project=True,
    ) -> dict:
        return self._run_commands(run_id, attempt, project, commands, image, cancel, deadline,
                                  read_only_project)

    def _run_commands(self, run_id, attempt, project, commands, image, cancel, deadline,
                      read_only_project, *, compile_tests=False):
        uuid.UUID(run_id)
        results = []
        scratch = self.directory / run_id
        scratch.mkdir(parents=True, exist_ok=True)
        for index, command in enumerate(commands, 1):
            # Keep headroom for nested source paths on Windows. Reserve the short
            # name atomically so failed copies can only remove their own workspace.
            copy = Path(tempfile.mkdtemp(prefix="t-", dir=scratch))
            mount = f"type=bind,source={copy},target=/workspace"
            if read_only_project:
                mount += ",readonly"
            name = f"workbench-build-{run_id[:8]}-{attempt}-{index}-{uuid.uuid4().hex[:8]}"
            args = [
                "docker",
                "run",
                "--rm",
                "--pull",
                "never",
                "--name",
                name,
                "--log-driver=none",
                "--network",
                "none",
                "--read-only",
                "--ipc",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                str(getattr(self.config, "process_limit", 128)),
                "--memory",
                f"{self.config.memory_mb}m",
                "--memory-swap",
                f"{self.config.memory_mb}m",
                "--cpus",
                str(self.config.cpus),
                "--ulimit",
                f"nofile={getattr(self.config, 'open_files', 256)}:{getattr(self.config, 'open_files', 256)}",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev," + ("noexec," if getattr(self.config, "temporary_noexec", False) else "")
                + f"size={getattr(self.config, 'temporary_mb', 64)}m",
                "--mount",
                mount,
                "--workdir",
                "/workspace",
                "--env",
                "HOME=/tmp",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
            ]
            if compile_tests:
                args.extend(["--entrypoint", "python3", image, "-I", "-c", PYTHON_TEST_COMPILE])
            else:
                args.extend([image or self.config.image, "/bin/sh", "-lc", command])
            try:
                shutil.copytree(project, copy, dirs_exist_ok=True)
                failure_stderr_bytes = getattr(self.config, "failure_stderr_bytes", None)
                options = {} if failure_stderr_bytes is None else {"failure_stderr_bytes": failure_stderr_bytes}
                result = self._bounded_container(args, self.config.test_timeout_seconds,
                                                 name, cancel, deadline, **options)
                result["command_index"] = index
            finally:
                shutil.rmtree(copy, ignore_errors=True)
            results.append(result)
            if (
                result["timed_out"]
                or result.get("cancelled")
                or not result["cleanup_confirmed"]
                or result["exit_code"] != 0
            ):
                break
        return {
            "status": "passed"
            if len(results) == len(commands)
            and all(r["exit_code"] == 0 and r["cleanup_confirmed"] and not r["cancelled"] and not r["timed_out"] for r in results)
            else "failed",
            "commands": results,
            "sandbox": {
                "engine": "docker",
                "image": self.config.image,
                "image_id": image,
                "network": "disabled",
                "read_only_root": True,
                "read_only_project": read_only_project,
            },
        }

    def cleanup(self, run_id: str):
        uuid.UUID(run_id)
        target = (self.directory / run_id).resolve(strict=False)
        if target.parent != self.directory.resolve(strict=False):
            raise ValueError("Invalid build workspace identity")
        shutil.rmtree(target, ignore_errors=True)
