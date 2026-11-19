"""Verifier profiles preserve bytes, reference barriers and companion identity."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from orchestrator.build_sandbox import DockerBuildExecutor
from orchestrator.project_build import ProjectArtifact, ProjectProject
from orchestrator.sandbox_profiles import normalize_verification_profile, verification_profile_identity
from orchestrator.store import digest
from orchestrator.whole_project_sandbox import execute_verification_profile, validate_self_suite
from tests import test_whole_project_sandbox as fixtures

IMAGE, POLICY = fixtures.IMAGE, fixtures.POLICY
verifier_receipt, suite_value = fixtures.verifier_receipt, fixtures.suite_value


class VerificationProfiles(unittest.TestCase):
    def setUp(self):
        base = fixtures.WholeProjectSandboxTests()
        base.setUp()
        self.workflow = base.workflow
        self.workflow.state = {"run_id": "run"}
        self.candidate = base.candidate
        self.suite = validate_self_suite(self.workflow, suite_value(), self.candidate)
        self.profile = normalize_verification_profile({"name": "production", "phase": "candidate",
                                                      "environment": {"NODE_ENV": "production"}})
        files = [{"path": "app.py", "content": "value = 1 + 1  # private reference\n"}]
        self.workflow.bundle.update(project_reference={"files": files, "digest": digest(files)},
            build={"verification_profiles": [self.profile]})
        self.calls = []

    def execution(self, state, stage, index, project, suite, *, verification_profile=None):
        self.calls.append((stage, index, project.model_dump(), verification_profile))
        receipt = verifier_receipt(passed=True)
        receipt["commands"][0].update(source_unchanged=True,
            source_sha256={item.path: hashlib.sha256(item.content.encode()).hexdigest() for item in project.files})
        if verification_profile is not None:
            effective = verification_profile_identity(verification_profile, IMAGE)
            receipt["sandbox"]["effective_runtime"] = effective
            receipt["commands"][0].update(effective_runtime=effective, profile_sources_unchanged=True)
        return receipt

    def run_profile(self, callback=None):
        self.workflow.runtime._build_execution = callback or self.execution
        return execute_verification_profile(self.workflow, self.candidate, self.suite,
                                            "whole_profile_0_0", 8600, self.profile)

    def test_reference_controls_and_candidate_are_distinct_same_source_bound_calls(self):
        result = self.run_profile()
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["execution_settled"])
        self.assertEqual([x[1] for x in self.calls], [25800, 25801, 25802])
        self.assertEqual([x[0].rsplit("_", 1)[-1] for x in self.calls], ["ordinary", "profile", "candidate"])
        self.assertIn("private reference", self.calls[0][2]["files"][0]["content"])
        self.assertNotIn("private reference", self.calls[-1][2]["files"][0]["content"])
        self.assertEqual(result["artifact_digest"], digest(self.candidate.model_dump()))

    def test_failed_reference_never_dispatches_candidate_or_leaks_feedback(self):
        def fail(*args, **kwargs):
            receipt = self.execution(*args, **kwargs)
            bad = verifier_receipt(passed=False)
            receipt["status"] = "failed"
            receipt["commands"][0].update({k: v for k, v in bad["commands"][0].items()})
            return receipt
        result = self.run_profile(fail)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["status"], "review_required")
        self.assertIsNone(result["candidate"])
        self.assertIsNone(result["feedback"])

    def test_different_reference_layout_is_bound_independently(self):
        files = [{"path": "trusted/reference.py", "content": "value = 2\n"}]
        self.workflow.bundle["project_reference"] = {"files": files, "digest": digest(files)}
        result = self.run_profile()
        self.assertEqual(result["status"], "passed")
        self.assertIn("trusted/reference.py", result["reference_profile"]["source_sha256"])
        self.assertNotIn("trusted/reference.py", result["candidate"]["source_sha256"])
        self.assertIn("app.py", result["candidate"]["source_sha256"])
        self.workflow.bundle["project_reference"]["files"][0]["content"] = "tampered\n"
        with self.assertRaisesRegex(ValueError, "exact approved reference"):
            self.run_profile()

    def test_settled_candidate_failure_has_only_candidate_feedback(self):
        def fail_candidate(*args, **kwargs):
            receipt = self.execution(*args, **kwargs)
            if args[1].endswith("_candidate"):
                bad = verifier_receipt(passed=False)
                receipt["status"] = "failed"
                receipt["commands"][0].update(bad["commands"][0])
            return receipt
        result = self.run_profile(fail_candidate)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("private reference", json.dumps(result["feedback"]))

    def test_missing_identity_source_or_cleanup_is_inconclusive(self):
        for mutation in (lambda r: r["sandbox"].pop("effective_runtime"),
                         lambda r: r["commands"][0].update(source_unchanged=False),
                         lambda r: r["commands"][0].update(cleanup_confirmed=False)):
            with self.subTest(mutation=mutation):
                self.calls = []
                def wrong(*args, **kwargs):
                    receipt = self.execution(*args, **kwargs)
                    if kwargs.get("verification_profile") is not None:
                        mutation(receipt)
                    return receipt
                result = self.run_profile(wrong)
                self.assertEqual(result["status"], "review_required")
                self.assertEqual(len(self.calls), 2)
                self.assertFalse(result["execution_settled"])

    def test_companion_requires_exact_marker_checks_not_test_count_credit(self):
        self.profile = normalize_verification_profile({"name": "restart", "commands": ["trusted-restart-probe"],
            "verification": {"policy": "restart-fixture-v1", "required_checks": ["restarted", "records"],
                             "marker": "RESTART_RESULT"}})
        self.workflow.bundle["build"]["verification_profiles"] = [self.profile]
        def companion(*args, **kwargs):
            receipt = self.execution(*args, **kwargs)
            if kwargs.get("verification_profile") is not None:
                summary = {"policy": "restart-fixture-v1", "status": "passed", "exit_code": 0,
                           "cleanup_confirmed": True, "checks": {key: {"status": "passed"}
                                                                  for key in ("restarted", "records")}}
                receipt["commands"][0]["stderr"] = "RESTART_RESULT " + json.dumps(summary)
            return receipt
        result = self.run_profile(companion)
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["acceptance_cases_executed_in_profile"])

    def test_profile_source_tamper_reserved_mounts_and_phase_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "bootstrap.js"
            file.write_text("// trusted verifier\n", encoding="utf-8")
            profile = {"name": "production", "readonly_files": [{"source": str(file),
                "target": "/opt/verifier/bootstrap.js", "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}]}
            bound = normalize_verification_profile(profile)
            self.assertEqual(bound["phase"], "final")
            for target in ("/workspace/app.py", "/proc/1", "/tmp/bootstrap.js"):
                with self.subTest(target=target), self.assertRaises(ValueError):
                    normalize_verification_profile({**profile, "readonly_files": [{**profile["readonly_files"][0], "target": target}]})
            with self.assertRaises(ValueError):
                normalize_verification_profile({**profile, "phase": "other"})
            file.write_text("// changed\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source changed"):
                normalize_verification_profile(bound)

    def test_executor_mounts_only_snapshot_and_records_profile_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            file = root / "bootstrap.js"
            file.write_bytes(b"// tooling\n")
            profile = normalize_verification_profile({"name": "production", "environment": {"NODE_ENV": "production"},
                "entrypoint": "/usr/bin/tini",
                "readonly_files": [{"source": str(file), "target": "/opt/bootstrap.js",
                                    "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}]})
            config = SimpleNamespace(image=IMAGE, memory_mb=128, cpus=1, test_timeout_seconds=10)
            executor = DockerBuildExecutor(root / "data", config)
            project = executor.materialize("00000000-0000-0000-0000-000000000001", 0, self.candidate)
            def bounded(args, *unused, **options):
                mounts = [args[i + 1] for i, part in enumerate(args) if part == "--mount"]
                overlay = next(row for row in mounts if "target=/opt/bootstrap.js" in row)
                self.assertNotIn(str(file), overlay)
                self.assertTrue(overlay.endswith(",readonly"))
                self.assertIn("NODE_ENV=production", args)
                self.assertEqual(args[args.index("--entrypoint") + 1], "/usr/bin/tini")
                self.assertLess(args.index("--entrypoint"), args.index(IMAGE))
                self.assertEqual(options["image_id"], IMAGE)
                return {"exit_code": 0, "cleanup_confirmed": True, "timed_out": False,
                        "cancelled": False, "stdout": "", "stderr": ""}
            executor._bounded_container = bounded
            result = executor.test("00000000-0000-0000-0000-000000000001", 0, project,
                                   ["run-tests"], image=IMAGE, verification_profile=profile)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["sandbox"]["effective_runtime"], verification_profile_identity(profile, IMAGE))

    def test_entrypoint_cannot_be_application_or_control_path(self):
        for entrypoint in ("relative", "/workspace/main.py", "/proc/self/exe", "/tmp/start", "/usr/bin/../bin/tini"):
            with self.subTest(entrypoint=entrypoint), self.assertRaises(ValueError):
                normalize_verification_profile({"name": "restart", "entrypoint": entrypoint})


if __name__ == "__main__":
    unittest.main()
