"""Every workflow role runs OpenCode; compaction cannot replace a schema-bound decision."""
from copy import deepcopy
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

from test_native_adapter import adapter, executable, pin
from orchestrator.project_opencode import _AGENT, _PROVIDER
from orchestrator.whole_project_limits import DEFAULT_SOURCE_BYTES
import native_transport


CASES = [('build_plan', 'planner', None), ('implement', 'implementer', None),
    ('repair_1', 'repairer', None), ('build_tests_retry', 'test_author', None),
    ('build_code_review_1_chunk_1', 'reviewer', adapter.REVIEW_POLICY),
    ('whole_self_tests', 'implementer', adapter.INLINE_SMOKE_POLICY),
    ('repair_1_select', 'repairer', adapter.INLINE_SELECTION_POLICY),
    (adapter.AUTHOR_RESTART_STAGE, 'test_author', adapter.INLINE_AUTHOR_RESTART_POLICY),
    ('build_tests_diagnose_1_file_1', 'test_author', adapter.DIAGNOSIS_POLICY),
    ('build_tests_defect_1_file_1', 'test_author', adapter.PATCH_POLICY)]


def compaction():
    return {'model': adapter.MODEL, 'stream': True, 'messages': [
        {'role': 'system', 'content': 'You are a context summarization agent.'},
        {'role': 'user', 'content': 'Here is the conversation so far:\n\n<conversation>\nSource and history\n'
                                  '</conversation>\n<template>\nSummary\n</template>'}]}


def settings():
    return {'context': adapter.CONTEXT, 'think': 'xhigh', 'output_tokens': 65536, **adapter.SAMPLING}


