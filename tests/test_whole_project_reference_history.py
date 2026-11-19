"""Remember failed native attempts without expanding scope or crediting success."""

from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from orchestrator.build_workflow import reference_test_failures
from orchestrator.project_build import unittest_failure_excerpts
from orchestrator.store import digest
from orchestrator.whole_project import IndependentSuiteRepairExhausted
from orchestrator.whole_project_reference_history import (
    empty_reference_history, history_with_record, project_reference_history,
    reference_attempt_record, validate_reference_history,
)
from orchestrator.whole_project_test_repair import (
    DIAGNOSIS_POLICY, DIAGNOSIS_POLICY_V2, METHOD_PATCH_POLICY_V3, METHOD_PATCH_POLICY_V4,
    compose_reference_method_patch, reference_diagnosis_binding, reference_diagnosis_inputs,
    reference_method_patch_inputs, validate_reference_diagnosis,
)
from test_whole_project_test_repair import diagnosis_response, failure, replacement_delta, suite, workflow


RUN_ID = "00000000-0000-4000-8000-000000000001"


def record(number, current=None, proposal=None, rejection=None):
    current = current or suite()
    problem = failure()
    problem["failures"][0]["excerpt"] += f"\nObserved operation {number} failed."
    result = {"source_run_id": RUN_ID, "reference_stage": f"build_control_{number}",
        "reference_receipt_digest": digest({"failed": number}), "suite_digest": digest(current.model_dump()),
        "failure": problem, "attempt": None}
    if proposal is not None:
        inputs = reference_method_patch_inputs(current, problem, byte_limit=16000, policy=METHOD_PATCH_POLICY_V3)
        revised = current if rejection else compose_reference_method_patch(current, problem, proposal,
            byte_limit=16000, policy=METHOD_PATCH_POLICY_V3)[0]
        result["attempt"] = reference_attempt_record(f"build_tests_defect_{min(number, 3)}_file_1", inputs,
            proposal, digest(current.model_dump()), digest(revised.model_dump()), rejection)
    return result


def failed_receipt():
    return {"status": "failed", "commands": [{"exit_code": 1, "cleanup_confirmed": True, "stdout": "", "stderr":
        "test_auth (test_api.API) ... FAIL\ntest_passed (test_api.API) ... ok\n"
        "======================================================================\nFAIL: test_auth (test_api.API)\n"
        "----------------------------------------------------------------------\nAssertionError: 200 != 401\n"
        "----------------------------------------------------------------------\nRan 2 tests in 0.1s\nFAILED (failures=1)\n"}]}


