"""Smoke reference failures are test repairs, never candidate repair evidence."""

from collections import Counter
from copy import deepcopy
import json
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from orchestrator.project_build import ProjectArtifact, ProjectProject, ProjectTestSuite
from orchestrator.project_test_lint import defects
from orchestrator.store import digest
from orchestrator.wire import wire_schema
from orchestrator.whole_project_smoke import INLINE_SMOKE_POLICY
from orchestrator.whole_project import WholeProjectBuildRun
from orchestrator.stage_recovery import KEY, DYNAMIC_POLICY, StageRecovery
from orchestrator.whole_project_smoke_reference import (reference_smoke_diagnostics,
    validate_smoke_reference, validate_smoke_reference_correction)


IMAGE = "sha256:" + "a" * 64
SOURCE = ("import unittest\ndef request(token=None): return 201 if token else 403\n"
          "class Smoke(unittest.TestCase):\n"
          "    def test_product(self):\n        status = request()\n        self.assertEqual(status, 201)\n"
          "    def test_clicks(self):\n        docs = [1, 2]\n"
          "        for count in docs: self.assertEqual(count, 2)\n")


def suite(source=SOURCE):
    return ProjectTestSuite.model_validate({"implementation_files": ["app.py"],
        "commands": ["python3 -m unittest discover -s tests -v"], "assumptions": [],
        "files": [{"path": "tests/test_smoke.py", "content": source}], "coverage": [
            {"criterion": i, "test_file": "tests/test_smoke.py", "test_name": "Smoke." + name,
             "scenario": "Exercise public behavior", "expected_result": "Required result", "catches": "Wrong result"}
            for i, name in enumerate(("test_product", "test_clicks"), 1)]})


def summary(failed=("test_product",)):
    rows = [{"test": name + " (test_smoke.Smoke)", "status": "failed", "assertion": "403 != 201"} for name in failed]
    return {"policy": "fixture-quality-v1", "status": "failed" if rows else "passed", "exit_code": int(bool(rows)),
            "cleanup_confirmed": True, "checks": {"trusted-api": {"status": "passed"},
                "python-unittest": {"status": "failed" if rows else "passed"}},
            "failures": [{"stage": "python-unittest", "status": "failed", "tests_run": 2,
                "test_failure_count": len(rows), "omitted_test_failures": 0,
                "test_failures_complete": True, "test_failures": rows,
                "api_context": ["REFERENCE SOURCE MUST NOT LEAK"]}] if rows else []}


def receipt(failed=("test_product",)):
    result = summary(failed)
    log = ("\n".join("test_" + name + " (test_smoke.Smoke.test_" + name + ") ... ok" for name in ("product", "clicks"))
           + "\nRan 2 tests in 0.01s\nOK\n" if not failed else "FAILED (failures=1)\n")
    return {"status": result["status"], "commands": [{"exit_code": result["exit_code"],
        "cleanup_confirmed": True, "cancelled": False, "timed_out": False,
        "stdout": "REFERENCE APP LOG MUST NOT LEAK", "stderr": log + "OFFLINE_PROJECT_RESULT " + json.dumps(result)}],
        "sandbox": {"image_id": IMAGE, "network": "disabled", "read_only_root": True, "read_only_project": True}}


