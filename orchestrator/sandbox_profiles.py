"""Explicit, source-bound verifier environments; never application patches.

Profiles are operator configuration frozen in the approved run. Their files are
trusted verifier tooling, mounted read-only outside the application workspace.
Application-specific bootstraps and restart protocols belong to local fixtures.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath

from .store import digest


POLICY = "verification-profile-v1"
DEFAULT_STDOUT_CAPTURE_BYTES = 4000
MAX_STDOUT_CAPTURE_BYTES = 2 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}")


def _text(value, label, maximum=4096):
    if not isinstance(value, str) or not value or len(value) > maximum or any(c in value for c in "\x00\r\n"):
        raise ValueError(f"Invalid verification profile {label}")
    return value


def normalize_verification_profile(value):
    """Validate the complete profile and rehash every trusted local file."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    allowed = {"policy", "name", "phase", "environment", "readonly_files", "commands", "verification", "entrypoint",
               "stdout_capture_bytes", "require_complete_stdout"}
    if not isinstance(value, dict) or set(value) - allowed or value.get("policy", POLICY) != POLICY:
        raise ValueError("Invalid verification profile policy or fields")
    name = _text(value.get("name"), "name", 64)
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
        raise ValueError("Invalid verification profile name")
    phase = value.get("phase", "final")
    if phase not in {"candidate", "final"}:
        raise ValueError("Verification profile phase must be candidate or final")
    entrypoint = value.get("entrypoint")
    if entrypoint is not None:
        entrypoint = _text(entrypoint, "entrypoint")
        path = PurePosixPath(entrypoint)
        if (not path.is_absolute() or path.as_posix() != entrypoint or ".." in path.parts
                or entrypoint == "/" or path.parts[1] in {"workspace", "proc", "sys", "dev", "tmp"}):
            raise ValueError("Verification profile entrypoint must be a trusted absolute container executable")
    environment = value.get("environment", {})
    if not isinstance(environment, dict) or len(environment) > 32:
        raise ValueError("Invalid verification profile environment")
    for key, item in environment.items():
        if (not isinstance(key, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", key)
                or key in {"HOME", "PYTHONDONTWRITEBYTECODE"}):
            raise ValueError("Verification profile cannot override reserved sandbox environment")
        if not isinstance(item, str) or len(item) > 4096 or any(c in item for c in "\x00\r\n"):
            raise ValueError("Invalid verification profile environment value")
    entries = value.get("readonly_files", [])
    if not isinstance(entries, list) or len(entries) > 16:
        raise ValueError("Invalid verification profile file inventory")
    files, targets, total_bytes = [], set(), 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"source", "target", "sha256"}:
            raise ValueError("Invalid verification profile file binding")
        source_text = _text(entry["source"], "source")
        source = Path(source_text)
        target = _text(entry["target"], "target")
        posix = PurePosixPath(target)
        if (not source.is_absolute() or not source.is_file() or source.is_symlink()
                or source.resolve() != source.absolute() or "," in source_text
                or not posix.is_absolute() or posix.as_posix() != target or ".." in posix.parts
                or target == "/" or "," in target
                or posix.parts[1] in {"workspace", "proc", "sys", "dev", "tmp"}
                or target in targets):
            raise ValueError("Profile files must be exact local files outside the application and sandbox control paths")
        if any(a == b or a in b.parents or b in a.parents for a in [posix] for b in map(PurePosixPath, targets)):
            raise ValueError("Verification profile mount targets overlap")
        if not isinstance(entry["sha256"], str) or not _SHA.fullmatch(entry["sha256"]):
            raise ValueError("Invalid verification profile source digest")
        size = source.stat().st_size
        total_bytes += size
        if size > 8 * 1024 * 1024 or total_bytes > 16 * 1024 * 1024:
            raise ValueError("Verification profile tooling exceeds its bounded byte inventory")
        if hashlib.sha256(source.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("Verification profile source changed")
        targets.add(target)
        files.append({"source": str(source.resolve()), "target": target, "sha256": entry["sha256"]})
    commands = value.get("commands")
    requirements = value.get("verification")
    if commands is not None:
        if not isinstance(commands, list) or len(commands) != 1:
            raise ValueError("A companion profile requires one bounded trusted command")
        commands = [_text(command, "command", 16384) for command in commands]
        if requirements is None:
            raise ValueError("Companion commands require explicit verifier result checks")
    if requirements is not None:
        if (not isinstance(requirements, dict)
                or set(requirements) - {"policy", "required_checks", "marker"}
                or not isinstance(requirements.get("required_checks"), list)
                or not requirements["required_checks"] or len(requirements["required_checks"]) > 64):
            raise ValueError("Invalid verification profile result contract")
        checks = [_text(c, "check", 128) for c in requirements["required_checks"]]
        if len(set(checks)) != len(checks):
            raise ValueError("Verification profile result checks repeat")
        marker = _text(requirements.get("marker", "OFFLINE_PROJECT_RESULT"), "marker", 128)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", marker):
            raise ValueError("Invalid verification profile result marker")
        requirements = {"policy": _text(requirements.get("policy"), "result policy", 128),
                        "required_checks": checks, "marker": marker}
    capture = value.get("stdout_capture_bytes", DEFAULT_STDOUT_CAPTURE_BYTES)
    complete = value.get("require_complete_stdout", False)
    if type(capture) is not int or not 1 <= capture <= MAX_STDOUT_CAPTURE_BYTES or type(complete) is not bool:
        raise ValueError("Invalid verification profile stdout capture contract")
    result = {"policy": POLICY, "name": name, "phase": phase, "entrypoint": entrypoint,
              "environment": dict(sorted(environment.items())),
              "readonly_files": files, "commands": commands, "verification": requirements}
    # Preserve existing frozen profile identities when capture is not selected.
    if capture != DEFAULT_STDOUT_CAPTURE_BYTES:
        result["stdout_capture_bytes"] = capture
    if complete:
        result["require_complete_stdout"] = True
    return result


def verification_profile_identity(profile, image_id):
    profile = normalize_verification_profile(profile)
    if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("Verification profiles require the approved immutable image ID")
    identity = {"policy": POLICY, "name": profile["name"], "phase": profile["phase"], "profile_digest": digest(profile),
                "image_id": image_id, "environment": profile["environment"],
                "entrypoint": profile["entrypoint"],
                "readonly_files": [{"target": row["target"], "sha256": row["sha256"]}
                                   for row in profile["readonly_files"]],
                "commands": profile["commands"], "verification": profile["verification"],
                "application_and_test_bytes_changed": False,
                "pristine_image_only": not profile["environment"] and not profile["readonly_files"] and profile["entrypoint"] is None}
    identity.update({key: profile[key] for key in ("stdout_capture_bytes", "require_complete_stdout") if key in profile})
    return {**identity, "digest": digest(identity)}
