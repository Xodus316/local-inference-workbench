"""Smoke authorship stays bounded without reducing the public contract."""

import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from orchestrator.project_build import ProjectArtifact, ProjectProject, ProjectTestSuite
from orchestrator.store import STAGES, digest
from orchestrator.whole_project import WholeProjectBuildRun
from orchestrator.whole_project_smoke import (INLINE_SMOKE_POLICY, LEGACY_INLINE_SMOKE_POLICY,
                                             SMOKE_OUTPUT_TOKENS, SMOKE_REFERENCE_REPAIR_STAGES,
                                             SMOKE_STAGES, public_smoke_plan, smoke_author_data)
from test_whole_project_sandbox import suite_value


class SmokeProjectionTests(unittest.TestCase):
    def test_actual_routes_and_referenced_types_precede_unrelated_source(self):
        files = [SimpleNamespace(path="aaa/Unrelated.java", content="unrelated" * 100),
                 SimpleNamespace(path="api/UserController.java", content="@RestController class UserController { UserDto register(); }"),
                 SimpleNamespace(path="api/UserDto.java", content="record UserDto(String username) {}")]
        data = smoke_author_data(files, byte_limit=100)
        self.assertEqual(data["implementation_files"], [file.path for file in files])
        self.assertEqual(data["source_projection"]["included_paths"], [files[1].path, files[2].path])
        self.assertEqual(data["source_projection"]["omitted_paths"], [files[0].path])
        self.assertEqual(data["implementation_tree"][0]["content_digest"], digest(files[0].content))
        for item in data["current_source"]:
            self.assertEqual(item["content"], next(file.content for file in files if file.path == item["path"]))
        self.assertLessEqual(data["source_projection"]["utf8_bytes"], 100)

    def test_oversized_source_is_utf8_bounded_and_explicitly_partial(self):
        data = smoke_author_data([SimpleNamespace(path="app.py", content="é" * 100)], byte_limit=21)
        self.assertEqual(data["source_projection"]["truncated_paths"], ["app.py"])
        self.assertEqual(data["current_source"][0]["content"], "é" * 10)
        self.assertEqual(data["source_projection"]["utf8_bytes"], 20)

    def test_plan_preserves_every_public_requirement_without_mutation(self):
        plan = {"goal": "Ship", "acceptance_criteria": ["criterion " + str(n) for n in range(20)],
                "assumptions": ["Keep interpretation explicit"], "implementation_steps": ["pseudocode" * 10000],
                "implementation_files": ["app.py"]}
        projected, metadata = public_smoke_plan(plan)
        self.assertEqual(projected["acceptance_criteria"], plan["acceptance_criteria"])
        self.assertEqual(projected["assumptions"], plan["assumptions"])
        self.assertEqual(metadata["plan_digest"], digest(plan))
        self.assertNotIn("implementation_steps", projected)
        self.assertIn("implementation_steps", plan)

    def test_legacy_projection_is_explicit_and_journal_stages_are_closed(self):
        files = [SimpleNamespace(path="app.py", content="actual source")]
        old = smoke_author_data(files, policy=LEGACY_INLINE_SMOKE_POLICY)
        self.assertEqual(old["source_projection"], {
            "policy": "inline-smoke-tests-v1", "included_paths": ["app.py"], "omitted_paths": [],
            "truncated_paths": [], "utf8_bytes": 13})
        plan = {"goal": "Ship", "acceptance_criteria": ["Works"], "assumptions": [], "implementation_steps": []}
        _, proof = public_smoke_plan(plan, policy=LEGACY_INLINE_SMOKE_POLICY)
        self.assertEqual(proof, {"policy": "inline-smoke-tests-v1", "plan_digest": digest(plan),
                                 "omitted_fields": ["implementation_steps"]})
        self.assertLessEqual(SMOKE_STAGES, STAGES)
        self.assertNotIn("whole_self_reference_repair_3", STAGES)


