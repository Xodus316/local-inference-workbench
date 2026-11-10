"""Offline project limits, incremental source windows, approvals and synthetic receipts."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError

from orchestrator.build_models import BuildArtifact, BuildProject
from orchestrator.config import Config
from orchestrator.project_build import (POLICY, ProjectArtifact, ProjectProject, ProjectTestSuite,
    GroupArtifact, RepairArtifact, offline_project_contract, project_enabled, selected_source,
    validate_group_plan, validate_project_artifact, validate_project_prompt, project_executor,
    failure_feedback, GroupPlan)
from orchestrator.runtime import Runtime
from orchestrator.schemas import Request
from orchestrator.store import digest, encoded


COMMAND = "python3 -m unittest discover -s tests -v"
BRIEF = "B" * 9868
TEST = {"path": "tests/test_api.py", "content": (
    "import unittest\nclass API(unittest.TestCase):\n"
    "    def test_api(self):\n        self.assertEqual('observable', 'observable')\n")}
COVERAGE = {"criterion": 1, "test_file": TEST["path"], "test_name": "API.test_api",
            "scenario": "Read a product", "expected_result": "The expected product",
            "catches": "A missing product"}
DIAGNOSIS = {"diagnosis": ["Wrong response"], "changes": ["Correct response"],
             "verification": ["Run frozen tests"]}


def manifest(path, content="source"):
    return {"path": path, "content": content}


def group_plan(paths):
    return {"contract": "The backend serves the API consumed by the frontend", "groups": [
        {"id": "backend", "files": paths[:1], "depends_on": [], "context_files": [],
         "interface": "Provide product API"},
        {"id": "frontend", "files": paths[1:], "depends_on": ["backend"],
         "context_files": paths[:1], "interface": "Call the product API"}]}


class Models:
    def __init__(self):
        self.seen = []
        self.stage = None
        self.overscope = False
        self.tamper = False
        self.lost = False
        self.still_broken = False
        self.test_file = TEST
        self.backend_source = "broken backend"
        self.group_plans = []
        self.group_unknown = False
        self.group_admission_failure = False
        self.group_finish_reason = "stop"
        self.fixture_repairs = []
        self.fixture_unknown = False

    def admit(self, model, settings, payload, stage):
        self.stage = stage
        self.seen.append((stage, model, copy.deepcopy(settings), copy.deepcopy(payload)))
        if stage == "build_project_plan" and self.group_admission_failure:
            raise ValueError("Synthetic context admission failure")

    def complete(self, *args):
        stage = self.stage
        if stage == "build_plan":
            value = {"goal": "Recreate application", "acceptance_criteria": ["Expose product behavior"],
                     "implementation_steps": ["Implement API and UI"], "test_strategy": ["Black-box tests"],
                     "assumptions": [], "risks": []}
        elif stage == "build_tests":
            value = {"implementation_files": ["api/App.java", "frontend/App.vue"], "files": [self.test_file],
                     "commands": [COMMAND], "coverage": [COVERAGE], "assumptions": []}
        elif stage.startswith("build_tests_fixture_"):
            if self.fixture_unknown:
                raise OSError("Synthetic unknown fixture repair receipt")
            value = {"files": [self.fixture_repairs.pop(0) if self.fixture_repairs else self.test_file]}
        elif stage == "build_project_plan" or stage.startswith("build_project_plan_repair_"):
            if self.group_unknown:
                raise ValueError("Synthetic failure before a known terminal receipt")
            value = self.group_plans.pop(0) if self.group_plans else group_plan(["api/App.java", "frontend/App.vue"])
            if isinstance(value, str):
                return {"choices": [{"finish_reason": "stop", "message": {"content": value}}]}
        elif stage == "implement_group_1":
            value = {"summary": "Backend", "files": [manifest("api/App.java", self.backend_source)]}
        elif stage == "implement_group_2":
            if self.lost:
                raise OSError("Synthetic lost receipt")
            value = {"summary": "Frontend", "files": [manifest("frontend/App.vue", "frontend source")]}
        elif stage.endswith("_select"):
            value = {"diagnosis": DIAGNOSIS,
                     "files": ["tests/test_api.py" if self.overscope else "api/App.java"]}
        elif stage.startswith("repair_"):
            value = {"diagnosis": DIAGNOSIS, "artifact": {"summary": "Fixed backend", "files": [
                manifest("tests/test_api.py" if self.tamper else "api/App.java",
                         "still broken" if self.still_broken else "correct backend")]}}
        else:
            raise AssertionError(stage)
        reason = self.group_finish_reason if stage.startswith("build_project_plan") else "stop"
        return {"choices": [{"finish_reason": reason, "message": {"content": json.dumps(value)}}]}


class Executor:
    def __init__(self):
        self.projects = []
        self.tests = []
        self.environment_failure = False

    def preflight(self):
        return {"engine": "docker", "image": "fixture:offline", "image_id": "sha256:" + "a" * 64}

    def materialize(self, run_id, attempt, project):
        self.projects.append(project.model_dump())
        return project

    def test(self, run_id, attempt, project, commands, image, cancel, deadline, read_only_project):
        assert read_only_project
        self.tests.append([f.model_dump() for f in project.files if f.path.startswith("tests/")])
        passed = any(f.path == "api/App.java" and f.content == "correct backend" for f in project.files)
        log = "test_api (test_api.API.test_api) ... " + ("ok" if passed else "FAIL")
        log += "\nRan 1 test in 0.01s\n\n" + ("OK\n" if passed else "FAILED (failures=1)\n")
        return {"status": "passed" if passed else "failed", "commands": [{
            "exit_code": 125 if self.environment_failure else 0 if passed else 1,
            "timed_out": False, "cancelled": False,
            "cleanup_confirmed": True, "command_index": 1, "stdout": "", "stderr": log}]}


class ProjectBoundsTests(unittest.TestCase):
    def test_default_limits_stay_exact_and_project_components_are_independent(self):
        with self.assertRaises(ValidationError):
            BuildArtifact(summary="Default", files=[manifest("a", "a" * 16001)])
        with self.assertRaises(ValidationError):
            BuildProject(summary="Default", files=[manifest("a", "a" * 32001)])
        implementation = [manifest(f"src/{i}", "é" * 8192) for i in range(8)]
        tests = [manifest("tests/test_api.py", "x" * 16384)]
        result = validate_project_artifact({"summary": "Boundary", "files": implementation + tests})
        self.assertIsInstance(result, ProjectProject)
        self.assertEqual(sum(len(f.content.encode()) for f in result.files), 147456)
        for files in (implementation + [manifest("extra", "x")] + tests,
                      implementation + [manifest("tests/test_api.py", "x" * 16385)]):
            with self.assertRaises(ValidationError):
                ProjectProject(summary="Overflow", files=files)
        with self.assertRaises(ValidationError):
            ProjectArtifact(summary="Collision", files=[manifest("x"), manifest("X")])
        with self.assertRaises(ValidationError):
            ProjectArtifact(summary="Test tampering", files=[manifest("Tests/x")])

    def test_file_counts_group_sizes_and_test_bound_remain_enforced(self):
        ProjectArtifact(summary="Paths", files=[manifest(f"x{i}", "") for i in range(128)])
        with self.assertRaises(ValidationError):
            ProjectArtifact(summary="Overflow", files=[manifest(f"x{i}", "") for i in range(129)])
        GroupArtifact(summary="Boundary", files=[manifest("x", "x" * 24576)])
        with self.assertRaises(ValidationError):
            GroupArtifact(summary="Overflow", files=[manifest("x", "x" * 24577)])
        RepairArtifact(summary="Boundary", files=[manifest("x", "x" * 32768)])
        suite = {"implementation_files": [f"src/{i}" for i in range(128)],
                 "files": [{**TEST, "content": TEST["content"] + "#" * (16384 - len(TEST["content"]))}],
                 "commands": [COMMAND], "coverage": [COVERAGE], "assumptions": []}
        ProjectTestSuite.model_validate(suite)
        suite["files"][0]["content"] += "x"
        with self.assertRaises(ValidationError):
            ProjectTestSuite.model_validate(suite)

    def test_group_partition_dependencies_and_source_windows_are_checked(self):
        paths = ["api/App.java", "frontend/App.vue"]
        plan = group_plan(paths)
        validate_group_plan(plan, paths)
        for mutate in (
            lambda p: p["groups"][0].update(depends_on=["frontend"]),
            lambda p: p["groups"][1].update(files=["api/App.java"]),
            lambda p: p["groups"][1].update(context_files=["unknown"]),
            lambda p: p["groups"].pop(),
        ):
            broken = copy.deepcopy(plan)
            mutate(broken)
            with self.assertRaises(ValueError):
                validate_group_plan(broken, paths)
        files = ProjectArtifact(summary="Large", files=[manifest("x", "x" * 32769)]).files
        for selected in (["unknown"], ["x", "x"], ["x"]):
            with self.assertRaises(ValueError):
                selected_source(files, selected, 32768)

    def test_explicit_opt_in_operator_enablement_and_exact_contract(self):
        self.assertIsNone(Request(prompt="Ordinary Build", mode="build").build_profile)
        with self.assertRaises(ValidationError):
            Request(prompt="Wrong mode", mode="fast", build_profile=POLICY)
        bundle = {"request": {"build_profile": POLICY}, "build": {"offline_project_enabled": True},
                  "build_contract": offline_project_contract(),
                  "roles": {"implementer": "fixture:code", "repairer": "fixture:code", "test_author": "fixture:tester"}}
        self.assertTrue(project_enabled(bundle))
        changed = copy.deepcopy(bundle)
        changed["build_contract"]["implementation_bytes"] += 1
        with self.assertRaises(ValueError):
            project_enabled(changed)
        changed = copy.deepcopy(bundle)
        changed["build_contract"]["sandbox_pids_limit"] = 512
        with self.assertRaises(ValueError):
            project_enabled(changed)
        changed = copy.deepcopy(bundle)
        changed["build_contract"]["sandbox_nofile"] = 2048
        with self.assertRaises(ValueError):
            project_enabled(changed)
        changed = copy.deepcopy(bundle)
        changed["build_contract"]["group_plan_format_repairs"] = 3
        with self.assertRaises(ValueError):
            project_enabled(changed)
        changed = copy.deepcopy(bundle)
        changed["build"]["offline_project_enabled"] = False
        with self.assertRaises(ValueError):
            project_enabled(changed)

    def test_prompt_guard_counts_entire_serialized_stage_and_schema_without_truncation(self):
        payload = {"messages": [{"role": "system", "content": "instructions"},
                    {"role": "user", "content": encoded({"brief": BRIEF, "tests": TEST,
                                                        "source": "x" * 24576, "plan": "full plan"})}],
                   "schema": {"type": "object", "description": "s" * 2000}}
        before = copy.deepcopy(payload)
        bundle = {"role_profiles": {"implementer": {"context": 98304}}}
        required = validate_project_prompt(payload, bundle, "implementer")
        self.assertGreater(required, 9868 + 24576 + 2000 + 2048)
        bundle["role_profiles"]["implementer"]["context"] = required - 1
        with self.assertRaisesRegex(ValueError, "Complete project prompt"):
            validate_project_prompt(payload, bundle, "implementer")
        self.assertEqual(payload, before)

    def test_failure_feedback_labels_utf8_tails_and_preserves_full_receipt(self):
        receipt = {"status": "failed", "commands": [{"exit_code": 1, "stdout": "é" * 3000,
                                                       "stderr": "🙂" * 2000 + "end"}]}
        original = copy.deepcopy(receipt)
        feedback = failure_feedback(receipt)
        self.assertEqual(receipt, original)
        self.assertEqual(feedback["full_failure_receipt_digest"], digest(receipt))
        command = feedback["commands"][0]
        for key, maximum in (("stdout", 2000), ("stderr", 5000)):
            metadata = command["output_feedback"][key]
            self.assertTrue(metadata["truncated"])
            self.assertEqual(metadata["original_utf8_bytes"], len(receipt["commands"][0][key].encode()))
            self.assertEqual(metadata["included_utf8_bytes"], len(command[key].encode()))
            self.assertLessEqual(metadata["included_utf8_bytes"], maximum)


class ProjectFlowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.models, self.executor = Models(), Executor()
        self.runtime = Runtime(Path(temp.name), Config(build={"offline_project_enabled": True}),
                               self.models, self.executor)
        self.addCleanup(self.runtime.close)

    def execute(self):
        row = self.runtime.prepare({"mode": "build", "prompt": BRIEF, "build_profile": POLICY})
        self.assertEqual(self.models.seen, [])
        self.runtime.approve(row["id"], row["digest"])
        self.runtime.queue.join()
        return self.runtime.store.get(row["id"])

    def test_groups_selected_repair_full_verification_and_no_mutation_claim(self):
        row = self.execute()
        self.assertEqual(row["status"], "completed", row.get("result"))
        self.assertEqual([c["stage"] for c in row["calls"]], ["build_plan", "build_tests", "build_project_plan",
            "implement_group_1", "implement_group_2", "repair_1_select", "repair_1"])
        output = row["result"]["output"]
        self.assertEqual(output["verification"]["mutation_verification"], "not_run")
        self.assertEqual(output["verification"]["strength"][0]["policy"], "unittest-acceptance-v1")
        self.assertEqual(output["repair_attempts"], 1)
        self.assertEqual(self.executor.tests, [[TEST], [TEST]])
        self.assertEqual(len(output["artifact"]["files"]), 3)
        for stage, model, settings, payload in self.models.seen:
            data = json.loads(payload["messages"][1]["content"])
            self.assertEqual(data["prompt"], BRIEF)
            if stage.startswith(("implement_", "repair_")):
                self.assertEqual(data["frozen_tests"]["files"], [TEST])
                self.assertEqual(data["plan"], output["plan"])
                self.assertEqual(settings["context"], 98304)
                self.assertEqual(settings["think"], "xhigh")
                self.assertIsNone(settings["output_tokens"])
            if stage == "implement_group_1":
                self.assertEqual(data["selected_source"], [])
            if stage == "implement_group_2":
                self.assertEqual(data["selected_source"], [manifest("api/App.java", "broken backend")])
            if stage == "repair_1":
                self.assertEqual(data["selected_source"], [manifest("api/App.java", "broken backend")])
                self.assertNotIn("frontend source", payload["messages"][1]["content"])

    def test_repair_selection_and_replacement_cannot_escape_frozen_scope(self):
        for field in ("overscope", "tamper"):
            with self.subTest(field=field):
                setattr(self.models, field, True)
                row = self.execute()
                self.assertEqual(row["status"], "failed")
                self.assertIsNone(row["result"]["output"]["artifact"])
                self.assertEqual(len(self.executor.tests), 1)
                setattr(self.models, field, False)
                self.models.seen.clear()
                self.executor.tests.clear()

    def test_unknown_native_completion_is_not_replayed(self):
        self.models.lost = True
        row = self.execute()
        self.assertEqual(row["status"], "blocked")
        self.assertEqual([stage for stage, *_ in self.models.seen].count("implement_group_2"), 1)
        self.assertEqual(self.executor.tests, [])

    def test_complete_large_suite_and_source_remain_within_98k_without_truncation(self):
        test_file = {**TEST, "content": TEST["content"] + "#" * (16384 - len(TEST["content"]))}
        self.models.test_file = test_file
        self.models.backend_source = "x" * 24576
        row = self.execute()
        self.assertEqual(row["status"], "completed", row.get("result"))
        for stage, _, _, payload in self.models.seen:
            if stage in {"implement_group_2", "repair_1"}:
                data = json.loads(payload["messages"][1]["content"])
                self.assertEqual(data["prompt"], BRIEF)
                self.assertEqual(data["frozen_tests"]["files"], [test_file])
                self.assertEqual(data["selected_source"], [manifest("api/App.java", "x" * 24576)])
                required = validate_project_prompt(payload, row["bundle"],
                                                   "implementer" if stage.startswith("implement") else "repairer")
                self.assertLessEqual(required, 98304)
        self.assertGreater(sum(len(f["content"]) for f in self.executor.projects[0]["files"]), 32000)

    def test_repairs_are_bounded_and_environment_failure_does_not_trigger_repair(self):
        self.models.still_broken = True
        row = self.execute()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["result"]["output"]["repair_attempts"], 3)
        self.assertEqual(len([s for s, *_ in self.models.seen if s.endswith("_select")]), 3)
        self.assertEqual(len(self.executor.tests), 4)
        self.models.seen.clear()
        self.executor.tests.clear()
        self.models.still_broken = False
        self.executor.environment_failure = True
        row = self.execute()
        self.assertEqual(row["status"], "review_required")
        self.assertEqual(row["result"]["output"]["repair_attempts"], 0)

    def test_complete_prompt_overflow_stops_before_provider_admission(self):
        row = self.runtime.prepare({"mode": "build", "prompt": BRIEF, "build_profile": POLICY,
            "sources": [{"id": "oversized", "text": "x" * 40000}]})
        self.runtime.approve(row["id"], row["digest"])
        self.runtime.queue.join()
        row = self.runtime.store.get(row["id"])
        self.assertEqual(row["status"], "failed")
        self.assertEqual(self.models.seen, [])
        self.assertEqual(row["calls"], [])

    def test_operator_profile_change_invalidates_prepared_approval(self):
        row = self.runtime.prepare({"mode": "build", "prompt": BRIEF, "build_profile": POLICY})
        self.runtime.config.build.offline_project_enabled = False
        with self.assertRaises(ValueError):
            self.runtime.approve(row["id"], row["digest"])
        self.assertEqual(self.models.seen, [])

    def test_invalid_group_id_gets_one_explicit_correction_before_freeze(self):
        invalid = group_plan(["api/App.java", "frontend/App.vue"])
        invalid["groups"][0]["id"] = "Backend Group"
        corrected = group_plan(["api/App.java", "frontend/App.vue"])
        corrected["groups"][0]["id"] = "service_api"
        corrected["groups"][1]["depends_on"] = ["service_api"]
        self.models.group_plans = [invalid, corrected]
        row = self.execute()
        self.assertEqual(row["status"], "completed", row.get("result"))
        stages = [call["stage"] for call in row["calls"]]
        self.assertLess(stages.index("build_project_plan_repair_1"), stages.index("implement_group_1"))
        frozen = row["result"]["stages"]["build_project_frozen"]
        self.assertEqual(frozen["format_repairs"], 1)
        self.assertEqual(frozen["group_plan"]["groups"][0]["id"], "service_api")
        initial = next(payload for stage, _, _, payload in self.models.seen if stage == "build_project_plan")
        repair = next(payload for stage, _, _, payload in self.models.seen if stage == "build_project_plan_repair_1")
        data, original = json.loads(repair["messages"][1]["content"]), json.loads(initial["messages"][1]["content"])
        feedback = data.pop("group_plan_feedback")
        self.assertEqual(data, original)
        self.assertEqual(repair["schema"], initial["schema"])
        self.assertIn("groups.0.id", feedback["validation_error"])
        self.assertEqual(feedback["rejected_plan"], invalid)

    def fixture_sources(self):
        from test_project_fixture_checks import HTTP, FIXED_HTTP
        original = {**TEST, "content": HTTP.replace("test_delete", "test_api")}
        revised = {**TEST, "content": FIXED_HTTP.replace("test_delete", "test_api")}
        return original, revised

    def test_fixture_repair_is_independent_and_completes_before_freeze_and_implementation(self):
        self.models.test_file, revised = self.fixture_sources()
        self.models.fixture_repairs = [revised]
        self.runtime.config.build.max_test_fixture_repairs = 0
        row = self.execute()
        self.assertEqual(row["status"], "completed", row.get("result"))
        stages = [call["stage"] for call in row["calls"]]
        self.assertLess(stages.index("build_tests_fixture_1"), stages.index("build_project_plan"))
        self.assertEqual(row["result"]["output"]["suite"]["files"], [revised])
        repair = next(payload for stage, _, _, payload in self.models.seen if stage == "build_tests_fixture_1")
        data = json.loads(repair["messages"][1]["content"])
        self.assertEqual(data["prompt"], BRIEF)
        self.assertEqual(data["files"], [self.models.test_file])
        self.assertEqual(data["plan"], row["result"]["output"]["plan"])
        self.assertIn("complete syntax tree", repair["messages"][0]["content"])
        self.assertEqual(self.runtime.config.build.max_test_fixture_repairs, 0)
        self.assertEqual(self.executor.tests, [[revised], [revised]])

    def test_two_unresolved_fixture_repairs_exhaust_without_freezing_or_implementation(self):
        self.models.test_file, _ = self.fixture_sources()
        row = self.execute()
        self.assertEqual(row["status"], "failed")
        self.assertIn("fixture repair budget exhausted", row["result"]["output"]["error"]["message"])
        self.assertEqual([c["stage"] for c in row["calls"]],
                         ["build_plan", "build_tests", "build_tests_fixture_1", "build_tests_fixture_2"])
        self.assertNotIn("build_frozen", row["result"]["stages"])
        self.assertIsNone(row["result"]["output"]["suite"])
        self.assertEqual(self.executor.tests, [])

    def test_unknown_fixture_repair_does_not_authorize_a_second_call(self):
        self.models.test_file, _ = self.fixture_sources()
        self.models.fixture_unknown = True
        row = self.execute()
        self.assertEqual(row["status"], "blocked")
        self.assertEqual([c["stage"] for c in row["calls"]],
                         ["build_plan", "build_tests", "build_tests_fixture_1"])
        self.assertEqual(self.executor.tests, [])

    def test_group_schema_error_inside_ask_still_uses_terminal_receipt_for_correction(self):
        invalid = group_plan(["api/App.java", "frontend/App.vue"])
        invalid["groups"][0]["id"] = "Invalid ID"
        self.models.group_plans = [invalid]
        original = self.runtime._build_model
        def checked(*args):
            value = original(*args)
            if args[1] == "build_project_plan":
                GroupPlan.model_validate(value)
            return value
        with patch.object(self.runtime, "_build_model", side_effect=checked):
            row = self.execute()
        self.assertEqual(row["status"], "completed", row.get("result"))
        self.assertTrue(any(call["stage"] == "build_project_plan_repair_1" for call in row["calls"]))

    def test_malformed_group_json_can_be_corrected_without_changing_prior_output(self):
        self.models.group_plans = ["not a JSON object"]
        row = self.execute()
        self.assertEqual(row["status"], "completed", row.get("result"))
        payload = next(p for stage, _, _, p in self.models.seen if stage == "build_project_plan_repair_1")
        self.assertEqual(json.loads(payload["messages"][1]["content"])["group_plan_feedback"]["rejected_content"],
                         "not a JSON object")

    def test_invalid_scope_and_dependencies_are_fully_revalidated_after_each_correction(self):
        paths = ["api/App.java", "frontend/App.vue"]
        first, second = group_plan(paths), group_plan(paths)
        first["groups"][0]["files"] = ["outside/undeclared.java"]
        second["groups"][0]["depends_on"] = ["frontend"]
        self.models.group_plans = [first, second, group_plan(paths)]
        row = self.execute()
        self.assertEqual(row["status"], "completed", row.get("result"))
        self.assertEqual(row["result"]["stages"]["build_project_frozen"]["format_repairs"], 2)
        self.assertEqual([g["files"] for g in row["result"]["output"]["group_plan"]["groups"]],
                         [[paths[0]], [paths[1]]])

    def test_group_format_repair_budget_exhausts_without_freezing_or_implementation(self):
        invalid = group_plan(["api/App.java", "frontend/App.vue"])
        invalid["groups"][1]["context_files"] = ["tests/test_api.py"]
        self.models.group_plans = [copy.deepcopy(invalid) for _ in range(3)]
        row = self.execute()
        self.assertEqual(row["status"], "failed")
        self.assertIn("budget exhausted", row["result"]["output"]["error"]["message"])
        self.assertEqual([c["stage"] for c in row["calls"] if c["stage"].startswith("build_project_plan")],
                         ["build_project_plan", "build_project_plan_repair_1", "build_project_plan_repair_2"])
        self.assertNotIn("build_project_frozen", row["result"]["stages"])
        self.assertFalse(any(c["stage"].startswith("implement_") for c in row["calls"]))

    def test_no_group_correction_after_unknown_native_or_admission_failure(self):
        self.models.group_unknown = True
        row = self.execute()
        self.assertEqual(row["status"], "blocked")
        self.assertFalse(any(c["stage"].startswith("build_project_plan_repair_") for c in row["calls"]))

    def test_no_group_correction_after_context_admission_failure(self):
        self.models.group_admission_failure = True
        row = self.execute()
        self.assertEqual(row["status"], "failed")
        self.assertFalse(any(c["stage"].startswith("build_project_plan") for c in row["calls"]))

    def test_unrelated_value_error_on_valid_terminal_plan_does_not_authorize_correction(self):
        original = self.runtime._build_model
        def interrupted(*args):
            value = original(*args)
            if args[1] == "build_project_plan":
                raise ValueError("Synthetic unrelated failure after a valid response")
            return value
        with patch.object(self.runtime, "_build_model", side_effect=interrupted):
            row = self.execute()
        self.assertEqual(row["status"], "failed")
        self.assertFalse(any(c["stage"].startswith("build_project_plan_repair_") for c in row["calls"]))

    def test_nonstop_group_response_does_not_authorize_correction(self):
        self.models.group_finish_reason = "length"
        invalid = group_plan(["api/App.java", "frontend/App.vue"])
        invalid["groups"][0]["id"] = "Invalid ID"
        self.models.group_plans = [invalid]
        row = self.execute()
        self.assertEqual(row["status"], "failed")
        self.assertFalse(any(c["stage"].startswith("build_project_plan_repair_") for c in row["calls"]))

    def test_unrelated_binding_error_on_invalid_terminal_plan_does_not_authorize_correction(self):
        invalid = group_plan(["api/App.java", "frontend/App.vue"])
        invalid["groups"][0]["id"] = "Invalid ID"
        self.models.group_plans = [invalid]
        original = self.runtime._build_model
        def interrupted(*args):
            value = original(*args)
            if args[1] == "build_project_plan":
                raise ValueError("Saved model response belongs to different inputs")
            return value
        with patch.object(self.runtime, "_build_model", side_effect=interrupted):
            row = self.execute()
        self.assertEqual(row["status"], "failed")
        self.assertFalse(any(c["stage"].startswith("build_project_plan_repair_") for c in row["calls"]))

    def test_correction_prompt_overflow_stops_without_spending_another_model_call(self):
        self.models.group_plans = ["x" * 30000]
        row = self.execute()
        self.assertEqual(row["status"], "failed")
        self.assertIn("Complete project prompt", row["result"]["output"]["error"]["message"])
        self.assertFalse(any(c["stage"].startswith("build_project_plan_repair_") for c in row["calls"]))

    def test_unused_new_defaults_preserve_ordinary_historical_config_binding(self):
        self.runtime.config.build.offline_project_enabled = False
        row = self.runtime.prepare({"mode": "build", "prompt": "Ordinary project"})
        previous = self.runtime.config.model_dump()
        previous["build"].pop("offline_project_enabled")
        previous["build"].pop("temporary_mb")
        old_bundle = {**row["bundle"], "config_digest": digest(previous)}
        self.assertTrue(self.runtime._config_matches(old_bundle))
        self.runtime.config.build.temporary_mb = 128
        self.assertFalse(self.runtime._config_matches(old_bundle))

    def test_operator_disabled_profile_rejects_before_model_dispatch(self):
        self.runtime.config.build.offline_project_enabled = False
        with self.assertRaisesRegex(ValueError, "operator enablement"):
            self.runtime.prepare({"mode": "build", "prompt": BRIEF, "build_profile": POLICY})
        self.assertEqual(self.models.seen, [])

    def test_profile_sandbox_resources_are_effective_without_mutating_operator_defaults(self):
        from orchestrator.build_sandbox import DockerBuildExecutor
        row = self.runtime.prepare({"mode": "build", "prompt": BRIEF, "build_profile": POLICY})
        self.runtime.build_executor = DockerBuildExecutor(self.runtime.store.directory, self.runtime.config.build)
        original = self.runtime.config.build.model_dump()
        executor = project_executor(self.runtime, {"run_id": row["id"]})
        self.assertEqual(executor.config.memory_mb, 16384)
        self.assertEqual(executor.config.temporary_mb, 2048)
        self.assertTrue(executor.config.temporary_noexec)
        self.assertEqual(executor.config.open_files, 1024)
        self.assertEqual(executor.config.process_limit, 256)
        self.assertEqual(self.runtime.config.build.model_dump(), original)
        artifact = ProjectProject(summary="Synthetic project", files=[manifest("api/App.java"), TEST])
        directory = executor.materialize(row["id"], 0, artifact)
        executor._bounded_container = Mock(return_value={"exit_code": 0, "timed_out": False,
            "cancelled": False, "cleanup_confirmed": True, "stdout": "", "stderr": ""})
        executor.test(row["id"], 0, directory, [COMMAND], image="sha256:" + "a" * 64)
        args = executor._bounded_container.call_args.args[0]
        for option, expected in (("--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=2048m"),
                                 ("--memory", "16384m"), ("--memory-swap", "16384m"),
                                 ("--pids-limit", "256"), ("--ulimit", "nofile=1024:1024"),
                                 ("--network", "none")):
            self.assertEqual(args[args.index(option) + 1], expected)
        self.assertIn("--read-only", args)
        self.assertIn("target=/workspace,readonly", args[args.index("--mount") + 1])
        ordinary = self.runtime.build_executor
        ordinary._bounded_container = executor._bounded_container
        ordinary.test(row["id"], 0, directory, [COMMAND], image="sha256:" + "a" * 64)
        ordinary_args = ordinary._bounded_container.call_args.args[0]
        self.assertEqual(ordinary_args[ordinary_args.index("--memory") + 1], "1024m")
        self.assertEqual(ordinary_args[ordinary_args.index("--ulimit") + 1], "nofile=256:256")
        self.assertEqual(ordinary_args[ordinary_args.index("--pids-limit") + 1], "128")
        self.assertEqual(ordinary_args[ordinary_args.index("--tmpfs") + 1], "/tmp:rw,nosuid,nodev,size=64m")


if __name__ == "__main__":
    unittest.main()
