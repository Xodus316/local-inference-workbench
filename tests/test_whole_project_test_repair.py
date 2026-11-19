"""Reference repair can change only native-authored, explicitly authorized definitions."""

from collections import Counter
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from orchestrator.build_workflow import BuildRun
from orchestrator.project_build import ProjectArtifact, ProjectBuildRun, ProjectProject, ProjectTestSuite
from orchestrator.store import digest
from orchestrator.whole_project import WholeProjectBuildRun
from orchestrator.whole_project_test_repair import (
    METHOD_PATCH_POLICY, REFERENCE_REPAIR_GUIDANCE, ReferenceMethodPatch,
    compose_reference_method_patch, reference_method_patch_inputs, reference_method_patch_type,
)


SOURCE = '''import unittest
from helpers import request

# Preserve non-ASCII context: café / 東京
class API(unittest.TestCase):
    marker = 'immutable'

    @classmethod
    def setUpClass(cls):
        cls.label = 'fixture'

    def _get(self):
        return request('/protected')

    def _unrelated(self):
        return request('/other')

    def test_auth(self):
        spec = r"""const data = {
  text: 'café',
};"""
        self.assertEqual(self._get(), 401)

    def test_passed(self):
        self.assertEqual(request('/ok'), 200)
'''


def suite(source=SOURCE):
    return ProjectTestSuite.model_validate({
        "implementation_files": ["app.py"], "commands": ["python3 -m unittest discover -s tests -v"],
        "assumptions": ["Retained assumption"],
        "files": [{"path": "tests/test_api.py", "content": source},
                  {"path": "tests/helpers.py", "content": "def request(path):\n    raise NotImplementedError(path)\n"}],
        "coverage": [{"criterion": 1, "test_file": "tests/test_api.py", "test_name": name,
                      "scenario": "Public HTTP request", "expected_result": "Expected status",
                      "catches": "Incorrect public status"} for name in ("API.test_auth", "API.test_passed")],
    })


def failure():
    return {"path": "tests/test_api.py", "names": ["API.test_auth"], "message": "Trusted reference failed these tests",
            "failures": [{"test": "test_auth", "excerpt": 'File "/workspace/tests/test_api.py", line 22, in test_auth\nAssertionError: 200 != 401'}]}


def delta(value=None, problem=None, target="API.test_auth"):
    value, problem = value or suite(), problem or failure()
    inputs = reference_method_patch_inputs(value, problem, byte_limit=16000)
    definition = next(item for item in inputs["targets"] if item["target"] == target)
    source = definition["source"].replace("self._get(), 401", "self._get(), 200")
    if target == "API._get":
        source = source.replace("'/protected'", "'/protected?fresh=1'")
    return {"changes": [{"target": target, "original_digest": definition["original_digest"], "source": source}]}


def replacement_delta(value=None, problem=None, target="API.test_auth"):
    return {"changes": [{"target": row["target"], "expected_original_digest": row["original_digest"],
                          "replacement_source": row["source"]} for row in delta(value, problem, target)["changes"]]}


def diagnosis_response(inputs):
    result = {"cases": [{"test_name": name, "observed_failure": "The current operation failed its assertion.",
        "cause": "The operation and its expected public behavior do not agree.",
        "evidence_ids": [next(row["id"] for row in inputs["evidence_catalog"]
                             if row["kind"] == "failure" and row["test_name"] == name),
                         next(row["id"] for row in inputs["evidence_catalog"] if row["kind"] == "test_source")],
        "required_operation_change": "Correct the failing operation while retaining the meaningful assertion.",
        "expected_behavior": "Exercise the specified successful public behavior."}
        for name in inputs["required_tests"]]}
    if inputs.get("diagnosis_policy") == "whole-reference-diagnosis-v2":
        for row in result["cases"]:
            row["prior_attempt_assessment"] = "Compare supplied prior operations and failures; preserve the required behavior."
            prior = next((item["id"] for item in inputs["evidence_catalog"]
                          if item["kind"] == "prior_reference_attempt" and item["has_attempt"]
                          and row["test_name"] in item["test_names"]), None)
            if prior:
                row["evidence_ids"].append(prior)
    return result


