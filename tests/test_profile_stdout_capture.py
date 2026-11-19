"""Bounded companion output is explicit, complete when required, and source-bound."""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from orchestrator import build_sandbox_lifecycle as lifecycle
from orchestrator.build_sandbox import DockerBuildExecutor
from orchestrator.project_build import ProjectArtifact, ProjectProject
from orchestrator.sandbox_profiles import normalize_verification_profile, verification_profile_identity
from orchestrator.store import digest
from orchestrator.whole_project_sandbox import execute_verification_profile, validate_self_suite
from tests.test_whole_project_sandbox import IMAGE, POLICY, suite_value, verifier_receipt


OWNER = "b" * 64
NAME = "workbench-build-output-fixture"


def capture(data, limit=2 * 1024 * 1024):
    return {"policy": lifecycle.STDOUT_CAPTURE_POLICY, "limit_bytes": limit,
            "total_bytes": len(data), "captured_bytes": len(data), "truncated": False,
            "complete": True, "utf8_valid": True,
            "sha256": hashlib.sha256(data).hexdigest(), "captured_sha256": hashlib.sha256(data).hexdigest()}


class ProfileStdoutCaptureTests(unittest.TestCase):
    def run_stream(self, data, *, limit=4000, exit_code=0, attach_code=None):
        nonce, inspections, removed = None, 0, False
        process = Mock(stdout=io.BytesIO(data), stderr=io.BytesIO(b""),
                       returncode=exit_code if attach_code is None else attach_code)
        process.poll.return_value = process.returncode

        def command(args, timeout):
            nonlocal nonce, inspections, removed
            if args[1] == "create":
                nonce = args[args.index("--label") + 1].split("=", 1)[1]
                return SimpleNamespace(returncode=0, stdout=OWNER.encode(), stderr=b"")
            if args[1] == "rm":
                self.assertEqual(args[-1], OWNER)
                removed = True
                return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
            if removed:
                return SimpleNamespace(returncode=1, stdout=b"", stderr=("Error: No such object: " + OWNER).encode())
            inspections += 1
            row = {"Id": OWNER, "Name": "/" + NAME, "Image": IMAGE,
                   "Config": {"Image": IMAGE, "Labels": {lifecycle.OWNER_LABEL: nonce}},
                   "State": {"Status": "created" if inspections == 1 else "exited", "Running": False,
                             "Pid": 0, "ExitCode": exit_code}}
            return SimpleNamespace(returncode=0, stdout=json.dumps([row]).encode(), stderr=b"")

        with tempfile.TemporaryDirectory() as folder, patch.object(lifecycle, "_run", side_effect=command), \
                patch.object(lifecycle.subprocess, "Popen", return_value=process):
            directory = Path(folder) / "attempt"
            result = lifecycle.run_owned_container(["docker", "run", "--rm", "--name", NAME, IMAGE,
                "/bin/sh", "-lc", "fixture"], 10, NAME, image_id=IMAGE, directory=directory,
                stdout_capture_bytes=limit)
            saved = json.loads((directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["stdout_capture"], result["stdout_capture"])
            self.assertEqual(saved["stdout"], result["stdout"])
            self.assertEqual(json.loads((directory / "intent.json").read_text())["stdout_capture_bytes"], limit)
        return result

    def test_default_tail_is_unchanged_but_omission_is_now_measured(self):
        data = b"a" * 5000
        result = self.run_stream(data)
        self.assertEqual(result["stdout"], "a" * 4000)
        self.assertEqual(result["stdout_capture"]["total_bytes"], 5000)
        self.assertTrue(result["stdout_capture"]["truncated"])
        self.assertFalse(lifecycle.complete_stdout_capture(result, 4000))
        self.assertEqual(result["stdout_capture"]["sha256"], hashlib.sha256(data).hexdigest())

    def test_selected_capture_retains_unicode_and_large_complete_logs(self):
        data = ("bounded café logs\n" * 2000).encode()
        result = self.run_stream(data, limit=2 * 1024 * 1024)
        self.assertEqual(result["stdout"].encode(), data)
        self.assertTrue(lifecycle.complete_stdout_capture(result, 2 * 1024 * 1024))
        self.assertEqual(result["stdout_capture"], capture(data))

    def test_oversized_or_unknown_execution_never_claims_complete_output(self):
        limit = 2 * 1024 * 1024
        result = self.run_stream(b"x" * (limit + 1), limit=limit)
        self.assertEqual(result["stdout_capture"]["captured_bytes"], limit)
        self.assertTrue(result["stdout_capture"]["truncated"])
        self.assertFalse(lifecycle.complete_stdout_capture(result, limit))
        result = self.run_stream(b"complete-looking output", exit_code=1, attach_code=0)
        self.assertIsNone(result["exit_code"])
        self.assertTrue(result["cleanup_confirmed"])
        self.assertFalse(lifecycle.complete_stdout_capture(result, 4000))
        failed = self.run_stream(b"settled failure output", exit_code=1)
        self.assertTrue(lifecycle.complete_stdout_capture(failed, 4000))

    def test_lossy_decoding_and_resealed_metadata_do_not_count_as_complete(self):
        result = self.run_stream(b"invalid \xff bytes")
        self.assertFalse(result["stdout_capture"]["utf8_valid"])
        self.assertFalse(lifecycle.complete_stdout_capture(result, 4000))
        good = self.run_stream("valid café".encode())
        for key, value in (("total_bytes", 1), ("captured_bytes", 2), ("limit_bytes", 5000),
                           ("truncated", True), ("complete", False), ("sha256", "f" * 64)):
            changed = deepcopy(good)
            changed["stdout_capture"][key] = value
            with self.subTest(key=key):
                self.assertFalse(lifecycle.complete_stdout_capture(changed, 4000))
        changed = deepcopy(good)
        changed["stdout"] += "altered"
        self.assertFalse(lifecycle.complete_stdout_capture(changed, 4000))

    def test_profile_defaults_preserve_identity_and_selected_bounds_are_frozen(self):
        original = normalize_verification_profile({"name": "production"})
        defaults = normalize_verification_profile({"name": "production", "stdout_capture_bytes": 4000,
                                                   "require_complete_stdout": False})
        self.assertEqual(original, defaults)
        self.assertEqual(verification_profile_identity(original, IMAGE), verification_profile_identity(defaults, IMAGE))
        selected = normalize_verification_profile({**original, "stdout_capture_bytes": 2 * 1024 * 1024,
                                                   "require_complete_stdout": True})
        identity = verification_profile_identity(selected, IMAGE)
        self.assertEqual(identity["stdout_capture_bytes"], 2 * 1024 * 1024)
        self.assertTrue(identity["require_complete_stdout"])
        self.assertNotEqual(identity["digest"], verification_profile_identity(original, IMAGE)["digest"])
        for bad in (0, -1, True, 2.5, 2 * 1024 * 1024 + 1):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_verification_profile({**original, "stdout_capture_bytes": bad})
        with self.assertRaises(ValueError):
            normalize_verification_profile({**original, "require_complete_stdout": "yes"})

    def setup_profile(self):
        self.profile = normalize_verification_profile({"name": "restart", "phase": "final",
            "stdout_capture_bytes": 2 * 1024 * 1024, "require_complete_stdout": True})
        reference = [{"path": "reference.py", "content": "private reference"}]
        self.candidate = ProjectArtifact(summary="Candidate", files=[{"path": "app.py", "content": "value = 2\n"}])
        self.calls = []
        self.flow = SimpleNamespace(state={"run_id": "fresh"}, artifact_type=ProjectArtifact, project_type=ProjectProject,
            bundle={"sandbox": {"image_id": IMAGE}, "build": {"verification_profiles": [self.profile]},
                    "project_reference": {"files": reference, "digest": digest(reference)}},
            output={"plan": {"acceptance_criteria": ["Compute sum"]}},
            validate_suite=Mock(), fixture_problems=Mock(return_value=[]),
            environment={"verification": {"policy": POLICY, "required_checks": ["python-unittest"]}})
        self.suite = validate_self_suite(self.flow, suite_value(), self.candidate)

    def profile_execution(self, state, stage, index, project, suite, *, verification_profile=None):
        self.calls.append(stage)
        receipt = verifier_receipt(passed=True)
        receipt["commands"][0].update(source_unchanged=True,
            source_sha256={item.path: hashlib.sha256(item.content.encode()).hexdigest() for item in project.files})
        if verification_profile is not None:
            identity = verification_profile_identity(verification_profile, IMAGE)
            receipt["sandbox"]["effective_runtime"] = identity
            data = b"complete synthetic companion logs"
            receipt["commands"][0].update(effective_runtime=identity, profile_sources_unchanged=True,
                                          stdout=data.decode(), stdout_capture=capture(data))
        return receipt

    def test_profile_requires_complete_reference_and_candidate_capture_not_just_exit_zero(self):
        for broken_stage, count in ((None, 3), ("_reference_profile", 2), ("_candidate", 3)):
            with self.subTest(stage=broken_stage):
                self.setup_profile()

                def execute(*args, **kwargs):
                    receipt = self.profile_execution(*args, **kwargs)
                    if broken_stage and args[1].endswith(broken_stage):
                        receipt["commands"][0]["stdout_capture"].update(truncated=True, complete=False)
                    return receipt

                self.flow.runtime = SimpleNamespace(_build_execution=execute)
                result = execute_verification_profile(self.flow, self.candidate, self.suite, "whole_profile_0_0", 0, self.profile)
                self.assertEqual(len(self.calls), count)
                self.assertEqual(result["status"], "review_required" if broken_stage else "passed")
                self.assertEqual(result["execution_settled"], broken_stage is None)
                if broken_stage:
                    self.assertIsNone(result["feedback"])

    def test_executor_propagates_only_selected_capture_limit_into_owned_lifecycle(self):
        self.setup_profile()
        with tempfile.TemporaryDirectory() as folder:
            executor = DockerBuildExecutor(Path(folder), SimpleNamespace(image=IMAGE, memory_mb=128, cpus=1, test_timeout_seconds=10))
            run_id = "00000000-0000-0000-0000-000000000001"
            project = executor.materialize(run_id, 0, self.candidate)
            bounded = Mock(return_value={"exit_code": 0, "cleanup_confirmed": True, "timed_out": False,
                                         "cancelled": False, "stdout": "", "stderr": ""})
            executor._bounded_container = bounded
            result = executor.test(run_id, 0, project, ["fixture"], image=IMAGE, verification_profile=self.profile)
            self.assertEqual(bounded.call_args.kwargs["stdout_capture_bytes"], 2 * 1024 * 1024)
            self.assertEqual(result["sandbox"]["effective_runtime"], verification_profile_identity(self.profile, IMAGE))


if __name__ == "__main__":
    unittest.main()
