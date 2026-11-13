"""Invalid independent additions are corrected within the existing call budget."""
import copy
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from orchestrator.build_models import ReviewedBuildTestSuite
from orchestrator.build_workflow import BuildRun
from orchestrator.config import Config
from orchestrator.project_build import REVIEWED_POLICY, offline_project_contract
from orchestrator.runtime import Runtime
from test_project_build import BRIEF, COMMAND, COVERAGE, TEST, Executor, Models, POLICY


UI_TEST = {"path": "tests/test_ui.py", "content": (
    "import unittest\nclass UI(unittest.TestCase):\n"
    "    def test_route(self):\n        self.assertEqual('protected', 'protected')\n")}
UI_COVERAGE = {"criterion": 2, "test_file": UI_TEST["path"], "test_name": "UI.test_route",
               "scenario": "Navigate without credentials", "expected_result": "Login route",
               "catches": "Missing authentication guard"}
GOOD = {"files": [UI_TEST], "coverage": [UI_COVERAGE]}
BROKEN_UI = {**UI_TEST, "content": (
    "import unittest\nclass UI(unittest.TestCase):\n"
    "    def test_route(self):\n        script = 'describe(\"route\", () => {\n"
    "            expect(true).toBe(true)\n        })'\n        self.assertTrue(script)\n")}


def suite():
    return ReviewedBuildTestSuite.model_validate({
        "implementation_files": ["api/App.java", "frontend/App.vue"], "files": [TEST],
        "commands": [COMMAND], "coverage": [COVERAGE], "assumptions": []})


def direct_run(responses, budget=2):
    run = object.__new__(BuildRun)
    run.counters, run.stages = Counter(), {}
    run.limits = {"max_test_coverage_repairs": budget, "max_test_strengthening_attempts": budget}
    run.seen = []
    def ask(stage, role, model_type, instruction, **data):
        run.stage = stage
        run.stages[stage] = {"role": role}
        run.seen.append((stage, instruction, copy.deepcopy(data)))
        answer = responses[len(run.seen) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return copy.deepcopy(answer)
    run.ask = ask
    return run


class CoverageModels(Models):
    def __init__(self):
        super().__init__()
        self.backend_source = "correct backend"
        self.first_addition = {"files": [TEST, UI_TEST], "coverage": [COVERAGE, UI_COVERAGE]}
        self.second_addition = GOOD
        self.unknown = False
        self.coverage_admission_failure = False
        self.syntax_file = UI_TEST
        self.syntax_unknown = False

    def admit(self, model, settings, payload, stage):
        super().admit(model, settings, payload, stage)
        if stage == "build_tests_coverage_1" and self.coverage_admission_failure:
            raise ValueError("Synthetic request admission failure")

    def complete(self, *args):
        if self.stage == "build_plan":
            value = {"goal": "Recreate application", "acceptance_criteria": ["API behavior", "UI behavior"],
                     "implementation_steps": ["Implement API and UI"], "test_strategy": ["Black-box tests"],
                     "assumptions": [], "risks": []}
        elif self.stage == "build_tests_coverage_1":
            if self.unknown:
                raise OSError("Synthetic lost native completion")
            value = self.first_addition
        elif self.stage == "build_tests_coverage_2":
            value = self.second_addition
        elif self.stage.startswith("build_tests_syntax_"):
            if self.syntax_unknown:
                raise OSError("Synthetic lost syntax-repair completion")
            value = {"files": [self.syntax_file]}
        else:
            return super().complete(*args)
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}


class CoverageExecutor(Executor):
    def test(self, *args, **kwargs):
        result = super().test(*args, **kwargs)
        row = result["commands"][0]
        row["stderr"] = row["stderr"].replace("Ran 1 test", "test_route (test_ui.UI.test_route) ... ok\nRan 2 tests")
        return result