class SmokeAdmissionTests(unittest.TestCase):
    def workflow(self):
        flow = object.__new__(WholeProjectBuildRun)
        flow.runtime = SimpleNamespace(whole_project_prompt_tokenizers={})
        flow.bundle = {"generation": {"implementer": {"context": SMOKE_OUTPUT_TOKENS + 8000}}}
        flow.brief = {"sources": [{"content": "The entire original public brief"}],
                      "environment": {"api": "http://runtime"}}
        flow.output = {"plan": {"goal": "Ship", "acceptance_criteria": ["criterion " + str(n) for n in range(12)],
                                "assumptions": ["Original assumption"], "implementation_steps": ["wrong pseudocode" * 3000]}}
        flow.stages = {}
        flow.contract = {"test_files": 15}
        flow.scoped_repairs = True

        def generate(stage, *_args, **_kwargs):
            flow.stages[stage] = {}
            return {"value": {"files": []}}

        flow._opencode = Mock(side_effect=generate)
        return flow

    def test_single_call_admits_projection_with_reserved_output_and_full_contract(self):
        flow = self.workflow()
        files = [SimpleNamespace(path="api/app.py", content="@app.get('/api')\ndef route(): pass"),
                 SimpleNamespace(path="other.py", content="x" * 12000)]
        flow._generate("whole_self_tests", "implementer", ProjectTestSuite, "Write compact tests.",
                       smoke_author_data(files), {}, tool_policy=INLINE_SMOKE_POLICY)
        arguments = flow._opencode.call_args.kwargs
        data = json.loads(arguments["payload"]["messages"][1]["content"])
        self.assertEqual(data["sources"], flow.brief["sources"])
        self.assertEqual(data["environment"], flow.brief["environment"])
        self.assertEqual(data["plan"]["acceptance_criteria"], flow.output["plan"]["acceptance_criteria"])
        self.assertEqual(data["plan"]["assumptions"], flow.output["plan"]["assumptions"])
        self.assertNotIn("implementation_steps", data["plan"])
        self.assertEqual(data["current_source"], [{"path": files[0].path, "content": files[0].content}])
        self.assertEqual(data["implementation_files"], [file.path for file in files])
        self.assertNotIn("implementation_tree", data)
        self.assertEqual(data["implementation_inventory"], {"policy": "path-inventory-digest-v1",
                         "digest": digest(smoke_author_data(files)["implementation_tree"]), "file_count": 2})
        self.assertEqual(data["source_projection"]["policy"], INLINE_SMOKE_POLICY)
        self.assertEqual(flow._opencode.call_args.args[3], {})
        self.assertEqual(flow._opencode.call_count, 1)

    def test_scope_and_admission_fail_closed_before_any_model_call(self):
        flow = self.workflow()
        data = smoke_author_data([SimpleNamespace(path="app.py", content="app")])
        with self.assertRaisesRegex(ValueError, "approved stage"):
            flow._generate("build_tests", "implementer", ProjectTestSuite, "Write tests", data, {},
                           tool_policy=INLINE_SMOKE_POLICY)
        flow.bundle["generation"]["implementer"]["context"] = SMOKE_OUTPUT_TOKENS
        with self.assertRaisesRegex(ValueError, "inference was not dispatched"):
            flow._generate("whole_self_tests", "implementer", ProjectTestSuite, "Write tests", data, {},
                           tool_policy=INLINE_SMOKE_POLICY)
        flow._opencode.assert_not_called()

    def test_reference_repairs_preserve_public_contract_without_candidate_source(self):
        previous = {"files": [{"path": "tests/test_api.py", "content": "exact original tests"}], "coverage": []}
        for stage in sorted(SMOKE_REFERENCE_REPAIR_STAGES):
            with self.subTest(stage=stage):
                flow = self.workflow()
                values = {"implementation_files": ["app.py"], "previous_suite": previous,
                          "reference_test_failure": {"test": "Case.test_auth", "message": "missing token"}}
                flow._generate(stage, "implementer", ProjectTestSuite, "Correct only diagnosed invalid tests.",
                               values, {}, tool_policy=INLINE_SMOKE_POLICY)
                call = flow._opencode.call_args
                data = json.loads(call.kwargs["payload"]["messages"][1]["content"])
                self.assertEqual(data["previous_suite"], previous)
                self.assertEqual(data["implementation_files"], ["app.py"])
                self.assertEqual(data["plan"]["acceptance_criteria"], flow.output["plan"]["acceptance_criteria"])
                self.assertEqual(data["source_projection"], {"policy": INLINE_SMOKE_POLICY,
                    "kind": "reference-test-repair-no-candidate-source", "candidate_source_included": False})
                self.assertNotIn("current_source", data)
                self.assertNotIn("implementation_tree", data)
                self.assertEqual(call.args[3], {})

    def test_reference_repairs_reject_source_or_oversized_input_before_dispatch(self):
        stage = sorted(SMOKE_REFERENCE_REPAIR_STAGES)[0]
        for key in ("implementation_tree", "current_source", "source_projection"):
            flow = self.workflow()
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "must not receive candidate source"):
                flow._generate(stage, "implementer", ProjectTestSuite, "Correct tests", {key: []}, {},
                               tool_policy=INLINE_SMOKE_POLICY)
            flow._opencode.assert_not_called()
        flow = self.workflow()
        flow.bundle["generation"]["implementer"]["context"] = SMOKE_OUTPUT_TOKENS
        with self.assertRaisesRegex(ValueError, "reference correction.*inference was not dispatched"):
            flow._generate(stage, "implementer", ProjectTestSuite, "Correct tests",
                           {"implementation_files": ["app.py"], "previous_suite": {}}, {},
                           tool_policy=INLINE_SMOKE_POLICY)
        flow._opencode.assert_not_called()

    def test_author_retry_uses_same_bounded_policy_and_legacy_is_unchanged(self):
        artifact = ProjectArtifact(summary="source", files=[{"path": "app.py", "content": "actual"}])
        for scoped in (True, False):
            with self.subTest(scoped=scoped):
                flow = self.workflow()
                flow.scoped_repairs = scoped
                flow.stages["whole_self_tests"] = {}
                flow._generate = Mock(return_value={})
                with patch("orchestrator.whole_project.validate_self_suite", side_effect=[ValueError("fix suite"), "suite"]):
                    self.assertEqual(flow._author_self_suite(artifact, ["app.py"]), "suite")
                self.assertEqual(flow._generate.call_count, 2)
                for call in flow._generate.call_args_list:
                    self.assertEqual(call.args[5], {} if scoped else {"app.py": "actual"})
                    self.assertEqual(call.kwargs, {"tool_policy": INLINE_SMOKE_POLICY} if scoped else {})

    def test_fixture_retry_receives_exact_rejected_source_and_preserves_test_methods(self):
        flow = self.workflow()
        flow.artifact_type, flow.project_type = ProjectArtifact, ProjectProject
        flow.validate_suite = Mock()
        flow.fixture_problems = Mock(return_value=[])
        flow.stages["whole_self_tests"] = {}
        artifact = ProjectArtifact(summary="source", files=[{"path": "app.py", "content": "actual"}])
        bad = suite_value()
        bad["files"][0]["content"] += (
            "\n    @classmethod\n    def setUpClass(cls):\n        cls.assertEqual(1, 2)\n")
        corrected = {"files": [{**bad["files"][0], "content": bad["files"][0]["content"].replace(
            "cls.assertEqual(1, 2)", "if 1 != 2: raise AssertionError('expected two')")} ]}
        flow._generate = Mock(side_effect=[bad, corrected])
        suite = flow._author_self_suite(artifact, ["app.py"])
        retry = flow._generate.call_args
        self.assertEqual(retry.args[0], "whole_self_tests_retry")
        self.assertEqual(set(retry.args[2].model_json_schema()["properties"]), {"files"})
        self.assertEqual(retry.args[4]["previous_suite"]["files"], bad["files"])
        self.assertNotIn("implementation_files", retry.args[4]["previous_suite"])
        self.assertEqual(retry.args[4]["implementation_files"], ["app.py"])
        self.assertEqual(retry.args[4]["fixture_diagnostics"][0]["kind"], "unbound_class_assertion")
        self.assertEqual(suite.coverage[0].test_name, "SelfCheck.test_value")
        self.assertEqual(flow._generate.call_count, 2)


if __name__ == "__main__":
    unittest.main()
