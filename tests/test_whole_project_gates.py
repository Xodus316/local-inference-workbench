"""Pure boundary checks for finite, reference-first candidate/final gates."""

from collections import Counter
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from orchestrator.project_build import ProjectArtifact, ProjectProject, ProjectTestSuite
from orchestrator.sandbox_profiles import normalize_verification_profile, verification_profile_identity
from orchestrator.store import digest
from orchestrator import whole_project_gates as gates
from orchestrator.whole_project_sandbox import validate_self_suite
from tests.test_whole_project_sandbox import IMAGE, POLICY, suite_value, verifier_receipt


class WholeProjectGateTests(unittest.TestCase):
    def setUp(self):
        self.candidate = ProjectArtifact(summary="Candidate", files=[{"path": "app.py", "content": "value = 2\n"}])
        self.revised = ProjectArtifact(summary="Candidate", files=[{"path": "app.py", "content": "value = 1 + 1\n"}])
        self.profile = normalize_verification_profile({"name": "production", "phase": "candidate",
                                                      "environment": {"NODE_ENV": "production"}})
        reference = [{"path": "app.py", "content": "PRIVATE REFERENCE SOURCE"}]
        self.snapshots, self.executions = [], []
        self.flow = SimpleNamespace(
            state={"run_id": "fresh-run"}, stage="build_verify_0", counters=Counter(), evidence={}, stages={},
            bundle={"whole_project_gate_policy": gates.POLICY, "sandbox": {"image_id": IMAGE},
                    "build": {"verification_profiles": [self.profile]},
                    "project_reference": {"files": reference, "digest": digest(reference)}},
            limits={"max_repair_attempts": 2},
            environment={"verification": {"policy": POLICY, "required_checks": ["python-unittest"]}},
            output={"repair_attempts": 0, "plan": {"implementation_files": ["app.py"], "acceptance_criteria": ["Compute sum"]}},
            artifact_type=ProjectArtifact, project_type=ProjectProject, suite_type=ProjectTestSuite,
            validate_suite=Mock(), fixture_problems=Mock(return_value=[]), repair_time_available=Mock(return_value=True),
        )
        self.flow.runtime = SimpleNamespace(
            _build_execution=self.execute,
            store=SimpleNamespace(snapshot_result=lambda run_id, value: self.snapshots.append(deepcopy(value))),
        )
        self.suite = validate_self_suite(self.flow, suite_value(), self.candidate)
        self.flow.output["suite"] = self.suite.model_dump()
        self.flow._patch = Mock(return_value=self.revised)

        def bounded(label, limit):
            self.flow.counters[label] += 1
            if self.flow.counters[label] > self.flow.limits[limit]:
                raise ValueError("exhausted")
            return f"{label}_{self.flow.counters[label]}"

        self.flow.bounded = bounded

    def execute(self, state, stage, index, project, suite, *, verification_profile=None):
        self.executions.append((stage, index, project.model_dump(), suite.model_dump(), verification_profile))
        receipt = verifier_receipt(passed=True)
        receipt["commands"][0].update(
            source_unchanged=True,
            source_sha256={item.path: hashlib.sha256(item.content.encode()).hexdigest() for item in project.files},
        )
        if verification_profile is not None:
            identity = verification_profile_identity(verification_profile, IMAGE)
            receipt["sandbox"]["effective_runtime"] = identity
            receipt["commands"][0].update(effective_runtime=identity, profile_sources_unchanged=True)
        return receipt

    def failing(self, suffix, *, cleanup=True):
        def execute(*args, **kwargs):
            receipt = self.execute(*args, **kwargs)
            if args[1].endswith(suffix):
                failed = verifier_receipt(passed=False)
                receipt["status"] = "failed"
                receipt["commands"][0].update(failed["commands"][0])
                receipt["commands"][0]["cleanup_confirmed"] = cleanup
            return receipt
        return execute

    def candidate_gates(self, artifact=None):
        return gates.candidate_gates(self.flow, artifact or self.candidate, self.suite, verifier_receipt(passed=True))

    def test_every_source_gets_fresh_reference_barriers_and_candidate_execution(self):
        self.assertIs(self.candidate_gates(), self.candidate)
        self.assertIs(self.candidate_gates(self.revised), self.revised)
        self.assertEqual(len(self.executions), 6)
        for start in (0, 3):
            self.assertTrue(self.executions[start][0].endswith("_reference_ordinary"))
            self.assertTrue(self.executions[start + 1][0].endswith("_reference_profile"))
            self.assertTrue(self.executions[start + 2][0].endswith("_candidate"))
        self.assertNotEqual(self.executions[2][2], self.executions[5][2])
        self.assertEqual(len({x[1] for x in self.executions}), 6)
        records = self.flow.evidence["verification_profile_checks"]["production"]
        self.assertEqual([r["artifact_digest"] for r in records],
                         [digest(self.candidate.model_dump()), digest(self.revised.model_dump())])
        self.flow._patch.assert_not_called()

    def test_each_reference_failure_blocks_candidate_and_author_without_feedback_leak(self):
        for suffix, count in (("_reference_ordinary", 1), ("_reference_profile", 2)):
            with self.subTest(suffix=suffix):
                self.setUp()
                self.flow.runtime._build_execution = self.failing(suffix)
                with self.assertRaisesRegex(gates.VerificationGateStop, "inconclusive"):
                    self.candidate_gates()
                self.assertEqual(len(self.executions), count)
                self.flow._patch.assert_not_called()
                result = self.flow.evidence["verification_profile_checks"]["production"][0]
                self.assertIsNone(result["candidate"])
                self.assertIsNone(result["feedback"])

    def test_candidate_failure_charges_shared_budget_before_native_dispatch(self):
        self.flow.output["repair_attempts"] = 1
        self.flow.counters["repair"] = 1
        self.flow.runtime._build_execution = self.failing("_candidate")

        def authored(*args, **kwargs):
            self.assertEqual(args[:2], ("repair_2", "repairer"))
            self.assertEqual(self.snapshots[-1]["output"]["repair_attempts"], 2)
            self.assertNotIn("PRIVATE REFERENCE SOURCE", json.dumps(args[4]))
            return self.revised

        self.flow._patch.side_effect = authored
        self.assertIs(self.candidate_gates(), self.revised)
        self.assertEqual(self.flow.output["repair_attempts"], 2)
        self.assertEqual(self.flow.output["artifact"], self.revised.model_dump())
        self.assertEqual(self.flow.evidence["additional_gate_repairs"][0]["predecessor_digest"], digest(self.candidate.model_dump()))
        with self.assertRaisesRegex(gates.VerificationGateStop, "budget is exhausted"):
            self.candidate_gates(self.revised)
        self.assertEqual(self.flow._patch.call_count, 1)

    def test_unknown_cleanup_and_execution_exceptions_never_authorize_repair(self):
        self.flow.runtime._build_execution = self.failing("_candidate", cleanup=False)
        with self.assertRaisesRegex(gates.VerificationGateStop, "inconclusive"):
            self.candidate_gates()
        self.flow._patch.assert_not_called()
        self.assertEqual(self.flow.output["repair_attempts"], 0)
        self.flow.runtime._build_execution = Mock(side_effect=OSError("execution unknown"))
        with self.assertRaisesRegex(OSError, "unknown"):
            self.candidate_gates()
        self.flow._patch.assert_not_called()

    def test_foreign_source_runtime_or_run_pass_is_rejected(self):
        identity = verification_profile_identity(self.profile, IMAGE)
        for key, bad in (("artifact_digest", "old-source"), ("image_id", "old-image"),
                         ("effective_runtime", {}), ("run_id", "old-run")):
            result = {"status": "passed", "execution_settled": True, "artifact_digest": digest(self.candidate.model_dump()),
                      "image_id": IMAGE, "effective_runtime": identity, "run_id": self.flow.state["run_id"]}
            result[key] = bad
            with self.subTest(key=key), patch("orchestrator.whole_project_sandbox.execute_verification_profile", return_value=result):
                with self.assertRaisesRegex(gates.VerificationGateStop, "does not bind"):
                    self.candidate_gates()
            self.flow._patch.assert_not_called()

    def test_source_digest_mismatch_in_actual_execution_cannot_pass(self):
        def mismatched(*args, **kwargs):
            result = self.execute(*args, **kwargs)
            result["commands"][0]["source_sha256"]["app.py"] = "f" * 64
            return result
        self.flow.runtime._build_execution = mismatched
        with self.assertRaisesRegex(gates.VerificationGateStop, "inconclusive"):
            self.candidate_gates()
        self.assertEqual(len(self.executions), 1)
        self.flow._patch.assert_not_called()

    def test_final_profile_repair_returns_changed_source_for_full_loop_reverification(self):
        profile = normalize_verification_profile({**self.profile, "phase": "final"})
        self.flow.bundle["build"]["verification_profiles"] = [profile]
        self.flow.runtime._build_execution = self.failing("_candidate")
        ready, candidate = gates.final_gates(self.flow, self.candidate)
        self.assertFalse(ready)
        self.assertIs(candidate, self.revised)
        self.assertEqual(self.flow._patch.call_count, 1)

    def test_missing_supplemental_cases_cannot_authorize_repair_or_pass(self):
        self.flow.bundle["whole_project_regression_suite"] = {"fixture": "bound"}
        result = {"status": "passed", "execution": verifier_receipt(passed=True), "feedback": {}}
        with patch.object(gates, "validate_regression_suite", return_value=self.suite), \
                patch.object(gates, "execute_self_test", return_value=result), \
                patch.object(gates, "regression_cases_passed", return_value=False):
            with self.assertRaisesRegex(gates.VerificationGateStop, "inconclusive"):
                self.candidate_gates()
        self.flow._patch.assert_not_called()
        self.assertEqual(self.executions, [])

    def test_noop_failed_compilation_and_unknown_author_consume_but_never_refund(self):
        failed = {"status": "failed", "execution_settled": True, "feedback": {"error": "candidate failed"}}
        for response in (None, self.candidate, RuntimeError("native completion unknown")):
            with self.subTest(response=response):
                self.setUp()
                self.flow._patch.side_effect = response if isinstance(response, BaseException) else None
                self.flow._patch.return_value = response
                with self.assertRaises((gates.VerificationGateStop, RuntimeError)):
                    gates.repair(self.flow, self.candidate, self.suite, failed, "production")
                self.assertEqual(self.flow.output["repair_attempts"], 1)
                self.assertEqual(self.snapshots[-1]["output"]["repair_attempts"], 1)

    def test_execution_and_time_limits_prevent_unbounded_gate_retries(self):
        self.flow.evidence["verification_profile_checks"] = {"production": [{}, {}, {}]}
        with self.assertRaisesRegex(gates.VerificationGateStop, "execution budget"):
            self.candidate_gates()
        self.assertEqual(self.executions, [])
        self.flow.evidence.clear()
        self.flow.repair_time_available.return_value = False
        self.flow.runtime._build_execution = self.failing("_candidate")
        with self.assertRaisesRegex(gates.VerificationGateStop, "budget is exhausted"):
            self.candidate_gates()
        self.flow._patch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