class ReferenceHistoryTests(unittest.TestCase):
    def test_exact_history_and_rejected_proposal_have_no_pass_credit(self):
        first = record(1, proposal=replacement_delta())
        history = history_with_record(empty_reference_history(), first)
        self.assertEqual(validate_reference_history(history), history)
        self.assertEqual(history["records"][0]["attempt"]["proposal"], replacement_delta())
        self.assertEqual(history["records"][0]["attempt"]["outcome"], "applied")
        invalid = replacement_delta(); invalid["changes"][0]["target"] = "API.test_passed"
        rejected = record(2, proposal=invalid, rejection="Target is outside the authorized scope")
        history = history_with_record(history, rejected)
        self.assertEqual(history["records"][1]["attempt"]["proposal"], invalid)
        self.assertEqual(history["records"][1]["attempt"]["previous_suite_digest"],
                         history["records"][1]["attempt"]["result_suite_digest"])
        self.assertEqual(history["records"][1]["attempt"]["changes"], [])
        self.assertNotIn("passed", history["records"][0])
        for mutation in ("source", "proposal", "credit", "receipt"):
            bad = deepcopy(history)
            if mutation == "source":
                bad["records"][0]["attempt"]["changes"][0]["original_source"] += "# fabricated"
            elif mutation == "proposal":
                bad["records"][0]["attempt"]["proposal"]["changes"][0]["replacement_source"] += "# fabricated"
            elif mutation == "credit":
                bad["records"][0]["attempt"]["outcome"] = "passed"
            else:
                bad["records"][0]["reference_receipt_digest"] = "unbound"
            bad["digest"] = digest(bad["records"])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_reference_history(bad)

    def test_projection_preserves_attempts_when_recovery_adds_repeated_failure_observations(self):
        history = empty_reference_history()
        first = record(1, proposal=replacement_delta())
        second = record(2, proposal=replacement_delta(), rejection="Recorded rejection")
        for row in (first, second, record(3), record(4), record(5)):
            history = history_with_record(history, row)
        projected = project_reference_history(history, failure())
        self.assertEqual([row["reference_stage"] for row in projected["records"]],
                         ["build_control_1", "build_control_2", "build_control_5"])
        self.assertEqual(projected["omitted_records"], 2)
        self.assertEqual(projected["omitted_records_digest"], digest(history["records"][2:4]))
        self.assertEqual(projected["history_digest"], history["digest"])
        self.assertIn("never reference success", projected["outcome_semantics"])
        self.assertEqual(len(history["records"]), 5)

    def test_new_diagnosis_requires_history_citation_and_patch_binds_projection(self):
        history = history_with_record(empty_reference_history(), record(1, proposal=replacement_delta()))
        original = suite(); problem = failure()
        inputs = reference_diagnosis_inputs(original, problem, byte_limit=16000,
            policy=DIAGNOSIS_POLICY_V2, history=history)
        value = diagnosis_response(inputs)
        proof = validate_reference_diagnosis(original, problem, value, byte_limit=16000,
            policy=DIAGNOSIS_POLICY_V2, history=history)
        self.assertEqual(proof["history_projection_digest"], digest(inputs["reference_attempt_history"]))
        binding = reference_diagnosis_binding(original, problem, value, byte_limit=16000,
            stage="build_tests_diagnose_1_file_1", policy=DIAGNOSIS_POLICY_V2, history=history)
        self.assertTrue(any(row["kind"] == "prior_reference_attempt" for row in binding["evidence"]))
        bad = deepcopy(value); bad["cases"][0]["evidence_ids"] = ["failure:0", "source:0"]
        with self.assertRaisesRegex(ValueError, "history"):
            validate_reference_diagnosis(original, problem, bad, byte_limit=16000,
                policy=DIAGNOSIS_POLICY_V2, history=history)
        inputs_patch = reference_method_patch_inputs(original, problem, byte_limit=16000,
            policy=METHOD_PATCH_POLICY_V4, history=history)
        self.assertEqual(inputs_patch["reference_attempt_history"], inputs["reference_attempt_history"])
        old = reference_method_patch_inputs(original, problem, byte_limit=16000, policy=METHOD_PATCH_POLICY_V3)
        self.assertNotIn("reference_attempt_history", old)
        self.assertEqual(inputs_patch["targets"], old["targets"])
        self.assertNotIn("reference_attempt_history", reference_diagnosis_inputs(original, problem, byte_limit=16000))

    def test_runtime_records_completed_receipt_native_attempt_and_final_exhausted_failure(self):
        instance = workflow(); original = suite(); result = failed_receipt()
        instance.state = {"run_id": RUN_ID}
        instance.output["verification"]["controls"] = [{"stage": "build_control_1", "receipt_digest": digest(result)}]
        def author(stage, _role, _kind, _instruction, **inputs):
            instance.stages[stage] = {}
            return diagnosis_response(inputs) if stage.startswith("build_tests_diagnose_") else replacement_delta()
        instance.ask = Mock(side_effect=author)
        revised = instance.repair_reference_failures(original, reference_test_failures(original, result), result)
        history = instance._reference_history()
        self.assertEqual(len(history["records"]), 1)
        saved = history["records"][0]
        self.assertEqual(saved["reference_receipt_digest"], digest(result))
        self.assertEqual(saved["suite_digest"], digest(original.model_dump()))
        self.assertEqual(saved["attempt"]["result_suite_digest"], digest(revised.model_dump()))
        self.assertEqual(saved["attempt"]["proposal"], replacement_delta())
        self.assertEqual(saved["failure"]["failures"][0]["excerpt"], unittest_failure_excerpts(result)["test_auth"])
        self.assertEqual(instance.ask.call_count, 2)
        instance.counters["build_tests_defect"] = 2
        instance.output["verification"]["controls"].append({"stage": "build_control_2", "receipt_digest": digest(result)})
        with self.assertRaises(IndependentSuiteRepairExhausted):
            instance.repair_reference_failures(revised, reference_test_failures(revised, result), result)
        self.assertEqual(len(instance._reference_history()["records"]), 2)
        self.assertIsNone(instance._reference_history()["records"][-1]["attempt"])
        self.assertEqual(instance.ask.call_count, 2)
        recovered = workflow()
        recovered.bundle["whole_project_seed"] = {"independent_reference_history": deepcopy(instance._reference_history())}
        self.assertEqual(recovered._reference_history(), instance._reference_history())
        self.assertEqual(recovered.counters["build_tests_defect"], 0)



if __name__ == "__main__":
    unittest.main()