class AllOpenCodeRolesTests(unittest.TestCase):
    def test_preparation_and_launch_reject_a_direct_or_noncompacting_role(self):
        import role_routes as fresh_trial
        with tempfile.TemporaryDirectory() as directory:
            profile = adapter.profile(pin(), executable(Path(directory)), source_bytes=DEFAULT_SOURCE_BYTES)
        self.assertEqual(set(fresh_trial.validate_opencode_routes(profile)), {*adapter.ROLES, 'reviewer'})
        for role in (*adapter.ROLES, 'reviewer'):
            changed = deepcopy(profile)
            worker = changed['backend']['review_worker'] if role == 'reviewer' else changed['whole_project_workers'][role]
            worker['auto_compaction'] = False
            with self.subTest(role=role), self.assertRaisesRegex(ValueError, 'OpenCode'):
                fresh_trial.validate_opencode_routes(changed)

    def test_real_config_keeps_same_compaction_model_for_every_role_and_structured_mode(self):
        for stage, role, policy in CASES:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                backend = adapter.Backend(root / 'backend', pin())
                worker = adapter.Worker(executable(root), backend.bridge, pin(), role=role,
                    source_bytes=DEFAULT_SOURCE_BYTES, test_bytes=65536)
                state = root / 'private'
                state.mkdir()
                schema = None if role in ('implementer', 'repairer') and policy is None else {'type': 'object'}
                env = worker._project_environment(state, settings(), (), 'fixed task', schema, tool_policy=policy)
                config = json.loads(Path(env['OPENCODE_CONFIG']).read_text())
                self.assertEqual(worker.identity()['name'], 'opencode')
                self.assertTrue(config['compaction']['auto'])
                self.assertEqual(config['agent']['compaction']['model'], f'{_PROVIDER}/{worker.model_alias}')
                self.assertEqual(config['provider'][_PROVIDER]['models'][worker.model_alias]['limit']['output'], 65536)
                if policy is not None or role == 'planner':
                    self.assertEqual(config['permission'], {'*': 'deny'})
                    self.assertEqual(config['agent'][_AGENT]['steps'], 9)

    def test_compaction_wire_protocol_is_allowed_for_all_bound_roles_without_allowing_untyped_primary(self):
        for stage, role, policy in CASES:
            if policy is None and role != 'planner':
                continue  # Ordinary tool writers already accept their tool protocol.
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                profile = adapter.generation_policy(stage, role, policy)
                body = native_transport._body(json.dumps(compaction()).encode(), adapter.MODEL,
                    adapter.CONTEXT, 'synthetic', 'xhigh', profile)
                self.assertNotIn('response_format', body)
                bridge = adapter.Bridge('http://127.0.0.1:18031', 'a' * 24, model=adapter.MODEL,
                    context=adapter.CONTEXT, directory=Path(directory), backend_identity=pin())
                result = {'stage': stage, 'request_id': 'synthetic', 'reasoning_effort': 'xhigh'}
                with bridge.role_profile(stage, role, policy), \
                     patch.object(adapter.HyperQwenOpenCodeBridge, '_forward', return_value=iter([b'sent'])):
                    self.assertEqual(list(bridge._forward(None, body, result, 0)), [b'sent'])
                self.assertEqual(result['auxiliary_request'], 'opencode-compaction')
                ordinary = {**compaction(), 'messages': [{'role': 'user', 'content': 'Return an untyped response'}]}
                with self.assertRaises(native_transport.HyperQwenBridgeError):
                    native_transport._body(json.dumps(ordinary).encode(), adapter.MODEL,
                        adapter.CONTEXT, 'synthetic', 'xhigh', profile)
                with self.assertRaises(native_transport.HyperQwenBridgeError):
                    native_transport._body(json.dumps({**compaction(), 'tools': [{'type': 'function'}]}).encode(),
                        adapter.MODEL, adapter.CONTEXT, 'synthetic', 'xhigh', profile)

    def test_compaction_only_multiple_decisions_and_excess_compactions_cannot_supply_a_decision(self):
        def row(aux=False, finish='stop'):
            return {'receipt': {'status': 'completed', 'done_event': True, 'generation_policy_verified': True,
                'finish_reason': finish, **({'auxiliary_request': 'opencode-compaction'} if aux else
                                            {'wire_response_format': {'synthetic': True}})}}
        self.assertTrue(adapter.decision_rows([row(True), row()]))
        self.assertTrue(adapter.decision_rows([row(True), row(finish='length')], finish='length'))
        for rows in ([row(True)], [row(), row()], [row(), row(True)], [row(True, 'length'), row()],
                     [*[row(True) for _ in range(9)], row()]):
            self.assertFalse(adapter.decision_rows(rows))

    def test_reviewer_dispatches_only_through_the_opencode_worker_with_one_use_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = adapter.Backend(root / 'backend', pin())
            backend.directory.mkdir()
            backend.activate = Mock()
            provider = adapter.ReviewerProvider(backend, pin(), executable(root))
            payload = {'messages': [{'role': 'system', 'content': 'Review the supplied source'},
                {'role': 'user', 'content': '{}'}], 'schema': {'type': 'object'}}
            original = deepcopy(payload)
            receipt = {'agent_transport': 'opencode-cli', 'auto_compaction': True, 'scratch_cleanup_confirmed': True}
            provider.worker.generate = Mock(return_value={'value': {'approved': True}, 'receipt': receipt,
                                                          'usage': {'prompt_tokens': 100, 'completion_tokens': 10}})
            with patch.object(adapter.PromptTokenizer, '__call__', return_value=100), \
                 patch.object(HTTPConnection, 'connect', side_effect=AssertionError('No direct model dispatch')):
                provider.admit(adapter.MODEL, settings(), payload, 'build_code_review_1_chunk_1')
                result = provider.complete(adapter.MODEL, settings(), payload, Event(), None)
                self.assertEqual(json.loads(result['choices'][0]['message']['content']), {'approved': True})
                with self.assertRaisesRegex(ValueError, 'one-use'):
                    provider.complete(adapter.MODEL, settings(), payload, Event(), None)
            self.assertEqual(payload, original)
            self.assertEqual(provider.worker.generate.call_count, 1)
            self.assertEqual(provider.worker.generate.call_args.kwargs['role'], 'reviewer')
            self.assertEqual(provider.worker.generate.call_args.kwargs['tool_policy'], adapter.REVIEW_POLICY)
            self.assertTrue(provider.identity()['auto_compaction'])
            self.assertFalse(provider.tokenizer.direct)

    def test_reviewer_distinguishes_known_rejection_from_unknown_native_completion(self):
        for done in (True, False):
            with self.subTest(done=done), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                backend = adapter.Backend(root / 'backend', pin())
                backend.directory.mkdir()
                backend.activate = Mock()
                provider = adapter.ReviewerProvider(backend, pin(), executable(root))
                payload = {'messages': [{'role': 'system', 'content': 'Review'}, {'role': 'user', 'content': '{}'}],
                           'schema': {'type': 'object'}}
                provider.worker.generate = Mock(side_effect=adapter.OpenCodeOutputLimitError(1))
                rows = [{'receipt': {'status': 'completed', 'done_event': done, 'generation_policy_verified': True}}]
                with patch.object(adapter.PromptTokenizer, '__call__', return_value=100), \
                     patch.object(adapter, 'native_receipts', return_value=rows):
                    provider.admit(adapter.MODEL, settings(), payload, 'build_code_review_1_chunk_1')
                    with self.assertRaises(adapter.OpenCodeOutputLimitError) as caught:
                        provider.complete(adapter.MODEL, settings(), payload, Event(), None)
                self.assertEqual(caught.exception.diagnostics['native_completion'], 'completed_rejected' if done else 'unknown')
                self.assertEqual(hasattr(caught.exception, 'raw_response'), done)


if __name__ == '__main__':
    unittest.main()
