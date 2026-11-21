"""Versioned source replacement and a bounded, tool-free native method-author call."""

from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import Mock, patch

from orchestrator.project_opencode import _AGENT, _PROVIDER
from orchestrator.project_opencode_whole import OpenCodeProjectWorker
from orchestrator.whole_project_test_repair import (
    INLINE_METHOD_PATCH_POLICY, METHOD_PATCH_GENERATION_LIMITS, METHOD_PATCH_OUTPUT_TOKENS,
    METHOD_PATCH_POLICY, METHOD_PATCH_POLICY_V2, compose_reference_method_patch,
    reference_method_patch_inputs, reference_method_patch_type,
)
from test_whole_project_test_repair import SOURCE, delta, failure, replacement_delta, suite, workflow


class MethodAuthorV2Tests(unittest.TestCase):
    def test_explicit_original_and_replacement_fields_keep_v1_composition_unchanged(self):
        original = suite()
        legacy = reference_method_patch_inputs(original, failure(), byte_limit=16000)
        self.assertEqual(legacy, reference_method_patch_inputs(original, failure(), byte_limit=16000, policy=METHOD_PATCH_POLICY))
        current = reference_method_patch_inputs(original, failure(), byte_limit=16000, policy=METHOD_PATCH_POLICY_V2)
        for before, after in zip(legacy["targets"], current["targets"]):
            self.assertEqual(before["source"], after["original_source"])
            self.assertNotIn("source", after)
            self.assertIn(f"editable definition {after['target']}", current["read_only_context"])
        self.assertIn("definition API.test_passed omitted; retained by harness", current["read_only_context"])
        revised, proof = compose_reference_method_patch(original, failure(), replacement_delta(), byte_limit=16000, policy=METHOD_PATCH_POLICY_V2)
        legacy_revised, legacy_proof = compose_reference_method_patch(original, failure(), delta(), byte_limit=16000)
        self.assertEqual(revised, legacy_revised)
        self.assertEqual(revised.files[0].content, SOURCE.replace("self._get(), 401", "self._get(), 200"))
        self.assertEqual(proof["policy"], METHOD_PATCH_POLICY_V2)
        self.assertEqual(legacy_proof["policy"], METHOD_PATCH_POLICY)
        for value in (delta(), {"changes": [{"target": "API.test_auth", "expected_original_digest": "0" * 64,
                                           "replacement_source": "    def test_auth(self):\n        pass"}]}):
            with self.assertRaises(ValueError):
                compose_reference_method_patch(original, failure(), value, byte_limit=16000, policy=METHOD_PATCH_POLICY_V2)
        no_op = replacement_delta(); no_op["changes"][0]["replacement_source"] = next(
            row["original_source"] for row in current["targets"] if row["target"] == "API.test_auth")
        with self.assertRaisesRegex(ValueError, "no-op"):
            compose_reference_method_patch(original, failure(), no_op, byte_limit=16000, policy=METHOD_PATCH_POLICY_V2)

    def test_input_and_completion_reserve_are_admitted_before_dispatch_without_trimming(self):
        instance = workflow()
        inputs = reference_method_patch_inputs(suite(), failure(), byte_limit=16000, policy=METHOD_PATCH_POLICY_V2)
        unchanged = deepcopy(inputs)
        kind = reference_method_patch_type(inputs["targets"], policy=METHOD_PATCH_POLICY_V2)
        def generate(stage, *_args, **_kwargs):
            instance.stages[stage] = {}
            return {"value": replacement_delta()}
        instance._opencode = Mock(side_effect=generate)
        for admitted, expected_calls in ((32768, 1), (32769, 0)):
            instance._opencode.reset_mock()
            with patch("orchestrator.whole_project.validate_project_prompt", return_value=admitted):
                if expected_calls:
                    instance.ask("build_tests_defect_1_file_1", "test_author", kind, "Correct the code.", **inputs)
                else:
                    with self.assertRaisesRegex(ValueError, "output reserve"):
                        instance.ask("build_tests_defect_1_file_1", "test_author", kind, "Correct the code.", **inputs)
            self.assertEqual(instance._opencode.call_count, expected_calls)
        self.assertEqual(inputs, unchanged)
        with self.assertRaises(ValueError):
            instance._generate("build_tests_defect_1_file_1", "test_author", kind, "Patch", inputs, {})