class SmokeReferenceTests(unittest.TestCase):
    def test_rejected_reference_source_uses_shared_recovery_and_repeats_all_semantic_checks(self):
        fixed = suite(SOURCE.replace("status = request()", "status = request(token='fixture')"))
        invalid_sources = ["...", fixed.files[0].content.replace("class Smoke(", "class Renamed("),
            fixed.files[0].content.replace("status = request(token='fixture')", "self.skipTest('skip')"),
            fixed.files[0].content.replace("self.assertEqual(count, 2)", "self.assertEqual(count, 1)")]
        for rejected in invalid_sources:
            for corrected in (fixed.files[0].content, "..."):
                with self.subTest(rejected=rejected[:30], correction_valid=corrected != "..."):
                    workflow, candidate = self.workflow([receipt(), receipt(())])
                    workflow.bundle[KEY] = deepcopy(DYNAMIC_POLICY)
                    workflow.bundle['whole_project_repair_policy'] = 'verified-scoped-repair-v1'
                    workflow.runtime.store.has_unknown_calls.return_value = False
                    workflow.runtime.store.unsettled_operations.return_value = []
                    workflow.recovery = StageRecovery(workflow)
                    workflow.validate_generated = lambda v, check: WholeProjectBuildRun.validate_generated(workflow,v,check)
                    # Use the actual shared consultation/restart adapter.
                    workflow._repair_decision = lambda *a, **kw: WholeProjectBuildRun._repair_decision(workflow,*a,**kw)
                    workflow.counters['whole_self_reference_repair'] = 1
                    def generate(stage, role, model_type, instruction, data, visible, **options):
                        workflow.stage = stage
                        workflow.stages[stage] = {}
                        if stage.endswith('_consult'):
                            self.assertEqual(data['available_capabilities'],['correct_stage_output'])
                            contract=data['original_stage_contract']
                            self.assertIn('Smoke.test_product',contract['inputs']['json_excerpt'])
                            self.assertIn('unaffected test methods unchanged',contract['instruction']['json_excerpt'])
                            self.assertNotIn('REFERENCE SOURCE',str(data))
                            return {'diagnosis':'The generated source violates the retained suite contract',
                                'evidence_quote':data['failure'], 'action':'correct_stage_output',
                                'requested_changes':['Return complete source, keeping original test identities and unaffected methods']}
                        if stage.endswith('_output'):
                            self.assertEqual(role,'implementer')
                            return {'files':[{'path':'tests/test_smoke.py','content':corrected}]}
                        value={'files':[{'path':'tests/test_smoke.py','content':rejected}]}
                        workflow._generation_context={'stage':stage,'role':role,'model_type':model_type,
                            'instruction':instruction,'data':deepcopy(data),'value':value,'value_digest':digest(value)}
                        return value
                    workflow._generate=Mock(side_effect=generate)
                    result=validate_smoke_reference(workflow,candidate,suite())
                    self.assertEqual(workflow.counters['whole_self_reference_repair'],2)
                    self.assertEqual(workflow.counters['recovery_output_correction'],1)
                    self.assertEqual(workflow._generate.call_count,3)
                    if corrected == '...':
                        self.assertIsNone(result)
                        self.assertEqual(workflow.control.call_count,1)
                        self.assertEqual(workflow.recovery.requests[-1]['status'],'output_correction_rejected')
                    else:
                        self.assertEqual(result,fixed)
                        self.assertEqual(workflow.control.call_count,2)
                        self.assertEqual(workflow.recovery.requests[-1]['status'],'output_revalidated')
                        self.assertFalse(workflow.recovery.requests[-1]['acceptance_credit'])

    def workflow(self, receipts, responses=()):
        workflow = SimpleNamespace(scoped_repairs=True, reference={"digest": "reference-digest", "files": []},
            bundle={"sandbox": {"image_id": IMAGE}}, counters=Counter(), evidence={}, stages={},
            output={"plan": {"acceptance_criteria": ["Products work", "Concurrent updates persist"]}},
            environment={"verification": {"policy": "fixture-quality-v1", "required_checks": ["trusted-api", "python-unittest"]}},
            artifact_type=ProjectArtifact, project_type=ProjectProject, validate_suite=Mock(),
            validate_generated=lambda value, validator: validator(value),
            fixture_problems=Mock(return_value=[]), test_defects=defects,
            state={"run_id": "run"}, runtime=SimpleNamespace(store=Mock()))
        remaining = iter(receipts)
        count = 0

        def control(_suite):
            nonlocal count
            workflow.stage = "build_control_" + str(count)
            count += 1
            return next(remaining)

        values = iter(responses)
        def generate(stage, *_args, **_kwargs):
            workflow.stage = stage
            workflow.stages[stage] = {}
            result = next(values)
            return result if isinstance(result, dict) else result.model_dump(include={"files"})

        workflow.control = Mock(side_effect=control)
        workflow._generate = Mock(side_effect=generate)
        candidate = ProjectArtifact(summary="Candidate", files=[{"path": "app.py", "content": "CANDIDATE SOURCE SECRET"}])
        return workflow, candidate

    def test_auth_and_invalid_concurrency_expectations_can_be_corrected_before_freeze(self):
        fixed = suite(SOURCE.replace("status = request()", "status = request(token='fixture')").replace(
            "for count in docs: self.assertEqual(count, 2)", "self.assertEqual(max(docs), 2)"))
        workflow, candidate = self.workflow([receipt(("test_product", "test_clicks")), receipt(())], [fixed])
        result = validate_smoke_reference(workflow, candidate, suite())
        self.assertEqual(result, fixed)
        self.assertEqual(workflow.control.call_count, 2)
        call = workflow._generate.call_args
        self.assertEqual(call.args[:2], ("whole_self_reference_repair_1", "implementer"))
        self.assertEqual(call.kwargs, {"tool_policy": INLINE_SMOKE_POLICY})
        self.assertEqual(call.args[5], {})
        properties = wire_schema(call.args[2])["properties"]
        self.assertEqual(set(properties), {"files"})
        self.assertEqual(properties["files"]["items"]["properties"]["path"]["enum"], ["tests/test_smoke.py"])
        self.assertIn("argument binding", call.args[3])
        data = call.args[4]
        self.assertEqual(data["previous_suite"]["files"], suite().model_dump()["files"])
        self.assertFalse({"implementation_tree", "current_source", "source_projection"} & data.keys())
        self.assertNotIn("SOURCE", json.dumps(data))
        self.assertNotIn("LOG MUST NOT LEAK", json.dumps(data))
        proof = workflow.evidence["self_reference_validation"]
        self.assertEqual((proof["status"], proof["suite_digest"]), ("passed", digest(fixed.model_dump())))
        self.assertEqual(proof["receipt_digest"], digest(receipt(())))

    def test_manifest_changes_are_rejected_and_the_next_source_only_patch_preserves_metadata(self):
        original = suite()
        fixed = suite(SOURCE.replace("status = request()", "status = request(token='fixture')"))
        proposal = {**fixed.model_dump(include={"files"}), "assumptions": ["Added by repair"]}
        workflow, candidate = self.workflow([receipt(), receipt(())], [proposal, fixed])
        revised = validate_smoke_reference(workflow, candidate, original)
        self.assertEqual(revised.model_dump(exclude={"files"}), original.model_dump(exclude={"files"}))
        self.assertEqual(workflow.control.call_count, 2)
        first, second = workflow._generate.call_args_list
        self.assertNotIn("previous_patch_rejection", first.args[4])
        self.assertIn("assumptions", second.args[4]["previous_patch_rejection"]["message"])
        self.assertEqual(second.args[4]["previous_suite"], first.args[4]["previous_suite"])

    def test_patch_cannot_replace_an_undiagnosed_file(self):
        unrelated = {"files": [{"path": "tests/test_other.py", "content": "raise AssertionError()"}]}
        workflow, candidate = self.workflow([receipt()], [unrelated, unrelated])
        self.assertIsNone(validate_smoke_reference(workflow, candidate, suite()))
        self.assertEqual(workflow.control.call_count, 1)
        self.assertEqual(workflow._generate.call_count, 2)
        self.assertIn("files.0.path", workflow.output["error"]["message"])

    def test_passed_suite_needs_no_repair_and_frozen_suite_failure_cannot_rewrite(self):
        workflow, candidate = self.workflow([receipt(())])
        self.assertEqual(validate_smoke_reference(workflow, candidate, suite()), suite())
        workflow._generate.assert_not_called()
        workflow, candidate = self.workflow([receipt()])
        self.assertIsNone(validate_smoke_reference(workflow, candidate, suite(), allow_repair=False))
        workflow._generate.assert_not_called()

    def test_reference_repair_budget_is_two_and_exhaustion_cannot_drop_tests(self):
        first = suite(SOURCE.replace("status = request()", "status = request(token='first')"))
        second = suite(SOURCE.replace("status = request()", "status = request(token='second')"))
        workflow, candidate = self.workflow([receipt(), receipt(), receipt()], [first, second])
        self.assertIsNone(validate_smoke_reference(workflow, candidate, suite()))
        self.assertEqual(workflow._generate.call_count, 2)
        self.assertEqual(workflow.control.call_count, 3)
        self.assertEqual(workflow.evidence["budget_counters"]["whole_self_reference_repair"], 2)
        self.assertIn("budget exhausted", workflow.output["error"]["message"])
        self.assertNotIn("self_reference_validation", workflow.evidence)

    def test_untrusted_or_incomplete_reference_failure_cannot_authorize_any_call(self):
        for change in ("image", "cleanup", "incomplete"):
            evidence = receipt()
            if change == "image":
                evidence["sandbox"]["image_id"] = "other"
            elif change == "cleanup":
                evidence["commands"][0]["cleanup_confirmed"] = False
            else:
                evidence["commands"][0]["stderr"] = evidence["commands"][0]["stderr"].replace(
                    '"test_failures_complete": true', '"test_failures_complete": false')
            workflow, candidate = self.workflow([evidence])
            with self.subTest(change=change):
                self.assertIsNone(validate_smoke_reference(workflow, candidate, suite()))
                workflow._generate.assert_not_called()

    def test_unknown_reference_execution_propagates_without_author_retry(self):
        workflow, candidate = self.workflow([])
        workflow.control.side_effect = OSError("unknown sandbox completion")
        with self.assertRaisesRegex(OSError, "unknown"):
            validate_smoke_reference(workflow, candidate, suite())
        workflow._generate.assert_not_called()

    def test_named_correction_rejects_changed_unaffected_tests_and_bypasses(self):
        diagnostics = reference_smoke_diagnostics(suite(), [summary()])
        fixed = suite(SOURCE.replace("status = request()", "status = request(token='fixture')"))
        self.assertEqual(validate_smoke_reference_correction(suite(), fixed, diagnostics), fixed)
        wrong_other = suite(fixed.files[0].content.replace("self.assertEqual(count, 2)", "self.assertEqual(count, 1)"))
        with self.assertRaisesRegex(ValueError, "outside the failure list"):
            validate_smoke_reference_correction(suite(), wrong_other, diagnostics)
        skipped = suite(fixed.files[0].content.replace("status = request(token='fixture')", "self.skipTest('skip')"))
        with self.assertRaisesRegex(ValueError, "skip"):
            validate_smoke_reference_correction(suite(), skipped, diagnostics)
        placeholder = suite(SOURCE.replace("self.assertEqual(status, 201)", "self.assertTrue(True)"))
        workflow, candidate = self.workflow([receipt()], [placeholder, placeholder])
        self.assertIsNone(validate_smoke_reference(workflow, candidate, suite()))
        self.assertIn("invalid test", workflow.output["error"]["message"])

    def test_structured_reference_diagnostics_never_include_app_context(self):
        value = summary()
        diagnostics = reference_smoke_diagnostics(suite(), [value])
        self.assertEqual(diagnostics[0]["names"], ["Smoke.test_product"])
        self.assertNotIn("api_context", diagnostics[0])
        self.assertNotIn("REFERENCE SOURCE", json.dumps(diagnostics))
        bad = deepcopy(value)
        bad["failures"][0]["test_failures"][0]["test"] = "test_product (another_module.Smoke)"
        self.assertEqual(reference_smoke_diagnostics(suite(), [bad]), [])

    def test_full_hashed_multiline_unittest_log_fallback_requires_complete_matching_bytes(self):
        log = ("test_product (test_smoke.Smoke)\nA multiline test description. ... FAIL\n\n"
               + "=" * 70 + "\nFAIL: test_product (test_smoke.Smoke)\nPublic product behavior.\n"
               + "-" * 70 + "\nTraceback (most recent call last):\n"
               '  File "/workspace/tests/test_smoke.py", line 6, in test_product\n'
               "    self.assertEqual(status, 201)\nAssertionError: 403 != 201\n\n"
               + "-" * 70 + "\nRan 2 tests in 0.01s\n\nFAILED (failures=1)\n")
        report = summary()
        report["failures"] = [{"stage": "python-unittest", "status": "failed",
                               "diagnostic_excerpt": "untrusted truncated diagnostic must not be used"}]
        report["stage_receipts"] = [{"stage": "python-unittest", "exit_code": 1,
            "log_complete": True, "stream_relay_complete": True, "log_bytes": len(log.encode()),
            "log_sha256": hashlib.sha256(log.encode()).hexdigest()}]
        raw = {"commands": [{"stdout": "", "stderr": log + "OFFLINE_PROJECT_RESULT " + json.dumps(report)}]}
        diagnostics = reference_smoke_diagnostics(suite(), [report], receipt=raw)
        self.assertEqual(diagnostics[0]["assertion"], "AssertionError: 403 != 201")
        self.assertNotIn("/workspace", json.dumps(diagnostics))
        for changed_log in (log[:-2], "REFERENCE APP LOG\n" + log, log.replace("failures=1", "failures=2")):
            bad = {"commands": [{"stdout": "", "stderr": changed_log + "OFFLINE_PROJECT_RESULT " + json.dumps(report)}]}
            self.assertEqual(reference_smoke_diagnostics(suite(), [report], receipt=bad), [])
        incomplete = deepcopy(report)
        incomplete["stage_receipts"][0]["log_complete"] = False
        bad = {"commands": [{"stdout": "", "stderr": log + "OFFLINE_PROJECT_RESULT " + json.dumps(incomplete)}]}
        self.assertEqual(reference_smoke_diagnostics(suite(), [incomplete], receipt=bad), [])

    def shadowed_run_fixture(self):
        source = SOURCE.replace("class Smoke(unittest.TestCase):\n",
            "class Smoke(unittest.TestCase):\n"
            "    @classmethod\n"
            "    def setUpClass(cls):\n"
            "        cls.run = str(123)\n"
            "        cls.product = 'café-' + cls.run\n").replace(
            "status = request()", "status = request(token=self.run)")
        return suite(source)

    def runner_crash_receipt(self):
        crash = ("Traceback (most recent call last):\n"
                 '  File "/usr/lib/python3.10/unittest/case.py", line 650, in __call__\n'
                 "    return self.run(*args, **kwds)\nTypeError: 'str' object is not callable\n")
        value = receipt()
        report = summary()
        report["failures"] = [{"stage": "python-unittest", "status": "failed", "exit_code": 1}]
        report["stage_receipts"] = [{"stage": "python-unittest", "exit_code": 1,
            "log_complete": True, "stream_relay_complete": True, "log_bytes": len(crash.encode()),
            "log_sha256": hashlib.sha256(crash.encode()).hexdigest()}]
        value["commands"][0].update(stdout="", stderr=crash + "OFFLINE_PROJECT_RESULT " + json.dumps(report))
        return value

    def test_runner_crash_renames_only_fixture_references_and_retries_reference(self):
        original = self.shadowed_run_fixture()
        crash = self.runner_crash_receipt()
        workflow, candidate = self.workflow([crash, receipt(())])
        revised = validate_smoke_reference(workflow, candidate, original)
        self.assertEqual(revised.model_dump(exclude={"files"}), original.model_dump(exclude={"files"}))
        original_source = original.files[0].content
        self.assertEqual(revised.files[0].content, original_source.replace(".run", ".fixture_run_id"))
        self.assertEqual(workflow.control.call_count, 2)
        workflow._generate.assert_not_called()
        proof = workflow.runtime.store.save_artifact.call_args.args[-1]
        self.assertEqual(proof["failure_receipt_digest"], digest(crash))
        self.assertEqual(proof["revised_suite_digest"], digest(revised.model_dump()))
        self.assertFalse(proof["model_call"])
        self.assertEqual(workflow.evidence["self_reference_validation"]["status"], "passed")

    def test_runner_crash_does_not_repair_unverified_or_frozen_source(self):
        unverified = self.runner_crash_receipt()
        unverified["commands"][0]["stderr"] = unverified["commands"][0]["stderr"].replace(
            "line 650", "line 651")
        for crash, fixture, allow_repair in (
                (unverified, self.shadowed_run_fixture(), True),
                (self.runner_crash_receipt(), suite(), True),
                (self.runner_crash_receipt(), self.shadowed_run_fixture(), False)):
            workflow, candidate = self.workflow([crash])
            self.assertIsNone(validate_smoke_reference(workflow, candidate, fixture, allow_repair=allow_repair))
            workflow.control.assert_called_once()
            workflow._generate.assert_not_called()
            workflow.runtime.store.save_artifact.assert_not_called()


if __name__ == "__main__":
    unittest.main()
