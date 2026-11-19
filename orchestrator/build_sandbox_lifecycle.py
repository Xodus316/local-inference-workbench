"""Bounded Docker create/start with durable, exact-owner reconciliation.

A stopped CLI is not proof that the daemon did nothing. No foreign container is
removed, and a timed-out create followed only by absence remains inconclusive.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path


POLICY = "owned-build-container-v1"
OWNER_LABEL = "org.workbench.build-owner"
STDOUT_CAPTURE_POLICY = "bounded-stdout-capture-v1"
MAX_STDOUT_CAPTURE_BYTES = 2 * 1024 * 1024


def complete_stdout_capture(row, limit):
    """Rederive complete UTF-8 output from its bounded stream receipt."""
    capture = row.get("stdout_capture")
    output = row.get("stdout")
    if not isinstance(capture, dict) or not isinstance(output, str):
        return False
    data = output.encode("utf-8")
    return (capture.get("policy") == STDOUT_CAPTURE_POLICY
            and type(capture.get("limit_bytes")) is int and capture["limit_bytes"] == limit
            and type(capture.get("total_bytes")) is int and capture["total_bytes"] == len(data) <= limit
            and type(capture.get("captured_bytes")) is int and capture["captured_bytes"] == len(data)
            and capture.get("truncated") is False and capture.get("complete") is True
            and capture.get("utf8_valid") is True
            and capture.get("sha256") == capture.get("captured_sha256") == hashlib.sha256(data).hexdigest())


def _validate_run_argv(args, name, image_id):
    """Parse only the finite Docker options produced by the owned executor."""
    if not isinstance(args, list) or args[:2] != ["docker", "run"] or any(
            not isinstance(part, str) or "\x00" in part for part in args):
        raise ValueError("Invalid owned Docker argument vector")
    valued = {"--name", "--pull", "--network", "--ipc", "--cap-drop", "--security-opt",
              "--pids-limit", "--memory", "--memory-swap", "--cpus", "--ulimit", "--tmpfs",
              "--mount", "--workdir", "--env", "--entrypoint"}
    switches = {"--rm", "--read-only", "--log-driver=none"}
    index, names = 2, []
    while index < len(args) and args[index].startswith("--"):
        option = args[index]
        if option in switches:
            index += 1
        elif option in valued and index + 1 < len(args):
            if option == "--name":
                names.append(args[index + 1])
            index += 2
        else:
            raise ValueError("Unsupported owned Docker lifecycle option")
    if names != [name] or index >= len(args) or args[index] != image_id:
        raise ValueError("Actual Docker name or image differs from owned create intent")
    return index


def _save(directory, name, value):
    path = directory / name
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def _run(args, timeout):
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, check=False)


def _decode(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _inspect_reports_absence(result, subject):
    # Docker CLI versions differ in diagnostic casing; the target must match exactly.
    diagnostic, _, reported_subject = _decode(result.stderr).strip().rpartition(": ")
    return (result.returncode != 0 and diagnostic.lower() == "error: no such object"
            and reported_subject == subject)


def inspect_owned(name, nonce, image_id, *, owner=None, timeout=15):
    subject = owner or name
    result = _run(["docker", "inspect", subject], timeout)
    if result.returncode:
        if _inspect_reports_absence(result, subject):
            return None
        raise ValueError("Docker did not confirm exact container presence or absence")
    value = json.loads(_decode(result.stdout))
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ValueError("Docker returned an ambiguous container inventory")
    row = value[0]
    config, state = row.get("Config") or {}, row.get("State") or {}
    identity = row.get("Id")
    if (not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity)
            or row.get("Name") != "/" + name or row.get("Image") != image_id
            or config.get("Image") != image_id
            or (config.get("Labels") or {}).get(OWNER_LABEL) != nonce
            or owner is not None and owner != identity):
        raise ValueError("Container name, nonce, image or ID differs from owned create intent")
    return {"container_id": identity, "name": name, "image_id": image_id,
            "ownership_nonce": nonce, "state": {key: state.get(key) for key in
            ("Status", "Running", "Paused", "Restarting", "Dead", "Pid", "ExitCode", "Error", "OOMKilled")}}


def remove_owned(name, nonce, image_id, *, owner=None, timeout=15):
    """Resolve ownership before any destructive request; verify absence afterward."""
    observed = inspect_owned(name, nonce, image_id, owner=owner, timeout=timeout)
    if observed is None:
        return {"confirmed": True, "outcome": "exact-id-absent" if owner else "exact-name-absent", "observed": None}
    identity = observed["container_id"]
    removal = _run(["docker", "rm", "--force", identity], timeout)
    # Even a successful client reply is followed by a daemon absence proof.
    remaining = _run(["docker", "inspect", identity], timeout)
    absent = _inspect_reports_absence(remaining, identity)
    return {"confirmed": absent, "outcome": "removed" if absent else "unresolved",
            "container_id": identity, "observed": observed,
            "remove_exit_code": removal.returncode,
            "absence_confirmed": absent}


def run_owned_container(args, timeout, name, cancel=None, deadline=None, failure_stderr_bytes=4000,
                        *, image_id, directory, create_timeout_seconds=180, cleanup_timeout_seconds=15,
                        stdout_capture_bytes=4000):
    """Run one command; cleanup is separate from normal execution completion."""
    if (args[:2] != ["docker", "run"] or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", name)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id)):
        raise ValueError("Owned execution requires an exact container name and immutable image")
    image_index = _validate_run_argv(args, name, image_id)
    for value, maximum in ((timeout, 86400), (create_timeout_seconds, 600), (cleanup_timeout_seconds, 120)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= maximum:
            raise ValueError("Invalid bounded Docker lifecycle timeout")
    if type(failure_stderr_bytes) is not int or not 1 <= failure_stderr_bytes <= 131072:
        raise ValueError("Invalid bounded failure output limit")
    if type(stdout_capture_bytes) is not int or not 1 <= stdout_capture_bytes <= MAX_STDOUT_CAPTURE_BYTES:
        raise ValueError("Invalid bounded stdout capture limit")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    nonce = uuid.uuid4().hex
    intent = {"policy": POLICY, "name": name, "image_id": image_id, "ownership_label": OWNER_LABEL,
              "ownership_nonce": nonce, "create_timeout_seconds": create_timeout_seconds,
              "command_timeout_seconds": timeout, "cleanup_timeout_seconds": cleanup_timeout_seconds,
              "stdout_capture_bytes": stdout_capture_bytes,
              "argv_sha256": hashlib.sha256(json.dumps(args, ensure_ascii=False).encode()).hexdigest()}
    _save(directory, "intent.json", intent)
    result = {"exit_code": None, "timed_out": False, "cancelled": False,
              "cleanup_confirmed": False, "stdout": "", "stderr": ""}
    lifecycle = {**intent, "create_dispatched": False, "start_dispatched": False,
                 "create_client_completed": False, "native_completion_claim": False}
    owner = None
    process = None
    readers = []
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    stdout_count = 0
    stdout_hash = hashlib.sha256()
    drain_errors = []
    stop_at = min(time.monotonic() + timeout, deadline if deadline is not None else float("inf"))

    def guard():
        if cancel is not None and cancel.is_set():
            result["cancelled"] = True
            raise InterruptedError("Sandbox cancelled before dispatch")
        if time.monotonic() >= stop_at:
            result["timed_out"] = True
            raise TimeoutError("Sandbox deadline expired before dispatch")

    def drain(stream, key):
        nonlocal stdout_count
        try:
            with stream:
                while block := stream.read(4096):
                    if key == "stdout":
                        stdout_count += len(block)
                        stdout_hash.update(block)
                    buffers[key].extend(block)
                    limit = 131072 if key == "stderr" else stdout_capture_bytes
                    if len(buffers[key]) > limit:
                        del buffers[key][:-limit]
        except Exception as error:
            drain_errors.append(type(error).__name__)

    try:
        guard()
        # The original restricted run arguments remain exact except lifecycle
        # flags; the daemon retains the container until ID-bound cleanup.
        flags = list(args[2:image_index])
        if "--rm" in flags:
            flags.remove("--rm")
        create = ["docker", "create", "--label", OWNER_LABEL + "=" + nonce, *flags, *args[image_index:]]
        _save(directory, "create-dispatch.json", {"name": name, "ownership_nonce": nonce})
        lifecycle["create_dispatched"] = True
        created = _run(create, min(create_timeout_seconds, max(.001, stop_at - time.monotonic())))
        lifecycle["create_client_completed"] = True
        lifecycle["create_exit_code"] = created.returncode
        _save(directory, "create-result.json", {"exit_code": created.returncode,
              "stdout": _decode(created.stdout)[-4000:], "stderr": _decode(created.stderr)[-4000:]})
        observed = inspect_owned(name, nonce, image_id, timeout=cleanup_timeout_seconds)
        if observed is not None:
            owner = observed["container_id"]
            lifecycle["container_id"] = owner  # Retain ownership before receipt I/O.
            _save(directory, "owner.json", observed)
        if created.returncode != 0 or owner is None or _decode(created.stdout).strip() != owner:
            raise ValueError("Docker create did not return the exact verified owner")
        if observed["state"].get("Status") != "created" or observed["state"].get("Running") is not False:
            raise ValueError("New owned container is not in its initial stopped state")
        guard()
        _save(directory, "start-dispatch.json", {"container_id": owner})
        lifecycle["start_dispatched"] = True
        process = subprocess.Popen(["docker", "start", "--attach", owner], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for key in buffers:
            reader = threading.Thread(target=drain, args=(getattr(process, key), key), daemon=True)
            readers.append(reader)
            reader.start()
        while process.poll() is None and time.monotonic() < stop_at and not (cancel and cancel.is_set()):
            time.sleep(.1)
        if process.poll() is None:
            result["cancelled"] = bool(cancel and cancel.is_set())
            result["timed_out"] = not result["cancelled"]
        else:
            observed = inspect_owned(name, nonce, image_id, owner=owner, timeout=cleanup_timeout_seconds)
            if (observed is None or observed["state"].get("Status") != "exited"
                    or observed["state"].get("Running") is not False
                    or type(observed["state"].get("Pid")) is not int or observed["state"]["Pid"] != 0
                    or type(observed["state"].get("ExitCode")) is not int
                    or observed["state"].get("Error") or observed["state"].get("OOMKilled") is True):
                raise ValueError("Attached client finished without an exact exited container receipt")
            result["exit_code"] = observed["state"]["ExitCode"]
            lifecycle["terminal"] = observed
            lifecycle["attach_exit_code"] = process.returncode
            if process.returncode != result["exit_code"]:
                raise ValueError("Attached client and container exit codes disagree")
    except Exception as error:
        lifecycle["error"] = {"type": type(error).__name__, "message": str(error)[:2000]}
        result["exit_code"] = None
        if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
            result["timed_out"] = True
    finally:
        try:
            if lifecycle["create_dispatched"]:
                cleanup = remove_owned(name, nonce, image_id, owner=owner, timeout=cleanup_timeout_seconds)
                # An absent name alone cannot settle a daemon create whose
                # client timed out; a late create could otherwise be orphaned.
                if not lifecycle["create_client_completed"] and cleanup["observed"] is None:
                    cleanup.update(confirmed=False, outcome="create-outcome-unknown")
            else:
                cleanup = {"confirmed": True, "outcome": "not-dispatched", "observed": None}
            result["cleanup_confirmed"] = cleanup["confirmed"]
        except Exception as error:
            cleanup = {"confirmed": False, "outcome": "unresolved", "error_type": type(error).__name__,
                       "error": str(error)[:2000]}
        lifecycle["cleanup"] = cleanup
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=cleanup_timeout_seconds)
            except (OSError, subprocess.TimeoutExpired) as error:
                lifecycle["attach_cleanup_error"] = type(error).__name__
                result["cleanup_confirmed"] = False
        for reader in readers:
            reader.join(cleanup_timeout_seconds)
        if any(reader.is_alive() for reader in readers) or drain_errors:
            lifecycle["stream_drain_errors"] = drain_errors
            result["cleanup_confirmed"] = False
        if result["exit_code"] != 0:
            del buffers["stderr"][:-failure_stderr_bytes]
        result.update({key: bytes(value).decode("utf-8", errors="replace") for key, value in buffers.items()})
        captured = bytes(buffers["stdout"])
        try:
            captured.decode("utf-8", errors="strict")
            utf8_valid = True
        except UnicodeDecodeError:
            utf8_valid = False
        truncated = stdout_count > len(captured)
        result["stdout_capture"] = {
            "policy": STDOUT_CAPTURE_POLICY, "limit_bytes": stdout_capture_bytes,
            "total_bytes": stdout_count, "captured_bytes": len(captured), "truncated": truncated,
            "sha256": stdout_hash.hexdigest(), "captured_sha256": hashlib.sha256(captured).hexdigest(),
            "utf8_valid": utf8_valid,
            "complete": (not truncated and utf8_valid and not drain_errors
                         and not any(reader.is_alive() for reader in readers)
                         and type(result["exit_code"]) is int),
        }
        lifecycle["execution_completion_known"] = type(result["exit_code"]) is int
        lifecycle["cleanup_confirmed"] = result["cleanup_confirmed"]
        _save(directory, "result.json", {**result, "lifecycle": lifecycle})
    return {**result, "lifecycle": lifecycle}