def fake_worker():
    worker = object.__new__(OpenCodeProjectWorker)
    worker.role, worker.model, worker.model_alias = "test_author", "fixture-model", "fixture-alias"
    worker.context, worker.thinking, worker.model_digest = 65536, True, "model-digest"
    worker.executable, worker.sha256 = Path(sys.executable), "executable-digest"
    config = {"agent": {_AGENT: {}}, "provider": {_PROVIDER: {"models": {
        worker.model_alias: {"options": {}}}}}, "permission": {"read": "allow"}}
    worker._environment = Mock(side_effect=lambda *_args: {"OPENCODE_CONFIG_CONTENT": json.dumps(config)})
    worker._model_metadata = Mock(return_value=worker.model_digest)
    worker._resident = Mock(return_value={"context_length": worker.context, "digest": worker.model_digest})
    worker._unload = Mock()
    return worker


class MethodAuthorWorkerTests(unittest.TestCase):
    def test_config_sets_one_step_no_tools_no_compaction_and_total_cap(self):
        worker = fake_worker()
        with TemporaryDirectory() as directory:
            state = Path(directory)
            for policy, cap in ((None, 65536), (INLINE_METHOD_PATCH_POLICY, METHOD_PATCH_OUTPUT_TOKENS)):
                env = worker._project_environment(state, {"context": 65536, "think": True}, (), "instruction", {"type": "object"}, tool_policy=policy)
                config = json.loads(Path(env["OPENCODE_CONFIG"]).read_text(encoding="utf-8"))
                model = config["provider"][_PROVIDER]["models"][worker.model_alias]
                self.assertEqual(model["limit"]["output"], cap)
                self.assertEqual(env["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"], str(cap))
                self.assertEqual(model["options"]["reasoningEffort"], "high")
                if policy:
                    self.assertEqual(config["permission"], {"*": "deny"})
        self.assertEqual(config["compaction"], {"auto": True})
        self.assertEqual(config["agent"][_AGENT]["steps"], 9)

    def test_terminal_usage_over_cap_is_settled_rejection_with_cleanup(self):
        worker = fake_worker()
        events = [
            {"type": "step_start", "sessionID": "session", "part": {"messageID": "message"}},
            {"type": "text", "sessionID": "session", "part": {"messageID": "message", "text": "{}"}},
            {"type": "step_finish", "sessionID": "session", "part": {"messageID": "message", "reason": "stop",
             "tokens": {"input": 100, "output": METHOD_PATCH_OUTPUT_TOKENS, "reasoning": 1, "cache": {"read": 0, "write": 0}}}},
        ]
        worker._execute = Mock(return_value=("\n".join(json.dumps(row) for row in events) + "\n").encode())
        with TemporaryDirectory() as directory, patch("orchestrator.project_opencode_whole._executable_identity", return_value=worker.sha256), patch("orchestrator.project_opencode_whole.subprocess.run"):
            with self.assertRaisesRegex(ValueError, "completion usage") as caught:
                worker._session(stage="build_tests_defect_1_file_1", role=worker.role, model=worker.model,
                    settings={"context": worker.context, "think": worker.thinking}, original={}, editable=(),
                    system="Patch", prompt="Public data", schema={"type": "object"}, collect=Mock(),
                    cancel=threading.Event(), deadline=time.monotonic()+30, scratch_parent=Path(directory),
                    on_event=None, tool_policy=INLINE_METHOD_PATCH_POLICY)
            self.assertTrue(caught.exception.backend_cleanup_confirmed)
            worker._unload.assert_called_once()
        self.assertTrue(METHOD_PATCH_GENERATION_LIMITS["includes_reasoning"])
        self.assertIsNone(METHOD_PATCH_GENERATION_LIMITS["separate_reasoning_limit"])


if __name__ == "__main__":
    unittest.main()
