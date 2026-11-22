"""Pure fresh-role transport checks. Never starts a model, server or tokenizer."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
PUBLIC = ROOT
sys.path[:0] = [str(PUBLIC), str(HERE)]
import native_adapter as adapter
import native_backend
from native_launch_profile import IMAGE, MODEL_DIR, PROFILE, REVISION
from orchestrator.whole_project_limits import DEFAULT_SOURCE_BYTES


def pin():
    value = {"image": IMAGE, "upstream_revision": REVISION, "model_root": MODEL_DIR, "settings": deepcopy(PROFILE),
             "inventory": {"metadata_sha256": {name: "a" * 64 for name in
                ("tokenizer.json", "tokenizer_config.json", "chat_template.jinja", "config.json")}}}
    return {**value, "model_manifest_sha256": hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()}


def executable(root):
    (root / "bin").mkdir()
    (root / "package.json").write_text(json.dumps({"name": "opencode-ai", "version": "1.19.0"}))
    path = root / "bin/opencode.exe"
    path.write_bytes(b"synthetic identity only; never execute")
    return path


class FreshNativeAdapterTests(unittest.TestCase):
    def test_pause_requires_owned_backend_release_and_transport_drain(self):
        provider = object.__new__(adapter.ReviewerProvider)
        receipt = {'stopped': True, 'transport_drained': True, 'container_id': 'owned'}
        from unittest.mock import Mock
        provider.backend = types.SimpleNamespace(owner='owned', deactivate=Mock(return_value=receipt))
        self.assertEqual(provider.pause('run'), receipt)
        provider.backend.deactivate.assert_called_once_with()
        for key in ('stopped', 'transport_drained'):
            provider.backend.deactivate.return_value = {**receipt, key: False}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'did not settle'):
                provider.pause('run')

    def test_pause_never_treats_an_unknown_create_as_not_created(self):
        provider = object.__new__(adapter.ReviewerProvider)
        provider.backend = types.SimpleNamespace(owner=None, _create_dispatched=True)
        with self.assertRaisesRegex(ValueError, 'remains unresolved'):
            provider.pause('run')
        provider.backend._create_dispatched = False
        self.assertTrue(provider.pause('run')['released'])

    def test_preview_builds_all_fresh_roles_without_process_network_or_prior_files(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = executable(Path(folder))
            with patch.object(subprocess, "run", side_effect=AssertionError("No process")), \
                 patch.object(subprocess, "Popen", side_effect=AssertionError("No process")), \
                 patch.object(HTTPConnection, "connect", side_effect=AssertionError("No network")):
                profile = adapter.profile(pin(), exe, source_bytes=DEFAULT_SOURCE_BYTES)
                backend = adapter.Backend(Path(folder) / "unused", pin())
                self.assertEqual(backend.preview(pin()), pin())
            self.assertEqual(set(profile["whole_project_workers"]), {"planner", "implementer", "repairer", "test_author"})
            self.assertEqual(set(profile["roles"]), {*adapter.ROLES, "reviewer"})
            self.assertEqual(profile['backend']['review_worker']['name'], 'opencode')
            self.assertTrue(profile['backend']['review_worker']['auto_compaction'])
            self.assertEqual(profile['backend']['agent_transport'], 'opencode-cli')
            self.assertFalse((Path(folder) / "unused").exists())
            self.assertIsNone(backend.owner)
            self.assertFalse(backend._bridge_open)
            self.assertEqual(profile["whole_project_workers"]["planner"]["planning_policy"], "task-only-no-tools-v1")
            self.assertEqual(profile["whole_project_prompt_tokenizers"]["planner"]["role"], "planner")
            for worker in profile["whole_project_workers"].values():
                self.assertEqual(worker["test_bytes"], 65536)
                self.assertEqual(worker["completion_token_ceiling"], 65536)
            self.assertTrue(all(settings["output_tokens"] == 65536 for settings in profile["generation"].values()))
            self.assertEqual(set(profile["backend"]["native_source_sha256"]), {
                "native_adapter.py", "native_backend.py", "native_transport.py", "native_worker_support.py",
                "native_launch_profile.py", "native_import_guard.py"})

    def test_undefined_test_capacity_is_rejected_for_all_workers(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = executable(Path(folder))
            bridge = adapter.Backend(Path(folder) / "unused", pin()).bridge
            for role in adapter.ROLES:
                with self.subTest(role=role), self.assertRaises(ValueError):
                    adapter.Worker(exe, bridge, pin(), role=role,
                                   source_bytes=DEFAULT_SOURCE_BYTES, test_bytes=65537)

    def test_failed_create_absence_never_claims_cleanup_or_native_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = adapter.Backend(Path(folder), pin())
            backend.create_intent = {"name": "synthetic", "ownership_nonce": "nonce"}
            backend._create_dispatched = True
            with patch.object(backend, "_inspect_exact_name", return_value=None), \
                 patch.object(native_backend, "docker_command", side_effect=AssertionError("No dispatch")):
                with self.assertRaisesRegex(ValueError, "remains unresolved"):
                    backend.close()
            self.assertFalse(backend._closed)
            receipt = json.loads((Path(folder) / "cleanup.json").read_text())
            self.assertFalse(receipt["stopped"])
            self.assertFalse(receipt["native_completion_claim"])
            self.assertEqual(receipt["create_outcome"], "unresolved")

    def test_owner_is_captured_before_owner_receipt_write(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = adapter.Backend(Path(folder), pin())
            backend.create_intent = {"name": "synthetic", "ownership_nonce": "nonce", "image_id": "sha256:" + "a" * 64}
            with patch.object(native_backend, "save", side_effect=OSError("synthetic disk failure")):
                with self.assertRaises(OSError):
                    backend._adopt({"container_id": "b" * 64}, reconciled=True)
            self.assertEqual(backend.owner, "b" * 64)

    def test_fresh_planner_and_test_author_stages_are_explicit(self):
        for stage, role in (("build_plan", "planner"), ("build_plan_retry", "planner"),
                            ("build_tests", "test_author"), ("build_tests_retry", "test_author"),
                            ("build_tests_fixture_1", "test_author")):
            policy = adapter.generation_policy(stage, role)
            self.assertEqual(policy["max_completion_tokens"], 65536)
            self.assertEqual(policy["thinking_token_budget"], {"build_plan": 8192, "build_plan_retry": 4096}.get(stage, 16384))
        for stage, role in (("repair_1", "planner"), ("implementation", "test_author"), ("build_tests", "reviewer")):
            with self.subTest(stage=stage, role=role), self.assertRaises(ValueError):
                adapter.generation_policy(stage, role)

    def test_inline_and_writer_policies_share_the_64k_ceiling(self):
        for stage, role, tool_policy in (
            ("implement", "implementer", None),
            ("whole_self_tests", "implementer", adapter.INLINE_SMOKE_POLICY),
            (adapter.AUTHOR_RESTART_STAGE, "test_author", adapter.INLINE_AUTHOR_RESTART_POLICY),
            ("repair_1_retry_select", "repairer", adapter.INLINE_SELECTION_POLICY),
            ("repair_1", "repairer", None),
            ("build_tests_diagnose_1_file_1", "test_author", adapter.DIAGNOSIS_POLICY),
            ("build_tests_defect_1_file_1", "test_author", adapter.PATCH_POLICY),
            ("build_code_review_1_chunk_1", "reviewer", adapter.REVIEW_POLICY),
        ):
            with self.subTest(stage=stage):
                self.assertEqual(adapter.generation_policy(stage, role, tool_policy)["max_completion_tokens"], 65536)

    def test_wire_schema_removes_only_string_bounds_and_keeps_original(self):
        schema = {"type": "object", "properties": {"source": {"type": "string", "minLength": 1,
            "maxLength": 200, "pattern": "ok", "enum": ["ok"]}, "number": {"type": "integer", "minimum": 1}},
            "required": ["source"], "additionalProperties": False}
        original = deepcopy(schema)
        projected, removed = adapter.transport_schema(schema)
        self.assertEqual(schema, original)
        self.assertEqual(projected["properties"]["source"], {"type": "string", "enum": ["ok"]})
        self.assertEqual(projected["properties"]["number"], schema["properties"]["number"])
        self.assertEqual({row["path"] for row in removed}, {
            "/properties/source/minLength", "/properties/source/maxLength", "/properties/source/pattern"})

    def test_real_profile_context_binds_wire_caps_role_schema_and_sampling(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = adapter.Bridge("http://127.0.0.1:18031", "a" * 24, model=adapter.MODEL,
                context=adapter.CONTEXT, directory=Path(folder), backend_identity=pin())
            body = {"model": adapter.MODEL, "messages": [{"role": "user", "content": "synthetic"}],
                "reasoning_effort": "xhigh", "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "xhigh"},
                "response_format": {"type": "json_schema", "json_schema": {"schema": {"type": "object"}}}}
            result = {"stage": "build_tests", "request_id": "synthetic", "reasoning_effort": "xhigh"}
            with bridge.role_profile("build_tests", "test_author") as session, \
                 patch.object(adapter.HyperQwenOpenCodeBridge, "_forward", return_value=iter([b"saved"])):
                self.assertEqual(list(bridge._forward(None, body, result, 0)), [b"saved"])
            self.assertEqual(body["max_tokens"], 65536)
            self.assertEqual(body["thinking_token_budget"], 16384)
            self.assertEqual(result["session_id"], session)
            self.assertEqual(result["role"], "test_author")
            self.assertEqual(result["dispatch_sequence"], 1)
            saved = Path(folder) / result["wire_response_format"]["path"]
            self.assertEqual(json.loads(saved.read_text()), body["response_format"])
            self.assertEqual({k: body[k] for k in adapter.SAMPLING}, adapter.SAMPLING)
            self.assertIsNone(bridge._role_profile)

    def test_worker_planner_receives_explicit_task_only_guidance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); exe = executable(root)
            bridge = adapter.Bridge("http://127.0.0.1:18031", "a" * 24, model=adapter.MODEL,
                context=adapter.CONTEXT, directory=root, backend_identity=pin())
            bridge.endpoint = "http://127.0.0.1:1"
            worker = adapter.Worker(exe, bridge, pin(), role="planner", source_bytes=DEFAULT_SOURCE_BYTES, test_bytes=65536)
            policy = adapter.generation_policy("build_plan", "planner")
            @contextmanager
            def role(*args):
                yield "synthetic-session"
            rows = [{"receipt": {"status": "completed", "done_event": True,
                "generation_policy_verified": True, "session_id": "synthetic-session",
                "generation_policy": policy, "finish_reason": "stop"}}]
            with patch.object(bridge, "role_profile", side_effect=role), patch.object(bridge, "ready"), \
                 patch.object(adapter, "native_receipts", side_effect=[[], rows]), \
                 patch.object(adapter.OpenCodeProjectWorker, "_session", return_value={"receipt": {}}) as invoke:
                result = worker._session(stage="build_plan", schema=None, system="brief", prompt="task")
            self.assertTrue(invoke.call_args.kwargs["system"].startswith(adapter.PLANNER_SYSTEM))
            self.assertTrue(invoke.call_args.kwargs["prompt"].startswith(adapter.PLANNER_PROMPT))
            self.assertEqual(result["receipt"]["backend_requests"], 1)

    def test_compaction_between_schema_bound_requests_is_never_the_final_response(self):
        body = {"messages": [
            {"role": "system", "content": "You are a context summarization agent."},
            {"role": "user", "content": "Here is the conversation so far:\n\n<conversation>\nContext\n"
                                       "</conversation>\n<template>\nSummary\n</template>"},
        ]}
        self.assertTrue(adapter.is_opencode_compaction(body))
        self.assertFalse(adapter.is_opencode_compaction({**body, "tools": [{"type": "function"}]}))
        self.assertFalse(adapter.is_opencode_compaction({"messages": [body["messages"][1]]}))
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "response.json"
            wire = {"type": "json_schema", "json_schema": {"schema": schema, "strict": True}}
            path.write_text(json.dumps(wire), encoding="utf-8")
            main = {"receipt": {"finish_reason": "stop", "wire_response_format": {
                "path": path.name, "sha256": adapter.sha(path), "content_digest": adapter.digest(wire)}}}
            compact = {"receipt": {"finish_reason": "stop", "auxiliary_request": "opencode-compaction"}}
            rows = [main, compact, main]
            self.assertTrue(adapter.validate_session_schema(rows, root, schema, allow_compaction=True))
            self.assertFalse(adapter.validate_session_schema(rows, root, schema, allow_compaction=False))
            self.assertFalse(adapter.validate_session_schema(rows[:2], root, schema, allow_compaction=True))
            self.assertTrue(adapter.validate_session_schema(rows[1:], root, schema, allow_compaction=True))
            self.assertFalse(adapter.validate_session_schema([main, {"receipt": {}}, main],
                                                            root, schema, allow_compaction=True))
            path.write_text("{}", encoding="utf-8")
            self.assertFalse(adapter.validate_session_schema(rows, root, schema, allow_compaction=True))

    def test_restart_proof_requires_terminal_bound_transport_and_scratch_cleanup(self):
        policy = adapter.generation_policy("build_tests", "test_author")
        receipt = {"status": "completed", "done_event": True, "generation_policy_verified": True,
                   "session_id": "bound", "generation_policy": policy}
        rows = [{"receipt": receipt}]
        self.assertEqual(adapter.settled_response_proof(rows, "bound", policy, True), {
            "session_id": "bound", "native_receipts_digest": adapter.digest(rows),
            "completed_requests": 1, "scratch_cleanup_confirmed": True})
        self.assertIsNone(adapter.settled_response_proof(rows, "bound", policy, False))
        self.assertIsNone(adapter.settled_response_proof(rows, "different", policy, True))
        receipt["done_event"] = False
        self.assertIsNone(adapter.settled_response_proof(rows, "bound", policy, True))

    def test_tokenizer_constructs_complete_role_framing_without_real_tokenization(self):
        payload = {"messages": [{"role": "system", "content": "all instructions"},
                                {"role": "user", "content": "all source"}], "schema": {"type": "object"}}
        seen = []
        def fake_docker(*args, **kwargs):
            seen.append(json.loads(kwargs["input_text"]))
            self.assertNotIn("--gpus", args)
            return json.dumps({"tokens": 123, "variants": [120, 123]})
        with patch.object(adapter, "docker", side_effect=fake_docker):
            self.assertEqual(adapter.PromptTokenizer(pin(), role="planner")(payload), 123)
            self.assertEqual(adapter.PromptTokenizer(pin(), role="reviewer", direct=True)(payload), 123)
        self.assertEqual(len(seen[0]["variants"]), 2)
        self.assertEqual(len(seen[1]["variants"]), 1)
        self.assertTrue(all(row[0]["content"].startswith(adapter.PLANNER_SYSTEM) for row in seen[0]["variants"]))
        self.assertTrue(all("all source" in row[1]["content"] for row in seen[0]["variants"]))
        self.assertEqual(payload["messages"][0]["content"], "all instructions")

    def test_legacy_orchestrator_submodule_is_rejected(self):
        foreign = types.ModuleType("orchestrator.synthetic_foreign")
        foreign.__file__ = str(ROOT / "old-private-engine/orchestrator/foreign.py")
        with patch.dict(sys.modules, {foreign.__name__: foreign}), self.assertRaisesRegex(RuntimeError, "legacy"):
            adapter.assert_public_runtime()

    def test_support_modules_have_no_old_runner_or_path_injection(self):
        for name in adapter.native_source_identity():
            source = (HERE / name).read_text()
            self.assertNotIn("from run_commerce", source)
            self.assertNotIn("from e2e_", source)
            self.assertNotIn("sys.path", source)
            self.assertNotIn("whole_project_checkpoint", source)
            self.assertNotIn("load_native_snapshot", source)


class RecoveryStageCompatibilityTests(unittest.TestCase):
    def test_selection_length_requires_one_completed_bound_request_and_scratch_cleanup(self):
        stage = 'whole_self_repair_2_select'
        policy = adapter.generation_policy(stage, 'repairer', adapter.INLINE_SELECTION_POLICY)
        for change, scratch, expected in (({}, True, True), ({'done_event': False}, True, False),
                ({'finish_reason': 'stop'}, True, False), ({'session_id': 'other'}, True, False),
                ({}, False, False), ({'generation_policy_verified': False}, True, False)):
            with self.subTest(change=change, scratch=scratch), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                bridge = adapter.Bridge('http://127.0.0.1:18031', 'a' * 24, model=adapter.MODEL,
                    context=adapter.CONTEXT, directory=root, backend_identity=pin())
                bridge.endpoint = 'http://127.0.0.1:1'
                worker = adapter.Worker(executable(root), bridge, pin(), role='repairer',
                    source_bytes=DEFAULT_SOURCE_BYTES, test_bytes=65536)
                rows = [{'receipt': {'status': 'completed', 'done_event': True,
                    'generation_policy_verified': True, 'session_id': 'bound',
                    'generation_policy': policy, 'finish_reason': 'length', **change}}]
                error = adapter.OpenCodeOutputLimitError(1)
                error.backend_cleanup_confirmed = True
                error.scratch_cleanup = {'scratch_cleanup_confirmed': scratch}
                @contextmanager
                def role(*args):
                    yield 'bound'
                with patch.object(bridge, 'role_profile', side_effect=role), patch.object(bridge, 'ready'), \
                     patch.object(adapter, 'native_receipts', side_effect=[[], rows]), \
                     patch.object(adapter.OpenCodeProjectWorker, '_session', side_effect=error):
                    with self.assertRaises(adapter.OpenCodeSettledResponseError if expected else adapter.OpenCodeOutputLimitError) as caught:
                        worker._session(stage=stage, schema=None, system='brief', prompt='task',
                                        tool_policy=adapter.INLINE_SELECTION_POLICY)
                if expected:
                    self.assertEqual(caught.exception.reason, 'structured-output-limit')
                    self.assertEqual(caught.exception.proof['completed_requests'], 1)

    def test_recovery_diagnoses_use_the_existing_one_step_selection_transport(self):
        for index in (1, 16):
            policy = adapter.generation_policy(f'whole_recovery_{index}_select', 'repairer', adapter.INLINE_SELECTION_POLICY)
            self.assertEqual(policy['max_completion_tokens'], 65536)
            self.assertEqual(policy['role'], 'repairer')


if __name__ == "__main__":
    unittest.main()
