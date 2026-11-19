"""Check the handoff between OpenCode self-tests and the pinned verifier."""

import json
from collections import Counter
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from orchestrator.project_build import ProjectArtifact, ProjectProject
from orchestrator.store import digest
from orchestrator.whole_project import WholeProjectBuildRun
from orchestrator.whole_project_sandbox import execute_self_test, validate_self_suite


IMAGE = "sha256:" + "a" * 64
POLICY = "synthetic-quality-v1"
TEST_SOURCE = (
    "import unittest\n\n"
    "class SelfCheck(unittest.TestCase):\n"
    "    def test_value(self):\n"
    "        self.assertEqual(2, 1 + 1)\n"
)


def suite_value():
    return {"implementation_files": ["app.py"], "commands": ["python3 -m unittest discover -s tests -v"],
            "files": [{"path": "tests/test_self.py", "content": TEST_SOURCE}], "assumptions": [],
            "coverage": [{"criterion": 1, "test_file": "tests/test_self.py", "test_name": "test_value",
                          "scenario": "Add two values", "expected_result": "Two", "catches": "Wrong result"}]}


def verifier_receipt(*, passed):
    status, exit_code = ("passed", 0) if passed else ("failed", 1)
    log = ("test_value (test_self.SelfCheck.test_value) ... ok\n\nRan 1 test in 0.0s\n\nOK\n"
           if passed else "AssertionError: wrong result\n")
    summary = {"policy": POLICY, "status": status, "exit_code": exit_code, "cleanup_confirmed": True,
               "checks": {"python-unittest": {"status": status}}}
    return {"status": status,
            "commands": [{"exit_code": exit_code, "cleanup_confirmed": True, "cancelled": False,
                          "timed_out": False, "stderr": log + "OFFLINE_PROJECT_RESULT " + json.dumps(summary),
                          "stdout": ""}],
            "sandbox": {"image_id": IMAGE, "network": "disabled", "read_only_root": True,
                        "read_only_project": True}}