class AdditionCorrectionTests(unittest.TestCase):
    def test_rejected_whole_suite_is_corrected_without_touching_existing_source_or_refs(self):
        original = suite()
        before = original.model_dump()
        wrong = {"files": [TEST, UI_TEST], "coverage": [COVERAGE, UI_COVERAGE]}
        run = direct_run([wrong, GOOD])
        result = run.additions(original, {2})
        self.assertEqual(original.model_dump(), before)
        self.assertEqual(result.files[0].model_dump(), before["files"][0])
        self.assertEqual(result.coverage[0].model_dump(), before["coverage"][0])
        self.assertEqual(result.commands, original.commands)
        feedback = run.seen[1][2]["addition_feedback"]
        self.assertEqual(feedback["rejected_addition"], wrong)
        self.assertIn("existing_paths=['tests/test_api.py']", feedback["validation_error"])
        self.assertIn("unrequested_criteria=[1]", feedback["validation_error"])
        self.assertEqual(run.seen[1][2]["requested_criteria"], [2])
        self.assertEqual(run.counters["build_tests_coverage"], 2)

    def test_standalone_dom_references_receive_discovery_feedback_before_acceptance(self):
        ts = {"files": [{"path": "tests/ui.test.ts", "content": "describe('route', () => {})"}],
              "coverage": [{**UI_COVERAGE, "test_file": "tests/ui.test.ts", "test_name": "route"}]}
        run = direct_run([ts, GOOD])
        result = run.additions(suite(), {2})
        self.assertIn("Coverage requires discoverable Python", run.seen[1][2]["addition_feedback"]["validation_error"])
        self.assertEqual(result.files[-1].path, UI_TEST["path"])
        self.assertIn("Python unittest wrapper", run.seen[0][1])
        self.assertIn("bounded subprocess timeout", run.seen[0][1])

    def test_missing_method_reference_is_corrected_within_coverage_budget(self):
        bad = {"files": [UI_TEST], "coverage": [{**UI_COVERAGE, "test_name": "UI.test_absent"}]}
        run = direct_run([bad, GOOD])
        self.assertEqual(run.additions(suite(), {2}).files[-1].model_dump(), UI_TEST)
        self.assertIn("addition_feedback", run.seen[1][2])

    def test_new_python_syntax_failure_is_handed_off_without_another_coverage_call(self):
        bad = {"files": [BROKEN_UI], "coverage": [UI_COVERAGE]}
        original = suite()
        before = original.model_dump()
        run = direct_run([bad], budget=1)
        result = run.additions(original, {2})
        self.assertEqual(result.files[-1].model_dump(), BROKEN_UI)
        self.assertEqual(original.model_dump(), before)
        self.assertEqual(len(run.seen), 1)
        diagnostics = run.stages["build_tests_coverage_1"]["syntax_handoff"]["diagnostics"]
        self.assertEqual(diagnostics[0]["path"], "tests/test_ui.py")
        self.assertIn("unterminated string", diagnostics[0]["message"])

    def test_syntax_does_not_bypass_discovery_and_invalid_feedback_retains_diagnostics(self):
        bad = {"files": [BROKEN_UI], "coverage": [{**UI_COVERAGE, "test_name": "UI route"}]}
        run = direct_run([bad, GOOD])
        self.assertEqual(run.additions(suite(), {2}).files[-1].model_dump(), UI_TEST)
        self.assertNotIn("syntax_handoff", run.stages["build_tests_coverage_1"])
        feedback = run.seen[1][2]["addition_feedback"]
        self.assertIn("discoverable Python", feedback["validation_error"])
        self.assertEqual(feedback["diagnostics"][0]["path"], "tests/test_ui.py")

    def test_partial_requested_coverage_can_use_later_existing_attempt(self):
        run = direct_run([GOOD])
        result = run.additions(suite(), {2, 3})
        self.assertEqual({entry.criterion for entry in result.coverage}, {1, 2})
        self.assertEqual(len(run.seen), 1)

    def test_exhaustion_zero_budget_and_strengthening_all_share_the_existing_limit(self):
        wrong = {"files": [TEST], "coverage": [COVERAGE]}
        for observations in (None, [{"path": "api/App.java", "line": 1, "family": "return",
                                      "original": "good", "replacement": "bad"}]):
            with self.subTest(strengthen=observations is not None):
                run = direct_run([wrong, wrong])
                with self.assertRaisesRegex(ValueError, "NEW files"):
                    run.additions(suite(), {2}, observations=observations)
                self.assertEqual(len(run.seen), 2)
        run = direct_run([], budget=0)
        with self.assertRaisesRegex(ValueError, "budget exhausted"):
            run.additions(suite(), {2})
        self.assertEqual(run.seen, [])

    def test_dispatch_errors_are_not_validation_corrections(self):
        for error in (OSError("unknown completion"), ValueError("context admission failed")):
            with self.subTest(error=error):
                run = direct_run([error, GOOD])
                with self.assertRaises(type(error)):
                    run.additions(suite(), {2})
                self.assertEqual(len(run.seen), 1)
                self.assertNotIn("validation_failure", run.stages["build_tests_coverage_1"])

    def test_invalid_original_command_is_not_submitted_as_an_addition_problem(self):
        original = suite().model_copy(update={"commands": ["exit 0"]})
        run = direct_run([GOOD])
        with self.assertRaisesRegex(ValueError, "Build requires"):
            run.additions(original, {2})
        self.assertEqual(run.seen, [])


class CoverageGraphTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.models, self.executor = CoverageModels(), CoverageExecutor()
        self.runtime = Runtime(Path(temporary.name), Config(build={"offline_project_enabled": True}),
                               self.models, self.executor)
        self.addCleanup(self.runtime.close)

    def execute(self, profile=POLICY):
        row = self.runtime.prepare({"mode": "build", "prompt": BRIEF, "build_profile": profile})
        self.runtime.approve(row["id"], row["digest"])
        self.runtime.queue.join()
        return self.runtime.store.get(row["id"])

    def test_corrected_addition_reaches_qwen_groups_and_full_acceptance(self):
        row = self.execute()
        self.assertEqual(row["status"], "completed", row.get("result"))
        self.assertEqual([c["stage"] for c in row["calls"]], ["build_plan", "build_tests",
            "build_tests_coverage_1", "build_tests_coverage_2", "build_project_plan", "implement_group_1", "implement_group_2"])
        self.assertEqual(self.executor.tests, [[TEST, UI_TEST]])
        for stage, model, settings, payload in self.models.seen:
            if stage.startswith("build_tests_coverage_"):
                self.assertNotIn("addition_budget", json.loads(payload["messages"][1]["content"]))
            if stage.startswith("implement_group"):
                self.assertEqual(model, "qwen3.8:27b-mtp-q4_K_M")
                self.assertEqual(settings["context"], 98304)
                self.assertEqual(settings["think"], "xhigh")
                self.assertEqual(json.loads(payload["messages"][1]["content"])["frozen_tests"]["files"], [TEST, UI_TEST])
        self.assertIn("validation_failure", row["result"]["stages"]["build_tests_coverage_1"])

    def test_reviewed_addition_prompt_exposes_remaining_utf8_budget_on_retry(self):
        contract = offline_project_contract(REVIEWED_POLICY)
        remaining = 256
        original = TEST["content"] + "# caf\u00e9 "
        draft_padding = contract["test_bytes"] - remaining - len(original.encode("utf-8"))
        self.models.test_file = {**TEST, "content": original + "x" * draft_padding}
        oversized = UI_TEST["content"] + "# "
        addition_padding = remaining + 1 - len(oversized.encode("utf-8"))
        self.models.first_addition = {**GOOD, "files": [{**UI_TEST, "content": oversized + "x" * addition_padding}]}

        row = self.execute(REVIEWED_POLICY)

        self.assertEqual(row["status"], "completed", row.get("result"))
        requests = [json.loads(payload["messages"][1]["content"])
                    for stage, _, _, payload in self.models.seen if stage.startswith("build_tests_coverage_")]
        self.assertEqual(len(requests), 2)
        for request in requests:
            budget = request["addition_budget"]
            self.assertEqual(budget["max_utf8_bytes"], remaining)
            self.assertEqual(budget["max_files"], contract["test_files"] - 1)
            self.assertRegex(budget["suggested_path"], r"^tests/test_\w+\.py$")
            self.assertNotEqual(budget["suggested_path"].casefold(), TEST["path"].casefold())
            self.assertEqual(request["requested_criteria"], [2])
        self.assertIn("byte manifest bound", requests[1]["addition_feedback"]["validation_error"])
        self.assertEqual(self.executor.tests, [[self.models.test_file, UI_TEST]])

    def test_two_bad_known_responses_stop_before_implementation(self):
        self.models.second_addition = self.models.first_addition
        row = self.execute()
        self.assertEqual(row["status"], "failed")
        self.assertEqual([c["stage"] for c in row["calls"]], ["build_plan", "build_tests", "build_tests_coverage_1", "build_tests_coverage_2"])
        self.assertEqual(self.executor.tests, [])

    def test_embedded_dom_source_syntax_uses_existing_syntax_budget_then_reaches_qwen(self):
        self.models.first_addition = {"files": [BROKEN_UI], "coverage": [UI_COVERAGE]}
        row = self.execute()
        self.assertEqual(row["status"], "completed", row.get("result"))
        self.assertEqual([c["stage"] for c in row["calls"]], ["build_plan", "build_tests",
            "build_tests_coverage_1", "build_tests_syntax_1", "build_project_plan", "implement_group_1", "implement_group_2"])
        repair = next(payload for stage, _, _, payload in self.models.seen if stage == "build_tests_syntax_1")
        data = json.loads(repair["messages"][1]["content"])
        self.assertEqual([file["path"] for file in data["files"]], ["tests/test_ui.py"])
        self.assertEqual(data["diagnostics"][0]["path"], "tests/test_ui.py")
        self.assertIn("unterminated string", data["diagnostics"][0]["message"])
        self.assertEqual(self.executor.tests, [[TEST, UI_TEST]])

    def test_unresolved_syntax_repair_never_spends_another_coverage_call(self):
        self.models.first_addition = {"files": [BROKEN_UI], "coverage": [UI_COVERAGE]}
        self.models.syntax_file = BROKEN_UI
        self.runtime.config.build.max_test_syntax_repairs = 1
        row = self.execute()
        self.assertEqual(row["status"], "failed", row.get("result"))
        self.assertEqual([c["stage"] for c in row["calls"]], ["build_plan", "build_tests",
            "build_tests_coverage_1", "build_tests_syntax_1"])
        self.assertEqual(self.executor.tests, [])

    def test_unknown_syntax_repair_completion_is_not_replayed(self):
        self.models.first_addition = {"files": [BROKEN_UI], "coverage": [UI_COVERAGE]}
        self.models.syntax_unknown = True
        row = self.execute()
        self.assertEqual(row["status"], "blocked", row.get("result"))
        self.assertEqual([c["stage"] for c in row["calls"]], ["build_plan", "build_tests",
            "build_tests_coverage_1", "build_tests_syntax_1"])
        self.assertEqual(self.executor.tests, [])

    def test_unknown_completion_and_admission_failure_never_dispatch_a_second_addition(self):
        for field, status in (("unknown", "blocked"), ("coverage_admission_failure", "failed")):
            with self.subTest(field=field):
                setattr(self.models, field, True)
                row = self.execute()
                self.assertEqual(row["status"], status, row.get("result"))
                self.assertNotIn("build_tests_coverage_2", [c["stage"] for c in row["calls"]])
                self.assertEqual(self.executor.tests, [])
                setattr(self.models, field, False)
                self.models.seen.clear()


if __name__ == "__main__":
    unittest.main()
