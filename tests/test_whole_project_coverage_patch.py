"""Coverage deltas cannot rewrite a manifest or bypass executable reference validation."""

from copy import deepcopy
from collections import Counter
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from orchestrator.build_workflow import BuildRun
from orchestrator.project_build import ProjectArtifact, ProjectProject, ProjectTestSuite
from orchestrator.project_test_lint import defects
from orchestrator.store import digest
from orchestrator.whole_project_test_author import (
    COVERAGE_PATCH_POLICY, IndependentCoveragePatch, compose_coverage_patch,
    coverage_index_diagnostics, coverage_patch_inputs, coverage_patch_type,
)
from test_whole_project_test_author import CRITERIA, correction, correction_patch, draft, flow


class CoveragePatchTests(unittest.TestCase):
    def test_agent_correction_retains_bound_coverage_schema_and_author_authority(self):
        from orchestrator.agent_recovery import INLINE_RECOVERY_POLICY
        from orchestrator.stage_recovery import KEY, DYNAMIC_POLICY, StageRecovery
        workflow = flow(draft())
        workflow.brief = {'prompt':'Public contract'}
        workflow.runtime = SimpleNamespace(whole_project_prompt_tokenizers={})
        workflow.bundle.update({KEY:deepcopy(DYNAMIC_POLICY),
            'whole_project_repair_policy':'verified-scoped-repair-v1',
            'generation':{'test_author':{'context':98304}}})
        workflow.counters, workflow.evidence = Counter(), {}
        workflow.recovery = StageRecovery(workflow)
        inputs = coverage_patch_inputs(ProjectTestSuite.model_validate(draft()),CRITERIA,
            byte_limit=16000,file_limit=15)
        response_type = coverage_patch_type(inputs['coverage_validation'])
        workflow._opencode = Mock(return_value={'value':correction_patch(draft())})
        workflow._agent_output_authority = {'stage':'whole_recovery_1_output','role':'test_author',
            'schema_digest':digest(response_type.model_json_schema())}
        revised = workflow._generate('whole_recovery_1_output','test_author',response_type,
            'Correct the rejected patch',inputs,{},tool_policy=INLINE_RECOVERY_POLICY)
        self.assertEqual(compose_coverage_patch(ProjectTestSuite.model_validate(draft()),revised,CRITERIA).model_dump(),
            correction(draft()))
        workflow._opencode.assert_called_once()
        with self.assertRaisesRegex(ValueError,'original schema'):
            workflow._generate('whole_recovery_1_output','implementer',response_type,
                'Correct',inputs,{},tool_policy=INLINE_RECOVERY_POLICY)

    def test_manifest_drift_cannot_be_emitted_or_accepted(self):
        value = draft()
        value["assumptions"] = ["CORS, and jsdom setup."]
        original = ProjectTestSuite.model_validate(value)
        delta = correction_patch(value)
        revised = compose_coverage_patch(original, delta, CRITERIA)
        self.assertEqual(revised.model_dump(), correction(value))
        self.assertEqual(original.model_dump(), value)
        for key, replacement in (("assumptions", ["CORS, jsdom setup."]),
                                 ("commands", value["commands"]), ("implementation_files", ["changed.py"]),
                                 ("summary", "Unrequested metadata")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                compose_coverage_patch(original, {**delta, key: replacement}, CRITERIA)
        with self.assertRaises(ValueError):
            compose_coverage_patch(original, correction(value), CRITERIA)

    def test_exact_remaps_and_new_test_scope_are_enforced(self):
        original = ProjectTestSuite.model_validate(draft())
        candidates = []
        for field, value in (("coverage_index", 13), ("coverage_index", 0),
                             ("from_criterion", 12), ("to_criterion", 2), ("to_criterion", 13)):
            delta = correction_patch(draft()); delta["remaps"][0][field] = value; candidates.append(delta)
        delta = correction_patch(draft()); delta["remaps"] = []; candidates.append(delta)
        delta = correction_patch(draft()); delta["remaps"] *= 2; candidates.append(delta)
        delta = correction_patch(draft()); delta["files"].append(draft()["files"][0]); candidates.append(delta)
        delta = correction_patch(draft()); delta["files"][0]["path"] = "tests/../app.py"; candidates.append(delta)
        delta = correction_patch(draft()); delta["coverage"][0]["criterion"] = 3; candidates.append(delta)
        delta = correction_patch(draft()); delta["coverage"][0]["test_name"] = "Security.test_absent"; candidates.append(delta)
        delta = correction_patch(draft()); delta["coverage"][0]["test_file"] = "tests/test_existing.py"; candidates.append(delta)
        delta = correction_patch(draft()); delta["files"][0]["content"] += "#" * 20000; candidates.append(delta)
        for index, value in enumerate(candidates):
            with self.subTest(index=index), self.assertRaises(ValueError):
                compose_coverage_patch(original, value, CRITERIA)

    def test_bound_schema_distinguishes_zero_based_rows_and_one_based_criteria(self):
        original = ProjectTestSuite.model_validate(draft())
        response_type = coverage_patch_type(coverage_index_diagnostics(original, CRITERIA))
        response_type.model_validate(correction_patch(draft()))
        for field, value in (("coverage_index", 13), ("coverage_index", 12),
                             ("from_criterion", 11), ("to_criterion", 2)):
            delta = correction_patch(draft()); delta["remaps"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                response_type.model_validate(delta)
        unknown_only = correction(draft())
        unknown_only["coverage"][-2]["criterion"] = 13
        revised = compose_coverage_patch(ProjectTestSuite.model_validate(unknown_only),
            {"files": [], "coverage": [], "remaps": [{"coverage_index": 11, "from_criterion": 13, "to_criterion": 12}]}, CRITERIA)
        self.assertEqual(revised.model_dump(), correction(draft()))
        missing_only = draft(); missing_only["coverage"][-1]["criterion"] = 12
        previous = ProjectTestSuite.model_validate(missing_only)
        delta = correction_patch(draft()); delta["remaps"] = []
        coverage_patch_type(coverage_index_diagnostics(previous, CRITERIA)).model_validate(delta)
        compose_coverage_patch(previous, delta, CRITERIA)

    def test_patch_request_is_stage_and_schema_bound_with_read_only_context(self):
        workflow = flow(draft())
        workflow.brief = {"prompt": "Public contract"}
        workflow.runtime = SimpleNamespace(whole_project_prompt_tokenizers={})
        workflow.bundle["generation"] = {"test_author": {"context": 65536}}
        workflow._opencode = Mock(return_value={"value": correction_patch(draft())})
        inputs = coverage_patch_inputs(ProjectTestSuite.model_validate(draft()), CRITERIA,
                                       byte_limit=16000, file_limit=15)
        response_type = coverage_patch_type(inputs["coverage_validation"])
        for stage, role, model_type, visible in (("build_tests", "test_author", response_type, {}),
                ("build_tests_retry", "implementer", response_type, {}),
                ("build_tests_retry", "test_author", ProjectTestSuite, {}),
                ("build_tests_retry", "test_author", response_type, {"app.py": "private"}),
                ("build_tests_retry", "test_author", IndependentCoveragePatch, {})):
            with self.subTest(stage=stage, role=role, model_type=model_type), self.assertRaises(ValueError):
                workflow._generate(stage, role, model_type, "Patch coverage", inputs, visible)
        workflow._opencode.assert_not_called()
        with patch("orchestrator.whole_project.validate_project_prompt", return_value=100):
            workflow._generate("build_tests_retry", "test_author", response_type, "Patch coverage", inputs, {})
        sent = json.loads(workflow._opencode.call_args.kwargs["payload"]["messages"][1]["content"])
        self.assertEqual(sent["coverage_patch_policy"], COVERAGE_PATCH_POLICY)
        self.assertEqual(sent["previous_suite_digest"], digest(draft()))
        self.assertEqual(sent["existing_files"], draft()["files"])
        self.assertNotIn("previous_suite", sent)
        self.assertEqual(len(sent["numbered_acceptance_criteria"]), 12)

    def test_composed_suite_still_requires_named_reference_failures_to_be_repaired(self):
        workflow = flow(draft(), retained=True)
        suite = workflow._author_independent_suite(["app.py"])
        self.assertIn("assertIn(failure.exception.code", suite.files[-1].content)
        self.assertEqual(defects(SimpleNamespace(files=[suite.files[-1]])), [])
        weak = suite.files[-1].model_copy(update={"content": "import unittest\nclass Security(unittest.TestCase):\n    def test_unauthorized(self):\n        pass\n"})
        self.assertTrue(defects(SimpleNamespace(files=[weak])))
        workflow.artifact_type, workflow.project_type = ProjectArtifact, ProjectProject
        workflow.unverified_criteria = frozenset()
        workflow.repair_invalid_test_references = True
        workflow.output["verification"] = {}
        workflow.fixture_problems = Mock(return_value=[])
        workflow.validate_suite = Mock()
        workflow.test_defects = Mock(return_value=[])
        failure = {"status": "failed", "commands": [{"exit_code": 1, "cleanup_confirmed": True,
            "stdout": "", "stderr": "FAIL: test_unauthorized (test_security.Security)\nAssertionError: public contract mismatch\nRan 13 tests in 0.1s\nFAILED (failures=1)\n"}]}
        lines = [f"{entry.test_name.split('.')[1]} ({entry.test_file[6:-3]}.{entry.test_name.split('.')[0]}) ... ok"
                 for entry in suite.coverage]
        passed = {"status": "passed", "commands": [{"exit_code": 0, "cleanup_confirmed": True,
            "stdout": "", "stderr": "\n".join(lines) + "\nRan 13 tests in 0.1s\nOK\n"}]}
        def control(_suite):
            self.assertNotIn("build_frozen", workflow.stages)
            return failure if workflow.control.call_count == 1 else passed
        workflow.control = Mock(side_effect=control)
        workflow.repair_reference_failures = Mock(return_value=suite)
        result = BuildRun.check_suite(workflow, suite)
        self.assertEqual(workflow.control.call_count, 2)
        problems = workflow.repair_reference_failures.call_args.args[1]
        self.assertEqual(problems[0]["names"], ["Security.test_unauthorized"])
        self.assertEqual(result.model_dump(), suite.model_dump())
        self.assertEqual(workflow.stages["build_frozen"]["trusted_control"], "passed")


if __name__ == "__main__":
    unittest.main()
