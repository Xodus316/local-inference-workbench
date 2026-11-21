"""Causal diagnosis is bounded evidence, never test acceptance or wider edit authority."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import Mock, patch

from orchestrator.build_workflow import build_model_payload
from orchestrator.project_opencode import _AGENT, _PROVIDER
from orchestrator.store import STAGES, digest
from orchestrator.whole_project_test_repair import (
    DIAGNOSIS_OUTPUT_TOKENS, DIAGNOSIS_POLICY, INLINE_DIAGNOSIS_POLICY,
    INLINE_METHOD_PATCH_POLICY_V3, METHOD_PATCH_POLICY_V2, METHOD_PATCH_POLICY_V3,
    REFERENCE_GENERATION_LIMITS, ReferenceDiagnosis, compose_reference_method_patch,
    reference_diagnosis_binding, reference_diagnosis_inputs, reference_diagnosis_type,
    reference_method_patch_inputs, reference_method_patch_type, validate_reference_diagnosis,
)
from test_whole_project_test_repair import (
    SOURCE, diagnosis_response, failure, replacement_delta, suite, workflow,
)
from test_whole_project_test_repair_v2 import fake_worker


class ReferenceDiagnosisTests(unittest.TestCase):
    def test_full_read_only_context_restores_passing_fixtures_without_new_edit_authority(self):
        original = suite()
        legacy = reference_method_patch_inputs(original, failure(), byte_limit=16000, policy=METHOD_PATCH_POLICY_V2)
        current = reference_method_patch_inputs(original, failure(), byte_limit=16000, policy=METHOD_PATCH_POLICY_V3)
        self.assertNotIn("request('/ok')", legacy["read_only_context"])
        self.assertEqual(current["read_only_context"], SOURCE)
        self.assertEqual(current["read_only_support_files"], [row.model_dump() for row in original.files[1:]])
        self.assertEqual(current["targets"], legacy["targets"])
        self.assertEqual(current["scope_projection"]["omitted_definitions"], [])
        self.assertEqual(current["previous_suite_digest"], digest(original.model_dump()))
        forged = replacement_delta(); forged["changes"][0]["target"] = "API.test_passed"
        with self.assertRaisesRegex(ValueError, "authorized"):
            compose_reference_method_patch(original, failure(), forged, byte_limit=16000, policy=METHOD_PATCH_POLICY_V3)
        revised, proof = compose_reference_method_patch(original, failure(), replacement_delta(),
            byte_limit=16000, policy=METHOD_PATCH_POLICY_V3)
        self.assertEqual(revised.files[0].content, SOURCE.replace("self._get(), 401", "self._get(), 200"))
        self.assertEqual(proof["policy"], METHOD_PATCH_POLICY_V3)

    def test_diagnosis_covers_exact_cases_and_cites_bound_evidence_without_copying_it(self):
        original = suite(); problem = failure()
        inputs = reference_diagnosis_inputs(original, problem, byte_limit=16000)
        self.assertNotIn("method_patch_policy", inputs)
        self.assertEqual(inputs["diagnosis_policy"], DIAGNOSIS_POLICY)
        value = diagnosis_response(inputs)
        proof = validate_reference_diagnosis(original, problem, value, byte_limit=16000)
        self.assertEqual(proof["previous_suite_digest"], digest(original.model_dump()))
        self.assertEqual(proof["failure_digest"], digest(problem))
        self.assertEqual(proof["evidence_catalog_digest"], digest(inputs["evidence_catalog"]))
        binding = reference_diagnosis_binding(original, problem, value,
            stage="build_tests_diagnose_1_file_1", byte_limit=16000)
        self.assertEqual(binding["value"], value)
        self.assertEqual({row["id"] for row in binding["evidence"]}, set(value["cases"][0]["evidence_ids"]))
        self.assertNotIn("passed", proof)
        schema = build_model_payload(reference_diagnosis_type(inputs), "Diagnose", inputs)["schema"]
        self.assertEqual(schema["properties"]["cases"]["items"]["properties"]["test_name"]["enum"], ["API.test_auth"])
        bad_values = []
        bad = deepcopy(value); bad["cases"][0]["test_name"] = "API.test_passed"; bad_values.append(bad)
        bad = deepcopy(value); bad["cases"] *= 2; bad_values.append(bad)
        bad = deepcopy(value); bad["cases"][0]["evidence_ids"] = ["source:0", "source:1"]; bad_values.append(bad)
        bad = deepcopy(value); bad["cases"][0]["evidence_ids"] = ["failure:0", "invented"]; bad_values.append(bad)
        bad = deepcopy(value); bad["cases"][0]["replacement_source"] = "pass"; bad_values.append(bad)
        for bad in bad_values:
            with self.subTest(value=bad), self.assertRaises(ValueError):
                validate_reference_diagnosis(original, problem, bad, byte_limit=16000)
        problem["names"].append("API.test_passed")
        problem["failures"].append({"test": "test_passed", "excerpt": "AssertionError: second case"})
        two = reference_diagnosis_inputs(original, problem, byte_limit=16000)
        bad = diagnosis_response(two); bad["cases"][1]["evidence_ids"] = ["failure:0", "source:0"]
        with self.assertRaisesRegex(ValueError, "own failure"):
            validate_reference_diagnosis(original, problem, bad, byte_limit=16000)

    def test_diagnosis_and_patch_have_separate_strict_stage_and_context_bounds(self):
        instance = workflow(); original = suite(); problem = failure()
        inputs = reference_diagnosis_inputs(original, problem, byte_limit=16000)
        value = diagnosis_response(inputs); kind = reference_diagnosis_type(inputs)
        def generate(stage, *_args, **_kwargs):
            instance.stages[stage] = {}
            return {"value": value}
        instance._opencode = Mock(side_effect=generate)
        for tokens, allowed in ((65536 - DIAGNOSIS_OUTPUT_TOKENS, True),
                                (65537 - DIAGNOSIS_OUTPUT_TOKENS, False)):
            instance._opencode.reset_mock()
            with patch("orchestrator.whole_project.validate_project_prompt", return_value=tokens):
                if allowed:
                    instance.ask("build_tests_diagnose_1_file_1", "test_author", kind, "Diagnose", **inputs)
                else:
                    with self.assertRaisesRegex(ValueError, "output reserve"):
                        instance.ask("build_tests_diagnose_1_file_1", "test_author", kind, "Diagnose", **inputs)
            self.assertEqual(instance._opencode.call_count, int(allowed))
        for stage, role, model_type, visible in (
                ("build_tests_defect_1_file_1", "test_author", kind, {}),
                ("build_tests_diagnose_1_file_1", "repairer", kind, {}),
                ("build_tests_diagnose_1_file_1", "test_author", ReferenceDiagnosis, {}),
                ("build_tests_diagnose_1_file_1", "test_author", kind, {"app.py": "source"})):
            with self.assertRaises(ValueError):
                instance._generate(stage, role, model_type, "Diagnose", inputs, visible, tool_policy=INLINE_DIAGNOSIS_POLICY)
        patch_inputs = reference_method_patch_inputs(original, problem, byte_limit=16000, policy=METHOD_PATCH_POLICY_V3)
        patch_kind = reference_method_patch_type(patch_inputs["targets"], policy=METHOD_PATCH_POLICY_V3)
        with self.assertRaisesRegex(ValueError, "causal diagnosis"):
            instance.ask("build_tests_defect_1_file_1", "test_author", patch_kind, "Patch", **patch_inputs)
        binding = reference_diagnosis_binding(original, problem, value,
            stage="build_tests_diagnose_1_file_1", byte_limit=16000)
        patch_inputs["reference_diagnosis"] = binding
        with patch("orchestrator.whole_project.validate_project_prompt", return_value=32768):
            instance.ask("build_tests_defect_1_file_1", "test_author", patch_kind, "Patch", **patch_inputs)
        for field in ("diagnosis_digest", "previous_suite_digest", "failure_digest"):
            tampered = deepcopy(patch_inputs); tampered["reference_diagnosis"]["proof"][field] = "0" * 64
            with self.assertRaisesRegex(ValueError, "causal diagnosis"):
                instance.ask("build_tests_defect_1_file_1", "test_author", patch_kind, "Patch", **tampered)

    def test_valid_diagnosis_is_consumed_but_invalid_or_unknown_diagnosis_cannot_patch(self):
        for outcome in ("valid", "invalid", "unknown"):
            instance = workflow(); original = suite()
            def author(stage, _role, _kind, instruction, **inputs):
                instance.stages[stage] = {}
                if stage.startswith("build_tests_diagnose_"):
                    self.assertIn("Return no replacement code", instruction)
                    if outcome == "unknown":
                        raise RuntimeError("native_completion_unknown")
                    result = diagnosis_response(inputs)
                    if outcome == "invalid":
                        result["cases"][0]["evidence_ids"] = ["source:0", "source:1"]
                    return result
                self.assertEqual(inputs["reference_diagnosis"]["proof"]["previous_suite_digest"], digest(original.model_dump()))
                self.assertEqual(inputs["read_only_context"], SOURCE)
                return replacement_delta()
            instance.ask = Mock(side_effect=author)
            with self.subTest(outcome=outcome):
                if outcome == "unknown":
                    with self.assertRaisesRegex(RuntimeError, "native_completion_unknown"):
                        instance._repair_reference_test_files(original, [failure()], "policy")
                else:
                    revised = instance._repair_reference_test_files(original, [failure()], "policy")
                    self.assertEqual(revised == original, outcome == "invalid")
                self.assertEqual(instance.ask.call_count, 2 if outcome == "valid" else 1)
                self.assertEqual(instance.counters["build_tests_defect"], 1)
                self.assertNotIn("build_frozen", instance.stages)
                if outcome == "valid":
                    record = instance.stages["build_tests_defect_1_file_1"]["method_patch"]
                    self.assertEqual(record["diagnosis_stage"], "build_tests_diagnose_1_file_1")
                    self.assertEqual(record["diagnosis_digest"], instance.stages[record["diagnosis_stage"]]["reference_diagnosis"]["diagnosis_digest"])
                if outcome == "invalid":
                    self.assertIn("diagnosis_rejected", instance.stages["build_tests_diagnose_1_file_1"])



class ReferenceDiagnosisWorkerTests(unittest.TestCase):
    def test_operation_policies_bind_distinct_caps_and_finite_stage_names(self):
        worker = fake_worker()
        with TemporaryDirectory() as directory:
            for policy, stage, cap in ((INLINE_DIAGNOSIS_POLICY, "build_tests_diagnose_1_file_1", 8192),
                                       (INLINE_METHOD_PATCH_POLICY_V3, "build_tests_defect_1_file_1", 32768)):
                env = worker._project_environment(Path(directory), {"context": 65536, "think": True}, (),
                    "Instruction", {"type": "object"}, tool_policy=policy)
                config = json.loads(Path(env["OPENCODE_CONFIG"]).read_text(encoding="utf-8"))
                self.assertEqual(config["provider"][_PROVIDER]["models"][worker.model_alias]["limit"]["output"], cap)
                self.assertEqual(config["permission"], {"*": "deny"})
                self.assertEqual(config["compaction"], {"auto": True})
                self.assertEqual(config["agent"][_AGENT]["steps"], 9)
                self.assertEqual(REFERENCE_GENERATION_LIMITS[policy]["max_completion_tokens"], cap)
                self.assertIn(stage, STAGES)
                wrong_stage = "build_tests_defect_1_file_1" if policy == INLINE_DIAGNOSIS_POLICY else "build_tests_diagnose_1_file_1"
                with self.assertRaisesRegex(ValueError, "approved role and stage"):
                    worker._session(stage=wrong_stage, role="test_author", model=worker.model,
                        settings={"context": 65536, "think": True}, original={}, editable=(), system="", prompt="",
                        schema={"type": "object"}, collect=Mock(), cancel=threading.Event(),
                        deadline=time.monotonic()+20, scratch_parent=Path(directory), on_event=None, tool_policy=policy)
        self.assertNotIn("build_tests_diagnose_4_file_1", STAGES)
        self.assertNotIn("build_tests_diagnose_1_file_16", STAGES)


if __name__ == "__main__":
    unittest.main()