class WholeProjectSandboxTests(unittest.TestCase):
    def setUp(self):
        self.workflow = SimpleNamespace(
            bundle={"sandbox": {"image_id": IMAGE}}, state={"run_id": "trial"}, stage=None,
            environment={"verification": {"policy": POLICY, "required_checks": ["python-unittest"]}},
            output={"plan": {"acceptance_criteria": ["Compute the sum"]}},
            runtime=SimpleNamespace(_build_execution=Mock()), validate_suite=Mock(),
            fixture_problems=Mock(return_value=[]),
            artifact_type=ProjectArtifact, project_type=ProjectProject,
        )
        self.candidate = ProjectArtifact(summary="Application", files=[{"path": "app.py", "content": "value = 2\n"}])

    def test_validates_test_environment_and_preserves_executable_source(self):
        suite = validate_self_suite(self.workflow, suite_value(), self.candidate)
        self.assertEqual(suite.coverage[0].test_name, "SelfCheck.test_value")
        self.assertEqual(suite.files[0].content, TEST_SOURCE)
        self.workflow.validate_suite.assert_called_once_with(suite)
        self.workflow.fixture_problems.assert_called_once_with(suite)

    def test_rejects_test_manifest_with_a_different_implementation(self):
        raw = {**suite_value(), "implementation_files": ["other.py"]}
        with self.assertRaisesRegex(ValueError, "exact whole-project implementation paths"):
            validate_self_suite(self.workflow, raw, self.candidate)

    def test_smoke_coverage_can_select_a_subset_of_known_criteria(self):
        self.workflow.output["plan"]["acceptance_criteria"].append("Handle negative numbers")
        raw = suite_value()
        suite = validate_self_suite(self.workflow, raw, self.candidate)
        self.assertEqual([entry.criterion for entry in suite.coverage], [1])
        raw["coverage"][0]["criterion"] = 3
        with self.assertRaisesRegex(ValueError, "reference criteria from the approved plan"):
            validate_self_suite(self.workflow, raw, self.candidate)

    def test_slash_delimited_self_test_coverage_keeps_source_and_requires_each_case(self):
        raw = suite_value()
        source = TEST_SOURCE + "\n    def test_other(self):\n        self.assertEqual(3, 1 + 2)\n"
        raw["files"][0]["content"] = source
        raw["coverage"][0]["test_name"] = "test_value / SelfCheck.test_other"
        suite = validate_self_suite(self.workflow, raw, self.candidate)
        self.assertEqual(suite.files[0].content, source)
        self.assertEqual([entry.test_name for entry in suite.coverage], ["SelfCheck.test_value", "SelfCheck.test_other"])
        self.assertEqual([entry.criterion for entry in suite.coverage], [1, 1])
        self.workflow.runtime._build_execution.return_value = verifier_receipt(passed=True)
        result = execute_self_test(self.workflow, self.candidate, suite, "whole_self_verify_0", 8000)
        self.assertEqual(result["status"], "review_required")  # One of the two declared tests did not run.

    def test_slash_delimited_self_test_coverage_rejects_unknown_or_ambiguous_methods(self):
        raw = suite_value()
        raw["files"][0]["content"] += ("\nclass OtherCheck(unittest.TestCase):\n"
                                         "    def test_value(self):\n        self.assertTrue(True)\n")
        for reference in ("test_value / OtherCheck.test_value", "SelfCheck.test_value / test_unknown",
                          "SelfCheck.test_value / SelfCheck.test_value", "SelfCheck.test_value / "):
            with self.subTest(reference=reference):
                raw["coverage"][0]["test_name"] = reference
                with self.assertRaisesRegex(ValueError, "distinct, unambiguous methods"):
                    validate_self_suite(self.workflow, raw, self.candidate)

    def test_final_self_check_uses_the_frozen_suite_and_pinned_receipt(self):
        suite = validate_self_suite(self.workflow, suite_value(), self.candidate)
        receipt = verifier_receipt(passed=True)
        self.workflow.runtime._build_execution.return_value = receipt
        result = execute_self_test(self.workflow, self.candidate, suite, "whole_self_verify_final", 8002)
        state, stage, index, project, executed_suite = self.workflow.runtime._build_execution.call_args.args
        self.assertEqual((state, stage, index, executed_suite), (self.workflow.state, "whole_self_verify_final", 8002, suite))
        self.assertEqual({item.path for item in project.files}, {"app.py", "tests/test_self.py"})
        self.assertEqual(result["project_digest"], digest(project.model_dump()))
        self.assertEqual(result["suite_digest"], digest(suite.model_dump()))
        self.assertEqual(result["status"], "passed")
        self.assertIs(result["execution"], receipt)
        self.assertNotIn("feedback", result)

    def test_feedback_requires_conclusive_failure_in_the_approved_sandbox(self):
        suite = validate_self_suite(self.workflow, suite_value(), self.candidate)
        receipt = verifier_receipt(passed=False)
        self.workflow.runtime._build_execution.return_value = receipt
        result = execute_self_test(self.workflow, self.candidate, suite, "whole_self_verify_0", 8000)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["feedback"]["full_failure_receipt_digest"], digest(receipt))
        self.assertIn("AssertionError", result["feedback"]["commands"][0]["stderr"])
        for key, value in (("image_id", "sha256:" + "b" * 64), ("network", "bridge"),
                           ("read_only_project", False), ("read_only_root", False)):
            with self.subTest(key=key):
                self.workflow.runtime._build_execution.return_value = {
                    **receipt, "sandbox": {**receipt["sandbox"], key: value}}
                result = execute_self_test(self.workflow, self.candidate, suite, "whole_self_verify_0", 8000)
                self.assertEqual(result["status"], "review_required")
                self.assertNotIn("feedback", result)
        receipt["commands"][0]["cleanup_confirmed"] = False
        self.workflow.runtime._build_execution.return_value = receipt
        result = execute_self_test(self.workflow, self.candidate, suite, "whole_self_verify_0", 8000)
        self.assertEqual(result["status"], "review_required")
        self.assertNotIn("feedback", result)

    def prefix_failure(self, *, route="/auth", missing="api/auth/register", reference_passed=True):
        controller = "api/src/main/java/AuthController.java"
        candidate = ProjectArtifact(summary="Application", files=[{"path": controller,
            "content": f'@RestController\n@RequestMapping("{route}")\nclass AuthController {{\n'
                       '    @PostMapping("/register")\n    void register() {}\n}\n'}])
        suite = validate_self_suite(self.workflow, {**suite_value(), "implementation_files": [controller]}, candidate)
        self.workflow.scoped_repairs = True
        self.workflow.environment["api"] = {"path_prefix": "/api", "base_url_kind": "api_root"}
        self.workflow.environment["verification"]["required_checks"].append("trusted-api")
        self.workflow.reference = {"digest": "reference"}
        self.workflow.evidence = {"self_reference_validation": {"status": "passed" if reference_passed else "failed",
            "suite_digest": digest(suite.model_dump()), "reference_digest": "reference", "receipt_digest": "f" * 64}}
        receipt = verifier_receipt(passed=False)
        summary = {"policy": POLICY, "status": "failed", "exit_code": 1, "cleanup_confirmed": True,
                   "checks": {name: {"status": status} for name, status in (
                       ("python-unittest", "failed"), ("trusted-api", "failed"), ("trusted-api-startup", "passed"))},
                   "failures": [
                       {"stage": "trusted-api", "status": "failed", "api_context": [
                           f"org.springframework.web.servlet.resource.NoResourceFoundException: No static resource {missing}."]},
                       {"stage": "python-unittest", "status": "failed", "tests_run": 0,
                        "test_failures_complete": True, "omitted_test_failures": 0,
                        "test_failures": [{"test": "setUpClass (test_self.SelfCheck)", "status": "error"}]}]}
        receipt["commands"][0]["stderr"] = "OFFLINE_PROJECT_RESULT " + json.dumps(summary)
        self.workflow.runtime._build_execution.return_value = receipt
        return candidate, suite, receipt

    def test_reference_qualified_missing_api_prefix_dispatches_a_repair_and_reruns_frozen_smoke(self):
        candidate, suite, receipt = self.prefix_failure()
        result = execute_self_test(self.workflow, candidate, suite, "whole_self_verify_0", 8000)
        self.assertEqual(result["status"], "failed")
        target = result["repair_target"]
        self.assertEqual((target["requested_path"], target["unprefixed_path"]), ("/api/auth/register", "/auth/register"))
        self.assertEqual(target["source_paths"], [candidate.files[0].path])
        self.assertEqual(target["receipt_digest"], digest(receipt))
        self.assertEqual(target["reference_receipt_digest"], "f" * 64)

        repaired = candidate.model_copy(update={"summary": "Repaired"})
        flow = object.__new__(WholeProjectBuildRun)
        flow.state, flow.runtime, flow.output = {"run_id": "run"}, SimpleNamespace(store=Mock()), {}
        flow.counters, flow.evidence, flow.stages = Counter(), {"self_checks": []}, {}
        flow.self_suite, flow.self_repair_role = suite, "repairer"
        flow._save_self_candidate, flow.repair_time_available = Mock(), Mock(return_value=True)
        flow._patch = Mock(return_value=repaired)
        flow._self_check = Mock(side_effect=[result, {"status": "passed"}])
        self.assertIs(flow._verify_self_suite(candidate), repaired)
        self.assertEqual(flow._patch.call_args.args, ("whole_self_repair", "repairer", candidate, suite,
                                                    {"failed_execution": result["feedback"], "repair_target": target}))
        self.assertEqual(flow._self_check.call_args.args, (repaired, "whole_self_verify_1", 8001))

    def test_prefix_repair_requires_reference_match_and_current_route_evidence(self):
        for options in ({"reference_passed": False}, {"route": "/api/auth"}, {"missing": "other/auth/register"}, {}):
            with self.subTest(options=options):
                self.setUp()
                candidate, suite, _receipt = self.prefix_failure(**options)
                if not options:
                    self.workflow.evidence["self_reference_validation"]["suite_digest"] = "stale"
                result = execute_self_test(self.workflow, candidate, suite, "whole_self_verify_0", 8000)
                self.assertEqual(result["status"], "review_required")
                self.assertIn("fixture_failure", result)
                self.assertNotIn("repair_target", result)


if __name__ == "__main__":
    unittest.main()
