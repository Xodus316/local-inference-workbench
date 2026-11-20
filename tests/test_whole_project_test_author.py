"""A numbering mistake cannot erase tests or fabricate missing acceptance coverage."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from orchestrator.project_build import ProjectTestSuite
from orchestrator.project_opencode_whole import OpenCodeSettledResponseError
from orchestrator.store import Store, digest
from orchestrator.whole_project import WholeProjectBuildRun
from orchestrator.whole_project_test_author import (AUTHOR_RESTART_STAGE, INLINE_AUTHOR_RESTART_POLICY,
                                                    IndependentCoverageError, IndependentCoveragePatch,
                                                    public_test_author_plan, validate_coverage_correction,
                                                    validate_coverage_indices)


CRITERIA = ["Public behavior " + str(index) for index in range(1, 13)]


def draft():
    indices = [1, *range(3, 14)]
    return {"implementation_files": ["app.py"], "commands": ["python3 -m unittest discover -s tests -v"],
            "files": [{"path": "tests/test_existing.py", "content": "import unittest\nclass Existing(unittest.TestCase):\n"
                       + "".join(f"    def test_{index}(self):\n        self.assertEqual({index}, {index})\n"
                                 for index in indices)}], "assumptions": [],
            "coverage": [{"criterion": index, "test_file": "tests/test_existing.py",
                          "test_name": f"Existing.test_{index}", "scenario": "Existing public scenario",
                          "expected_result": "Existing observable result", "catches": "Existing regression"}
                         for index in indices]}


def correction(value):
    result = deepcopy(value)
    next(row for row in result["coverage"] if row["criterion"] == 13)["criterion"] = 12
    result["files"].append({"path": "tests/test_security.py", "content": (
        "import os, unittest, urllib.request, urllib.error\n"
        "class Security(unittest.TestCase):\n"
        "    def test_unauthorized(self):\n"
        "        with self.assertRaises(urllib.error.HTTPError) as failure:\n"
        "            urllib.request.urlopen(os.environ['API_BASE_URL'] + '/products', timeout=10)\n"
        "        self.assertIn(failure.exception.code, (401, 403))\n")})
    result["coverage"].append({"criterion": 2, "test_file": "tests/test_security.py",
                               "test_name": "Security.test_unauthorized", "scenario": "Unauthenticated protected GET",
                               "expected_result": "Authorization is denied", "catches": "Anonymous protected access"})
    return result


def correction_patch(value):
    revised = correction(value)
    return {"files": revised["files"][len(value["files"]):],
            "coverage": revised["coverage"][len(value["coverage"]):],
            "remaps": [{"coverage_index": index, "from_criterion": row["criterion"], "to_criterion": 12}
                       for index, row in enumerate(value["coverage"]) if row["criterion"] == 13]}


def flow(value=None, *, scoped=True, retained=False):
    instance = object.__new__(WholeProjectBuildRun)
    instance.scoped_repairs = scoped
    instance.suite_type = ProjectTestSuite
    instance.output = {"plan": {"goal": "Public contract", "acceptance_criteria": CRITERIA,
        "assumptions": ["Public assumption"], "implementation_files": ["app.py"],
        "implementation_steps": ["STALE NARRATIVE" * 1000], "test_strategy": ["STALE STRATEGY"], "risks": []}}
    instance.bundle = ({"whole_project_seed": {"independent_test_draft": {
        "suite": value, "source_draft_digest": digest(value)}}} if retained else {})
    instance.test_brief = "Author independent tests."
    instance.contract = {"test_bytes": 98304, "test_files": 15}
    instance.stages = {}

    def author(stage, *_args, **_kwargs):
        instance.stages[stage] = {}
        return deepcopy(value if stage == "build_tests" else correction_patch(value))

    instance.ask = Mock(side_effect=author)
    return instance


class CoverageCorrectionTests(unittest.TestCase):
    def test_exact_observed_gap_requires_new_test_and_preserves_report_obligation(self):
        original = draft()
        workflow = flow(original)
        result = workflow._author_independent_suite(["app.py"])
        self.assertEqual([call.args[0] for call in workflow.ask.call_args_list], ["build_tests", "build_tests_retry"])
        request = workflow.ask.call_args_list[-1].kwargs
        self.assertEqual(request["existing_files"], original["files"])
        self.assertEqual(request["existing_coverage"], original["coverage"])
        self.assertEqual(request["previous_suite_digest"], digest(original))
        self.assertTrue(issubclass(workflow.ask.call_args.args[2], IndependentCoveragePatch))
        self.assertEqual(request["coverage_validation"]["missing_criteria"], [2])
        self.assertEqual(request["coverage_validation"]["unknown_criteria"], [13])
        self.assertEqual(result.files[0].model_dump(), original["files"][0])
        self.assertEqual({entry.criterion for entry in result.coverage}, set(range(1, 13)))
        self.assertEqual(len([entry for entry in result.coverage if entry.criterion == 12]), 2)
        self.assertEqual(result.coverage[-1].test_name, "Security.test_unauthorized")
        self.assertEqual(workflow.stages["build_tests_retry"]["coverage_correction"]["corrected_suite_digest"],
                         digest(result.model_dump()))

    def test_correction_cannot_drop_relabel_weaken_or_remove_existing_obligations(self):
        original = ProjectTestSuite.model_validate(draft())
        cases = []
        dropped = correction(draft()); dropped["coverage"].pop(-2); cases.append(dropped)
        relabelled = correction(draft()); relabelled["coverage"][-2]["criterion"] = 2; cases.append(relabelled)
        modified = correction(draft()); modified["files"][0]["content"] += "# changed\n"; cases.append(modified)
        removed = correction(draft()); removed["files"].pop(0); cases.append(removed)
        rewritten = correction(draft()); rewritten["coverage"][0]["expected_result"] = "Always passes"; cases.append(rewritten)
        changed_command = correction(draft()); changed_command["commands"] = ["python -m unittest discover -s tests -v"]; cases.append(changed_command)
        fake = correction(draft()); fake["files"].pop(); fake["coverage"][-1].update(
            test_file="tests/test_existing.py", test_name="Existing.test_13"); cases.append(fake)
        nonexistent = correction(draft()); nonexistent["coverage"][-1]["test_name"] = "Security.test_missing"; cases.append(nonexistent)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_coverage_correction(original, ProjectTestSuite.model_validate(value), CRITERIA)

    def test_missing_only_and_unknown_only_have_lossless_corrections(self):
        for missing, unknown in ((True, False), (False, True)):
            value = draft()
            if not unknown:
                value["coverage"][-1]["criterion"] = 12
            if not missing:
                extra = correction(draft())
                value["files"].append(extra["files"][-1])
                value["coverage"].append(extra["coverage"][-1])
            with self.subTest(missing=missing):
                with self.assertRaises(IndependentCoverageError):
                    validate_coverage_indices(ProjectTestSuite.model_validate(value), CRITERIA)
                revised = deepcopy(value)
                if unknown:
                    revised["coverage"][-2]["criterion"] = 12
                else:
                    revised["files"].append(correction(draft())["files"][-1])
                    revised["coverage"].append(correction(draft())["coverage"][-1])
                self.assertEqual(validate_coverage_correction(ProjectTestSuite.model_validate(value),
                    ProjectTestSuite.model_validate(revised), CRITERIA).model_dump(), revised)

    def test_retained_draft_dispatches_only_correction_and_no_old_pass_is_credited(self):
        value = draft()
        workflow = flow(value, retained=True)
        result = workflow._author_independent_suite(["app.py"])
        self.assertEqual([call.args[0] for call in workflow.ask.call_args_list], ["build_tests_retry"])
        self.assertEqual(workflow.stages["build_tests"]["reused_untrusted_draft"], digest(value))
        self.assertNotIn("build_frozen", workflow.stages)
        self.assertEqual(result.model_dump(), correction(value))
        valid = flow(correction(value), retained=True)
        with self.assertRaisesRegex(ValueError, "does not require"):
            valid._author_independent_suite(["app.py"])
        valid.ask.assert_not_called()

    def test_invalid_correction_and_unknown_dispatch_are_not_retried(self):
        workflow = flow(draft())
        def invalid(stage, *_args, **_kwargs):
            workflow.stages[stage] = {}
            return draft()
        workflow.ask.side_effect = invalid
        with self.assertRaises(ValueError):
            workflow._author_independent_suite(["app.py"])
        self.assertEqual(workflow.ask.call_count, 2)
        unknown = flow(draft())
        unknown.ask.side_effect = RuntimeError("native_completion_unknown")
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            unknown._author_independent_suite(["app.py"])
        unknown.ask.assert_called_once()

    def test_legacy_manifest_behavior_is_unchanged(self):
        workflow = flow(draft(), scoped=False)
        result = workflow._author_independent_suite(["app.py"])
        self.assertEqual(result.model_dump(), draft())
        workflow.ask.assert_called_once()



class AuthorContextTests(unittest.TestCase):
    def test_settled_authoring_restarts_once_and_reuses_the_journal_on_reentry(self):
        workflow = flow()
        del workflow.ask  # Exercise real dispatch and durable call records.
        workflow.brief = {"prompt": "Public brief", "sources": []}
        settings = {"context": 98304, "think": "xhigh", "output_tokens": 65536}
        identity = {"model_digest": "fixture-digest"}
        workflow.bundle.update(generation={"test_author": settings}, roles={"test_author": "fixture"},
                               build_contract=workflow.contract)
        workflow._worker_identity = Mock(return_value=(identity, settings))
        proof = {"session_id": "completed-session", "native_receipts_digest": "a" * 64,
                 "completed_requests": 3, "scratch_cleanup_confirmed": True}

        def generate(*, stage, visible_files, tool_policy=None, **_arguments):
            self.assertEqual(visible_files, {})
            if stage == "build_tests":
                raise OpenCodeSettledResponseError("unverified-wire-format", proof)
            if stage == AUTHOR_RESTART_STAGE:
                self.assertEqual(tool_policy, INLINE_AUTHOR_RESTART_POLICY)
            value = draft() if stage == AUTHOR_RESTART_STAGE else correction_patch(draft())
            return {"value": value, "receipt": {"stage": stage, "role": "test_author", "worker": identity,
                "context": 98304, "thinking": "xhigh", "model_digest": identity["model_digest"],
                "tool_policy": tool_policy, "resident": {"digest": identity["model_digest"], "context_length": 98304}}}

        worker = Mock()
        worker.generate.side_effect = generate
        workflow.workers = {"test_author": worker}
        with TemporaryDirectory() as directory:
            store = Store(Path(directory))
            try:
                run_id = store.create(workflow.bundle)
                workflow.state = {"run_id": run_id}
                workflow.runtime = SimpleNamespace(store=store, _guard=Mock(), inference_lock=Lock(),
                    events={run_id: Event()}, deadline=float("inf"), whole_project_prompt_tokenizers={})
                self.assertEqual(workflow._author_independent_suite(["app.py"]).model_dump(), correction(draft()))
                self.assertFalse(store.has_unknown_calls())
                self.assertEqual(store.cached(run_id, "build_tests"), {"dispatch_status": "completed_rejected",
                    "rejection_reason": "unverified-wire-format", "settled_response_proof": proof})
                self.assertEqual([call["stage"] for call in store.get(run_id)["calls"]],
                                 ["build_tests", AUTHOR_RESTART_STAGE, "build_tests_retry"])
                self.assertEqual(workflow.stages["build_tests"]["recovery"]["decision"], "restart")
                self.assertEqual(workflow._author_independent_suite(["app.py"]).model_dump(), correction(draft()))
                self.assertEqual(worker.generate.call_count, 3)
            finally:
                store.close()

    def test_real_generation_projects_only_independent_author_and_retains_public_context(self):
        workflow = flow(draft())
        workflow.brief = {"prompt": "ALL PUBLIC REQUIREMENTS", "sources": [{"content": "PUBLIC HARNESS CONTRACT"}]}
        workflow.runtime = SimpleNamespace(whole_project_prompt_tokenizers={})
        workflow.bundle["generation"] = {"test_author": {"context": 98304}, "planner": {"context": 98304}}
        workflow._opencode = Mock(return_value={"value": draft()})
        with patch("orchestrator.whole_project.validate_project_prompt", return_value=100):
            workflow._generate("build_tests", "test_author", ProjectTestSuite, "Write tests", {}, {})
        request = workflow._opencode.call_args.kwargs
        inputs = json.loads(request["payload"]["messages"][1]["content"])
        projected, proof = public_test_author_plan(workflow.output["plan"])
        self.assertEqual(inputs["plan"], projected)
        self.assertEqual(inputs["test_author_plan_projection"], proof)
        self.assertEqual([row["requirement"] for row in inputs["numbered_acceptance_criteria"]], CRITERIA)
        self.assertEqual([row["criterion"] for row in inputs["numbered_acceptance_criteria"]], list(range(1, 13)))
        self.assertEqual(inputs["prompt"], workflow.brief["prompt"])
        self.assertEqual(inputs["sources"], workflow.brief["sources"])
        self.assertNotIn("STALE NARRATIVE", json.dumps(inputs))
        self.assertEqual(workflow._opencode.call_args.args[3], {})
        for scoped, role in ((False, "test_author"), (True, "planner")):
            workflow.scoped_repairs = scoped
            with patch("orchestrator.whole_project.validate_project_prompt", return_value=100):
                workflow._generate("build_tests", role, ProjectTestSuite, "Write tests", {}, {})
            inputs = json.loads(workflow._opencode.call_args.kwargs["payload"]["messages"][1]["content"])
            self.assertEqual(inputs["plan"], workflow.output["plan"])
            self.assertNotIn("test_author_plan_projection", inputs)


if __name__ == "__main__":
    unittest.main()