def workflow():
    result = object.__new__(WholeProjectBuildRun)
    result.scoped_repairs = True
    result.bundle = {"reference_test_repair": "scoped-reference-test-repair-v3",
                     "generation": {"test_author": {"context": 65536}}}
    result.contract = {"test_bytes": 16000, "test_files": 15}
    result.counters = Counter()
    result.limits = {"max_test_defect_repairs": 2}
    result.stages = {}; result.evidence = {}
    result.reference_test_repair_feedback = {}; result.reference_test_repair_notes = {}
    result.frontend_dom_test_brief = ""; result.environment = {}
    result.suite_type = ProjectTestSuite
    result.output = {"plan": {"goal": "Public HTTP contract", "acceptance_criteria": ["Correct authorization"],
                    "assumptions": [], "implementation_files": ["app.py"], "implementation_steps": ["STALE"]},
                    "verification": {}}
    result.brief = {"prompt": "Keep frozen test bytes unchanged during application repair."}
    result.runtime = SimpleNamespace(whole_project_prompt_tokenizers={})
    return result


class ReferenceMethodPatchTests(unittest.TestCase):
    def test_composition_preserves_all_other_bytes_with_multiline_unicode_and_crlf(self):
        for text in (SOURCE, SOURCE.replace("\n", "\r\n")):
            value = suite(text)
            revised, proof = compose_reference_method_patch(value, failure(), delta(value), byte_limit=16000)
            self.assertEqual(revised.files[0].content, text.replace("self._get(), 401", "self._get(), 200"))
            self.assertEqual(value.files[0].content, text)
            self.assertEqual(revised.files[1:], value.files[1:])
            self.assertEqual(revised.coverage, value.coverage)
            self.assertEqual(revised.assumptions, value.assumptions)
            self.assertEqual(proof["changed_targets"], ["API.test_auth"])

    def test_helper_scope_is_derived_and_imported_helpers_remain_read_only(self):
        inputs = reference_method_patch_inputs(suite(), failure(), byte_limit=16000)
        self.assertEqual({row["target"] for row in inputs["targets"]}, {"API.test_auth", "API._get"})
        self.assertNotIn("self.assertEqual(request('/ok'), 200)", inputs["read_only_context"])
        self.assertEqual([row["path"] for row in inputs["read_only_support_files"]], ["tests/helpers.py"])
        revised, proof = compose_reference_method_patch(suite(), failure(), delta(target="API._get"), byte_limit=16000)
        self.assertEqual(proof["changed_targets"], ["API._get"])
        self.assertEqual(revised.files[0].content, SOURCE.replace("'/protected'", "'/protected?fresh=1'"))
        for name in ("API.test_passed", "API._unrelated", "API.setUpClass", "helpers.request"):
            bad = delta(); bad["changes"][0]["target"] = name
            with self.subTest(name=name), self.assertRaises(ValueError):
                compose_reference_method_patch(suite(), failure(), bad, byte_limit=16000)

    def test_same_file_traceback_authorizes_fixture_and_preserves_its_decorator(self):
        problem = failure()
        problem["failures"][0]["excerpt"] += '\nFile "/workspace/tests/test_api.py", line 9, in setUpClass'
        inputs = reference_method_patch_inputs(suite(), problem, byte_limit=16000)
        definition = next(row for row in inputs["targets"] if row["target"] == "API.setUpClass")
        self.assertTrue(definition["source"].startswith("    @classmethod\n"))
        change = {"target": definition["target"], "original_digest": definition["original_digest"],
                  "source": definition["source"].replace("'fixture'", "'fixed fixture'")}
        revised, _ = compose_reference_method_patch(suite(), problem, {"changes": [change]}, byte_limit=16000)
        self.assertIn("    @classmethod\n", revised.files[0].content)
        change["source"] = change["source"].replace("    @classmethod\n", "")
        with self.assertRaisesRegex(ValueError, "signatures and decorators"):
            compose_reference_method_patch(suite(), problem, {"changes": [change]}, byte_limit=16000)

    def test_rejects_noop_crossed_digest_scope_tampering_duplicates_and_scope_escape(self):
        inputs = reference_method_patch_inputs(suite(), failure(), byte_limit=16000)
        source = next(row for row in inputs["targets"] if row["target"] == "API.test_auth")["source"]
        candidates = []
        bad = delta(); bad["changes"][0]["source"] = source + "\n# only a comment"; candidates.append(bad)
        bad = delta(); bad["changes"][0]["original_digest"] = next(row for row in inputs["targets"] if row["target"] == "API._get")["original_digest"]; candidates.append(bad)
        bad = delta(); bad["changes"] *= 2; candidates.append(bad)
        bad = delta(); bad["targets"] = inputs["targets"]; candidates.append(bad)
        bad = delta(); bad["changes"][0]["source"] += "\n    def test_new(self):\n        pass"; candidates.append(bad)
        bad = delta(); bad["changes"][0]["source"] += "\nprint('outside class')"; candidates.append(bad)
        bad = delta(); bad["changes"][0]["source"] = bad["changes"][0]["source"].replace("test_auth(self)", "test_auth(self, other=None)"); candidates.append(bad)
        bad = delta(); bad["changes"][0]["source"] = "    def test_auth(self):\n        self.skipTest('ignore failure')"; candidates.append(bad)
        bad = delta(); bad["changes"][0]["source"] = "    def test_auth(self):\n        self.assertTrue(True)"; candidates.append(bad)
        bad = delta(); bad["changes"][0]["source"] = "    def test_auth(self):\n        pass"; candidates.append(bad)
        for index, bad in enumerate(candidates):
            with self.subTest(index=index), self.assertRaises(ValueError):
                compose_reference_method_patch(suite(), failure(), bad, byte_limit=16000)
        with self.assertRaises(ValueError):
            compose_reference_method_patch(suite(), failure(), delta(), byte_limit=10)

    def test_actual_request_schema_keeps_singleton_enums_and_phase_specific_guidance(self):
        instance = workflow()
        inputs = reference_method_patch_inputs(suite(), failure(), byte_limit=16000)
        instance._opencode = Mock(return_value={"value": delta()})
        kind = reference_method_patch_type(inputs["targets"])
        with patch("orchestrator.whole_project.validate_project_prompt", return_value=100):
            instance.ask("build_tests_defect_1_file_1", "test_author", kind, "Patch the named definitions.", **inputs)
        sent = instance._opencode.call_args.kwargs["payload"]
        self.assertIn(REFERENCE_REPAIR_GUIDANCE, sent["messages"][0]["content"])
        self.assertNotIn("Cover every supplied criterion", sent["messages"][0]["content"])
        self.assertEqual(json.loads(sent["messages"][1]["content"])["prompt"], instance.brief["prompt"])
        for stage, role, model_type, visible in (("build_tests_retry", "test_author", kind, {}),
                ("build_tests_defect_1_file_1", "repairer", kind, {}),
                ("build_tests_defect_1_file_1", "test_author", ProjectTestSuite, {}),
                ("build_tests_defect_1_file_1", "test_author", kind, {"app.py": "hidden"}),
                ("build_tests_defect_1_file_1", "test_author", ReferenceMethodPatch, {})):
            with self.subTest(stage=stage, role=role), self.assertRaises(ValueError):
                instance._generate(stage, role, model_type, "Patch", inputs, visible)
        single = reference_method_patch_type(inputs["targets"][:1])
        from orchestrator.build_workflow import build_model_payload
        schema = build_model_payload(single, "Patch", inputs)["schema"]
        fields = schema["properties"]["changes"]["items"]["properties"]
        self.assertEqual(fields["target"]["enum"], [inputs["targets"][0]["target"]])
        self.assertEqual(fields["original_digest"]["enum"], [inputs["targets"][0]["original_digest"]])

    def test_full_reference_route_and_rejected_patch_retry_never_freeze_bad_tests(self):
        instance = workflow()
        instance.artifact_type, instance.project_type = ProjectArtifact, ProjectProject
        instance.unverified_criteria = frozenset(); instance.repair_invalid_test_references = True
        instance.fixture_problems = Mock(return_value=[]); instance.validate_suite = Mock()
        bad = {"status": "failed", "commands": [{"exit_code": 1, "cleanup_confirmed": True, "stdout": "", "stderr":
            "test_auth (test_api.API) ... FAIL\ntest_passed (test_api.API) ... ok\n"
            "======================================================================\nFAIL: test_auth (test_api.API)\n"
            "----------------------------------------------------------------------\nAssertionError: 200 != 401\n"
            "----------------------------------------------------------------------\nRan 2 tests in 0.1s\nFAILED (failures=1)\n"}]}
        good = {"status": "passed", "commands": [{"exit_code": 0, "cleanup_confirmed": True, "stdout": "", "stderr":
            "test_auth (test_api.API) ... ok\ntest_passed (test_api.API) ... ok\nRan 2 tests in 0.1s\nOK\n"}]}
        controls = []
        def control(current):
            self.assertNotIn("build_frozen", instance.stages)
            controls.append(current)
            return good if "self._get(), 200" in current.files[0].content else bad
        instance.control = Mock(side_effect=control)
        def author(stage, _role, _kind, _instruction, **inputs):
            instance.stages[stage] = {}
            if stage.startswith("build_tests_diagnose_"):
                return diagnosis_response(inputs)
            if stage.startswith("build_tests_defect_1"):
                item = next(row for row in inputs["targets"] if row["target"] == "API.test_auth")
                return {"changes": [{"target": item["target"], "expected_original_digest": item["original_digest"],
                                      "replacement_source": item["original_source"]}]}
            self.assertIn("no-op", inputs["previous_rejection"])
            return replacement_delta()
        instance.ask = Mock(side_effect=author)
        result = BuildRun.check_suite(instance, suite())
        self.assertEqual(len(controls), 3)
        self.assertEqual(controls[0], controls[1])
        self.assertEqual(result.files[0].content, SOURCE.replace("self._get(), 401", "self._get(), 200"))
        self.assertEqual(instance.counters["build_tests_defect"], 2)
        self.assertIn("replacement_rejected", instance.stages["build_tests_defect_1_file_1"])
        self.assertEqual(instance.stages["build_tests_defect_2_file_1"]["method_patch"]["changed_targets"], ["API.test_auth"])
        self.assertEqual(instance.stages["build_frozen"]["trusted_control"], "passed")

    def test_retained_reference_draft_requires_fresh_checks_and_legacy_dispatch_is_unchanged(self):
        instance = workflow(); value = suite().model_dump()
        instance.bundle["whole_project_seed"] = {"independent_reference_draft": {"suite": value, "suite_digest": digest(value)}}
        instance.test_brief = ""; instance.ask = Mock()
        result = instance._author_independent_suite(["app.py"])
        self.assertEqual(result.model_dump(), value)
        instance.ask.assert_not_called()
        self.assertNotIn("build_frozen", instance.stages)
        self.assertEqual(instance.stages["build_tests"]["reused_unfrozen_reference_draft"], digest(value))
        instance.scoped_repairs = False
        with patch.object(ProjectBuildRun, "_repair_reference_test_files", return_value="legacy") as legacy:
            self.assertEqual(instance._repair_reference_test_files(suite(), [failure()], "old"), "legacy")
            legacy.assert_called_once()

    def test_dom_guidance_follows_authorized_code_not_unrelated_support_helpers(self):
        for uses_dom in (False, True):
            instance = workflow()
            instance.frontend_dom_test_brief = " DOM CONTRACT SENTINEL"
            source = SOURCE.replace("self._get()", "run_vitest(spec)") if uses_dom else SOURCE
            value = suite(source)
            value.files[1].content += "def run_vitest(spec):\n    return 0\n"
            def author(stage, _role, _kind, instruction, **inputs):
                instance.stages[stage] = {}
                self.assertEqual("DOM CONTRACT SENTINEL" in instruction, uses_dom)
                self.assertIn("run_vitest", inputs["read_only_support_files"][0]["content"])
                if stage.startswith("build_tests_diagnose_"):
                    return diagnosis_response(inputs)
                item = next(row for row in inputs["targets"] if row["target"] == "API.test_auth")
                return {"changes": [{"target": item["target"], "expected_original_digest": item["original_digest"],
                                      "replacement_source": item["original_source"]}]}
            instance.ask = Mock(side_effect=author)
            with self.subTest(uses_dom=uses_dom):
                instance._repair_reference_test_files(value, [failure()], "scoped-reference-test-repair-v3")
                self.assertEqual(instance.ask.call_count, 2)


if __name__ == "__main__":
    unittest.main()
