"""Exercise the whole-project graph with deterministic workers and sandbox receipts."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock
from uuid import uuid4

from orchestrator.config import Config
from orchestrator.project_build import REVIEWED_POLICY
from orchestrator.runtime import Runtime
from orchestrator.store import digest
from orchestrator.whole_project import POLICY, validate_whole_project_seed, whole_project_types
from orchestrator.whole_project_limits import DEFAULT_SOURCE_BYTES, LEGACY_SOURCE_BYTES, MAX_SOURCE_BYTES
from test_whole_project_sandbox import IMAGE, suite_value, verifier_receipt


PLAN = {"goal": "Implement the application", "acceptance_criteria": ["Compute the sum"],
        "implementation_steps": ["Write the app"], "test_strategy": ["Test behavior"],
        "assumptions": [], "risks": [], "implementation_files": ["app.py"]}


def seeded_large_draft():
    paths = ["app.py", "one.py", "two.py", "three.py"]
    plan = {**PLAN, "implementation_files": paths}
    artifact = {"summary": "Retained draft", "files": [{"path": paths[0], "content": "draft"},
        *[{"path": path, "content": "é" * 22500} for path in paths[1:]]]}
    return {"plan": plan, "artifact": artifact, "provenance": {
        "kind": "unverified-worktree-snapshot", "source_run_id": str(uuid4()),
        "source_plan_digest": digest(plan), "source_artifact_digest": digest(artifact),
        "source_snapshot_sha256": "a" * 64,
    }}


class WholeProjectFlowTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = Config(build={"offline_project_enabled": True})
        self.provider = Mock()
        self.provider.identity.return_value = {"name": "ollama", "endpoint": self.config.ollama_url}
        self.provider.complete.side_effect = self.native_response
        self.executor = Mock()
        self.executor.preflight.return_value = {"engine": "docker", "image": "fixture:offline", "image_id": IMAGE}
        self.executor.materialize.side_effect = lambda _run_id, _index, project: project
        self.executor.preflight_tests.return_value = {
            "status": "passed", "commands": [{"exit_code": 0, "cleanup_confirmed": True,
                "cancelled": False, "timed_out": False,
                "stderr": 'WORKBENCH_TEST_COMPILE {"python_version":"3.10","diagnostics":[]}\n'}],
        }
        self.executor.test.side_effect = self.verify
        self.runtime = Runtime(Path(directory.name), self.config, self.provider, self.executor)
        self.addCleanup(self.runtime.close)
        self.runtime.whole_project_workers = {role: self.worker(role) for role in ("implementer", "test_author", "repairer")}
        self.runtime.whole_project_event = Mock()
        self.runtime.before_whole_project_worker = Mock()
        self.verified = []
        self.regress_self_tests = False
        self.invalid_suites = {}

    def native_response(self, _model, _settings, payload, *_args):
        if "flat inventory" in payload["messages"][0]["content"]:
            value = PLAN
        else:
            value = {"findings": []}
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}

    def worker(self, role, source_bytes=DEFAULT_SOURCE_BYTES):
        worker = Mock()
        settings = self.config.generation_for(role)
        model = self.config.roles[role]
        identity = {"name": "opencode", "role": role, "model": model, "model_digest": role + "-digest",
                    "context": settings.context, "thinking": settings.think, "auto_compaction": True,
                    "source_bytes": source_bytes}
        worker.identity.return_value = identity

        def receipt(stage, on_event):
            if on_event:
                on_event({"type": "step_finish"})
            return {"worker": identity, "role": role, "stage": stage, "context": settings.context,
                    "thinking": settings.think, "model_digest": identity["model_digest"],
                    "resident": {"digest": identity["model_digest"], "context_length": settings.context}}

        def write(*, stage, on_event, **_kwargs):
            value = {"implement": "draft", "whole_self_repair": "self-fixed", "repair_1": "finished",
                     "repair_2": "both-fixed"}[stage]
            return {"files": {"app.py": value}, "receipt": receipt(stage, on_event)}

        def author(*, stage, on_event, payload, **_kwargs):
            suite = suite_value()
            suite["implementation_files"] = json.loads(payload["messages"][1]["content"])["implementation_files"]
            if role == "test_author":
                suite["files"][0]["content"] += "# independent suite\n"
            if stage in self.invalid_suites:
                suite["files"][0]["content"] = self.invalid_suites[stage]
            return {"value": suite, "receipt": receipt(stage, on_event)}

        worker.run.side_effect = write
        worker.generate.side_effect = author
        return worker

    def verify(self, _run_id, attempt, project, *_args, **_kwargs):
        files = {file.path: file.content for file in project.files}
        independent = "# independent suite" in files["tests/test_self.py"]
        source = files["app.py"]
        self.verified.append((attempt, independent, source))
        passed = (source == "both-fixed" or (source == "finished" and (independent or not self.regress_self_tests))
                  or (source == "self-fixed" and not independent))
        return verifier_receipt(passed=passed)

    def run_workflow(self, **approval_fields):
        row = self.runtime.prepare({"mode": "build", "prompt": "Implement an offline application.",
                                    "build_profile": REVIEWED_POLICY})
        bundle = {**row["bundle"], **approval_fields, "whole_project_workflow": POLICY, "whole_project_workers": {
            role: worker.identity() for role, worker in self.runtime.whole_project_workers.items()}}
        row = self.runtime.store.get(self.runtime.store.create(bundle))
        self.runtime.approve(row["id"], row["digest"])
        self.runtime.queue.join()
        result = self.runtime.store.get(row["id"])
        self.assertEqual(result["status"], "completed", result.get("result"))
        return result

    def test_implementation_self_repair_independent_repair_and_final_frozen_checks(self):
        result = self.run_workflow()
        output = result["result"]["output"]
        self.assertEqual(self.verified, [(8000, False, "draft"), (8001, False, "self-fixed"),
                                        (0, True, "self-fixed"), (1, True, "finished"), (8002, False, "finished")])
        self.assertEqual([call["stage"] for call in result["calls"]], [
            "build_plan", "implement", "whole_self_tests", "whole_self_repair", "build_tests", "repair_1",
            "build_code_review_1_chunk_1",
        ])
        self.assertTrue(all(call["status"] == "terminal" for call in result["calls"]))
        by_stage = {call["stage"]: call for call in result["calls"]}
        self.assertEqual(self.runtime.before_whole_project_worker.call_count, 5)
        for event_call in self.runtime.whole_project_event.call_args_list:
            run_id, request_id, stage, role, model, _event = event_call.args
            self.assertEqual((run_id, request_id, role, model),
                             (result["id"], by_stage[stage]["request_id"], by_stage[stage]["role"], by_stage[stage]["model"]))
        independent = self.runtime.whole_project_workers["test_author"].generate.call_args.kwargs
        self.assertEqual(independent["visible_files"], {})
        self.assertNotIn("self-fixed", json.dumps(independent["payload"]))
        evidence = output["verification"]["whole_project"]
        self.assertEqual(evidence["final_self_check"]["status"], "passed")
        self.assertEqual(evidence["final_self_check"]["suite_digest"], evidence["self_suite_digest"])
        self.assertEqual(evidence["independent_suite_digest"], output["suite_digest"])
        self.assertNotEqual(output["suite_digest"], evidence["self_suite_digest"])

    def test_final_self_suite_regression_routes_back_through_independent_verification(self):
        self.regress_self_tests = True
        result = self.run_workflow()
        output = result["result"]["output"]
        self.assertEqual(self.verified[-4:], [(1, True, "finished"), (8002, False, "finished"),
                                              (2, True, "both-fixed"), (8003, False, "both-fixed")])
        self.assertEqual(output["repair_attempts"], 2)
        self.assertIn("repair_2", [call["stage"] for call in result["calls"]])
        evidence = output["verification"]["whole_project"]
        self.assertEqual([check["status"] for check in evidence["final_self_checks"]], ["failed", "passed"])
        self.assertEqual(evidence["final_self_check"]["suite_digest"], evidence["self_suite_digest"])

    def test_seeded_larger_project_runs_fresh_tests_and_repairs(self):
        seed = seeded_large_draft()
        previous_expanded_limit = 160 * 1024
        self.runtime.whole_project_workers = {
            role: self.worker(role, previous_expanded_limit) for role in self.runtime.whole_project_workers
        }
        result = self.run_workflow(whole_project_source_bytes=previous_expanded_limit, whole_project_seed=seed)
        output = result["result"]["output"]
        evidence = output["verification"]["whole_project"]
        stages = [call["stage"] for call in result["calls"]]
        self.assertEqual(stages[:4], ["whole_self_tests", "whole_self_repair", "build_tests", "repair_1"])
        self.assertNotIn("build_plan", stages)
        self.assertNotIn("implement", stages)
        self.assertEqual(evidence["draft_provenance"], seed["provenance"])
        self.assertEqual(evidence["draft_digest"], seed["provenance"]["source_artifact_digest"])
        self.assertEqual(evidence["source_bytes"], previous_expanded_limit)
        artifact_type, project_type = whole_project_types(result["bundle"])
        delivered = project_type.model_validate(output["artifact"])
        delivered_source = [file for file in delivered.files if not file.path.startswith("tests/")]
        self.assertEqual(sum(len(file.content.encode("utf-8")) for file in delivered_source), 135008)
        implementation = artifact_type(summary=delivered.summary, files=delivered_source)
        self.assertEqual(digest(implementation.model_dump()), evidence["final_self_check"]["implementation_digest"])

    def test_seed_source_limit_and_provenance_are_enforced(self):
        seed = seeded_large_draft()
        default_type, _ = whole_project_types({"whole_project_workers": {"implementer": {"name": "opencode"}}})
        self.assertEqual(default_type.byte_limit, LEGACY_SOURCE_BYTES)
        with self.assertRaisesRegex(ValueError, "131072-byte"):
            validate_whole_project_seed(seed, default_type, [])
        artifact_type, project_type = whole_project_types({"whole_project_source_bytes": 160 * 1024})
        with self.assertRaisesRegex(ValueError, "implementation inventory"):
            validate_whole_project_seed(seed, artifact_type, ["README.md"])
        seed["artifact"]["files"][0]["content"] = "different source"
        with self.assertRaisesRegex(ValueError, "provenance differs"):
            validate_whole_project_seed(seed, artifact_type, [])
        seed["artifact"]["files"][0]["content"] = "é" * 15000
        with self.assertRaisesRegex(ValueError, "163840-byte"):
            project_type.model_validate({**seed["artifact"], "files": [*seed["artifact"]["files"],
                                                                     *suite_value()["files"]]})
        with self.assertRaisesRegex(ValueError, "Whole-project source bytes"):
            whole_project_types({"whole_project_source_bytes": MAX_SOURCE_BYTES + 1})

    def test_new_whole_project_limit_uses_worktree_file_bounds(self):
        seed = seeded_large_draft()
        artifact_type, project_type = whole_project_types({})
        self.assertEqual(artifact_type.byte_limit, MAX_SOURCE_BYTES)
        validate_whole_project_seed(seed, artifact_type, [])
        oversized_file = {"path": "wide.txt", "content": "é" * 30001}
        with self.assertRaisesRegex(ValueError, "60000-byte worktree file limit"):
            artifact_type(summary="Wide", files=[oversized_file])
        with self.assertRaisesRegex(ValueError, "60000-byte worktree file limit"):
            project_type.model_validate({"summary": "Wide", "files": [oversized_file, *suite_value()["files"]]})

    def test_self_suite_can_exceed_its_prompt_target(self):
        self.invalid_suites["whole_self_tests"] = suite_value()["files"][0]["content"] + "# " + "x" * 9000
        result = self.run_workflow()
        self.assertNotIn("whole_self_tests_retry", [call["stage"] for call in result["calls"]])
        suite = result["result"]["output"]["verification"]["whole_project"]["self_suite"]
        self.assertGreater(len(suite["files"][0]["content"].encode("utf-8")), 8192)

    def test_known_oversize_manifests_get_one_bounded_correction_each(self):
        self.invalid_suites = {"whole_self_tests": "é" * 8500, "build_tests": "é" * 8500}
        result = self.run_workflow()
        calls = [call["stage"] for call in result["calls"]]
        for stage, role in (("whole_self_tests", "implementer"), ("build_tests", "test_author")):
            with self.subTest(stage=stage):
                retry = stage + "_retry"
                self.assertEqual(calls.count(retry), 1)
                self.assertEqual(calls.index(retry), calls.index(stage) + 1)
                worker = self.runtime.whole_project_workers[role]
                request = next(call.kwargs for call in worker.generate.call_args_list if call.kwargs["stage"] == retry)
                payload = request["payload"]["messages"][1]["content"]
                self.assertNotIn(self.invalid_suites[stage], payload)
                feedback = json.loads(payload)["previous_suite_validation"]
                self.assertEqual(feedback["observed_utf8_bytes"], len(self.invalid_suites[stage].encode("utf-8")))
                self.assertEqual(feedback["max_utf8_bytes"], 16384)


if __name__ == "__main__":
    unittest.main()
