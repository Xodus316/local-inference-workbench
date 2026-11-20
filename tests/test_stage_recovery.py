"""Cross-stage ownership, grounded diagnosis and mandatory execution barriers."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from orchestrator.project_build import ProjectArtifact
from orchestrator.stage_recovery import (KEY, POLICY, StageRecovery, correction_stage, enabled,
    route, settled_source_failure, validate_diagnosis)
from orchestrator.store import STAGES
from orchestrator.whole_project import WholeProjectBuildRun
from test_compile_progress import flow_fixture, compile_fixture, CHECKPOINT, IMAGE


ERROR = 'Could not resolve versionless dependency example:library in offline mode'
SOURCE = 'implementation("example:library")'


def fixture():
    artifact = ProjectArtifact(summary='Current source', files=[{'path': 'api/build.gradle.kts', 'content': SOURCE}])
    flow = object.__new__(WholeProjectBuildRun)
    flow.bundle = {'sandbox': {'image_id': IMAGE}, KEY: deepcopy(POLICY),
                   'whole_project_repair_policy': 'verified-scoped-repair-v1'}
    flow.environment = {'verification': {'policy': 'fixture-v1', 'required_checks': ['gradle-compile']}}
    flow.stage, flow.state = 'build_compile_checkpoint_1_1', {'run_id': 'run'}
    flow.output, flow.evidence, flow.counters, flow.stages = {}, {}, Counter(), {}
    flow.contract, flow.dependency_reference = {'repair_selected_bytes': 65536}, None
    flow.runtime = SimpleNamespace(store=Mock())
    flow.recovery = StageRecovery(flow)
    diagnostic = {'owner': 'application', 'action': 'fix_source', 'diagnosis': 'Generated dependency coordinates omit a version',
        'receipt_quote': ERROR, 'source_anchors': [{'path': 'api/build.gradle.kts', 'line': 1, 'quote': SOURCE}],
        'requested_changes': ['Use the pinned dependency management contract']}
    flow._generate = Mock(return_value=diagnostic)
    summary = {'policy': 'fixture-v1', 'checkpoint': 'backend', 'status': 'review_required', 'exit_code': 125,
        'cleanup_confirmed': True, 'checks': {'gradle-compile': {'status': 'review_required'}},
        'failures': [{'stage': 'gradle-compile', 'status': 'review_required', 'exit_code': 125, 'diagnostic_excerpt': ERROR}]}
    receipt = {'status': 'failed', 'commands': [{'exit_code': 125, 'timed_out': False, 'cancelled': False,
        'cleanup_confirmed': True, 'source_unchanged': True,
        'source_sha256': {f.path: hashlib.sha256(f.content.encode()).hexdigest() for f in artifact.files},
        'stderr': 'OFFLINE_PROJECT_RESULT ' + json.dumps(summary), 'stdout': ''}],
        'sandbox': {'image_id': IMAGE, 'network': 'disabled', 'read_only_project': True, 'read_only_root': True}}
    return flow, artifact, receipt, diagnostic


class StageRoutingTests(unittest.TestCase):
    def test_shared_diagnosis_uses_the_same_bounded_settled_restart(self):
        from orchestrator.project_opencode_whole import OpenCodeSettledResponseError
        flow, artifact, receipt, diagnostic = fixture()
        flow._generate.side_effect = [OpenCodeSettledResponseError('structured-output-limit', {
            'completed_requests': 1, 'scratch_cleanup_confirmed': True,
            'native_receipts_digest': 'a' * 64, 'session_id': 'bound'}), diagnostic]
        proof = flow.recovery.diagnose(artifact, None, receipt, checkpoint=CHECKPOINT)
        self.assertEqual(proof['paths'], ['api/build.gradle.kts'])
        self.assertEqual(flow._generate.call_args_list[-1].args[0], 'whole_recovery_1_retry_select')
        self.assertIn('whole_recovery_1_retry_select', STAGES)
        self.assertEqual(flow.counters['recovery_diagnosis'], 1)
        self.assertEqual(flow.stage, 'build_compile_checkpoint_1_1')

    def test_every_workflow_phase_emits_an_owned_fix_request(self):
        cases = [
            ('build_plan_retry', 'planning', 'planner'),
            ('implement_inventory_retry', 'implementation', 'implementer'),
            ('build_compile_repair_1_2', 'compile', 'repairer'),
            ('whole_self_tests_retry', 'smoke_authoring', 'implementer'),
            ('whole_self_reference_repair_1', 'smoke_qualification', 'implementer'),
            ('whole_self_repair', 'smoke_verification', 'repairer'),
            ('build_tests_retry', 'independent_authoring', 'test_author'),
            ('build_tests_defect_1_file_1', 'independent_qualification', 'test_author'),
            ('repair_1', 'candidate_verification', 'repairer'),
            ('whole_regression_verify_1', 'supplemental_verification', 'repairer'),
            ('whole_profile_0_1', 'verification_profile', 'repairer'),
            ('build_code_review_1_chunk_1_citation_retry', 'review', 'reviewer'),
            ('whole_self_verify_final_1', 'final_verification', 'repairer'),
            ('delivery_export', 'delivery', 'operator')]
        for stage, phase, owner in cases:
            with self.subTest(stage=stage):
                flow, _, _, _ = fixture()
                self.assertEqual(route(stage), (phase, owner))
                request = flow.recovery.terminal(stage, 'Stage output needs correction after its bounded allowance')
                self.assertEqual((request['phase'], request['owner']), (phase, owner))
                self.assertEqual(request['status'], 'blocked')
                self.assertIn('test_assertions', request['immutable_contracts'])
                self.assertIn(flow.runtime.store.save_artifact.call_args.args[1], STAGES)

    def test_unknown_native_completion_at_any_phase_routes_to_operator_without_a_retry(self):
        for stage in ('build_plan', 'implement', 'whole_self_tests', 'build_tests', 'build_verify_0',
                      'build_code_review_0_chunk_1', 'delivery_export'):
            with self.subTest(stage=stage):
                flow, *_ = fixture()
                request = flow.recovery.terminal(stage, 'native_completion_unknown; physical backend unresolved')
                self.assertEqual((request['owner'], request['action']), ('operator', 'resolve_execution'))
                flow._generate.assert_not_called()

    def test_existing_corrections_are_recorded_before_worker_dispatch(self):
        flow, *_ = fixture()
        for stage, role in [('build_plan_retry', 'planner'), ('implement_inventory_retry', 'implementer'),
                            ('whole_self_reference_repair_1', 'implementer'), ('build_tests_fixture_1', 'test_author'),
                            ('build_code_review_1_chunk_1_citation_retry', 'reviewer')]:
            self.assertTrue(correction_stage(stage))
            flow.recovery.dispatch(stage, role, {'editable_paths': ['app.py'], 'failure': 'diagnostic'})
            self.assertEqual(flow.recovery.requests[-1]['status'], 'dispatch_requested')
            self.assertEqual(flow.recovery.requests[-1]['owner'], role)

    def test_terminal_issue_remains_visible_when_request_journal_is_full(self):
        flow, *_ = fixture()
        for index in range(POLICY['max_requests']):
            flow.recovery.request('implement', 'implementer', 'fix', evidence=index)
        flow.recovery.terminal('delivery', 'Final delivery failed')
        self.assertEqual(len(flow.recovery.requests), POLICY['max_requests'])
        self.assertEqual(flow.output['recovery']['terminal_request']['owner'], 'operator')
        with self.assertRaisesRegex(ValueError, 'worker dispatch was not authorized'):
            flow.recovery.dispatch('repair_1', 'repairer', {})


class GroundedDiagnosisTests(unittest.TestCase):
    def test_settled_exit_125_can_authorize_source_repair_without_rewriting_the_receipt(self):
        flow, artifact, receipt, _ = fixture()
        before = deepcopy(receipt)
        proof = flow.recovery.diagnose(artifact, None, receipt, checkpoint=CHECKPOINT)
        self.assertEqual(proof['paths'], ['api/build.gradle.kts'])
        self.assertEqual(flow.recovery.selection(proof, artifact), proof['paths'])
        self.assertEqual(receipt, before)
        self.assertEqual(flow.counters['recovery_diagnosis'], 1)
        self.assertEqual(flow.stage, 'build_compile_checkpoint_1_1')
        flow.runtime.store.snapshot_result.assert_called_once()

    def test_every_execution_barrier_blocks_diagnosis_dispatch(self):
        mutations = [lambda r: r['commands'][0].update(cleanup_confirmed=False),
            lambda r: r['commands'][0].update(timed_out=True), lambda r: r['commands'][0].update(cancelled=True),
            lambda r: r['commands'][0].update(exit_code=None), lambda r: r['commands'][0].update(exit_code=137),
            lambda r: r['commands'][0].update(source_unchanged=False),
            lambda r: r['commands'][0].update(source_sha256={}),
            lambda r: r['sandbox'].update(image_id='sha256:' + 'b' * 64),
            lambda r: r['sandbox'].update(network='enabled'),
            lambda r: r['commands'][0].update(stderr='No trusted result marker')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                flow, artifact, receipt, _ = fixture()
                mutate(receipt)
                self.assertIsNone(flow.recovery.diagnose(artifact, None, receipt, checkpoint=CHECKPOINT))
                flow._generate.assert_not_called()

    def test_fabricated_paths_lines_quotes_receipt_quotes_and_test_edits_are_rejected(self):
        for field, value in [('path', 'tests/test_app.py'), ('path', '../secret'), ('line', 2), ('quote', 'invented')]:
            flow, artifact, _, diagnosis = fixture()
            diagnosis['source_anchors'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_diagnosis(diagnosis, artifact.files, ERROR, 65536)
        diagnosis['receipt_quote'] = 'invented failure'
        with self.assertRaises(ValueError):
            validate_diagnosis(diagnosis, artifact.files, ERROR, 65536)

    def test_diagnosis_cannot_expand_scope_after_source_or_proof_changes(self):
        flow, artifact, receipt, _ = fixture()
        proof = flow.recovery.diagnose(artifact, None, receipt, checkpoint=CHECKPOINT)
        changed = ProjectArtifact(summary='Different source', files=[{'path': 'api/build.gradle.kts', 'content': 'changed'}])
        with self.assertRaises(ValueError):
            flow.recovery.selection(proof, changed)
        proof['paths'].append('unapproved.py')
        # Copying in-memory proof values must not change the retained authority.
        with self.assertRaises(ValueError):
            flow.recovery.selection(proof, artifact)

    def test_infrastructure_diagnosis_requests_operator_attention(self):
        flow, artifact, receipt, diagnosis = fixture()
        diagnosis.update(owner='infrastructure', action='request_operator', source_anchors=[], requested_changes=[])
        self.assertIsNone(flow.recovery.diagnose(artifact, None, receipt, checkpoint=CHECKPOINT))
        self.assertEqual(flow.recovery.requests[-1]['owner'], 'operator')

    def test_repeated_issue_and_global_diagnosis_caps_are_preserved(self):
        flow, artifact, receipt, _ = fixture()
        for _ in range(2):
            self.assertIsNotNone(flow.recovery.diagnose(artifact, None, receipt, checkpoint=CHECKPOINT))
        self.assertIsNone(flow.recovery.diagnose(artifact, None, receipt, checkpoint=CHECKPOINT))
        self.assertEqual(flow._generate.call_count, 2)
        flow.counters['recovery_diagnosis'] = 16
        self.assertIsNone(flow.recovery.diagnose(artifact, None, receipt, checkpoint=CHECKPOINT))
        self.assertEqual(flow._generate.call_count, 2)

    def test_failed_profile_reference_never_sends_reference_material_to_a_model(self):
        flow, artifact, _, _ = fixture()
        result = {'status': 'review_required', 'candidate': None,
                  'reference_profile': {'execution': {'stdout': 'PRIVATE REFERENCE MATERIAL'}}}
        self.assertFalse(flow.authorize_source_repair(artifact, None, result))
        flow._generate.assert_not_called()
        self.assertNotIn('PRIVATE REFERENCE MATERIAL', json.dumps(flow.recovery.requests))
        self.assertEqual(flow.recovery.requests[-1]['owner'], 'operator')

    def test_old_approvals_keep_historical_behavior_and_new_policy_is_exact(self):
        self.assertFalse(enabled({}))
        flow, *_ = fixture()
        self.assertTrue(enabled(flow.bundle))
        with self.assertRaises(ValueError):
            enabled({**flow.bundle, KEY: {**POLICY, 'max_diagnoses': 999}})

    def test_acceptance_checkpoint_costs_cannot_exceed_diagnostic_request_or_issue_bounds(self):
        from orchestrator.stage_recovery import validate_costs
        flow, *_ = fixture()
        validate_costs(flow.bundle, {'recovery_diagnosis': 16, 'recovery_request': 64, 'recovery_issue_a': 2})
        for counters in ({'recovery_diagnosis': 17}, {'recovery_request': 65}, {'recovery_issue_a': 3}):
            with self.subTest(counters=counters), self.assertRaises(ValueError):
                validate_costs(flow.bundle, counters)

    def test_fixture_failure_requests_the_test_owner_without_application_edits(self):
        flow, artifact, _, _ = fixture()
        flow.stage = 'whole_self_verify_0'
        self.assertFalse(flow.authorize_source_repair(artifact, None, {
            'status': 'review_required', 'fixture_failure': {'message': 'Invalid fixture'}}))
        self.assertEqual(flow.recovery.requests[-1]['action'], 'correct_test_fixture')
        self.assertEqual(flow.recovery.requests[-1]['owner'], 'implementer')
        flow._generate.assert_not_called()

    def test_request_counters_survive_restored_checkpoint_costs(self):
        flow, *_ = fixture()
        flow.counters['recovery_request'] = POLICY['max_requests']
        self.assertIsNone(flow.recovery.request('implement', 'implementer', 'correct_stage_output'))
        self.assertEqual(flow.recovery.requests, [])

    def test_a_passing_recheck_only_closes_the_same_gate_scope(self):
        flow, artifact, receipt, _ = fixture()
        flow.recovery.diagnose(artifact, None, receipt, checkpoint=CHECKPOINT)
        flow.recovery.verified('build_compile_checkpoint_2_0', {'status': 'passed'})
        self.assertEqual(flow.recovery.requests[-1]['status'], 'authorized')
        flow.recovery.verified('build_compile_checkpoint_1_2', {'status': 'passed'})
        self.assertEqual(flow.recovery.requests[-1]['status'], 'gate_rechecked')

    def test_delivery_error_has_an_operator_request_without_retry(self):
        from orchestrator.stage_recovery import external_fix_request
        request = external_fix_request('delivery_export', OSError('Destination is unavailable'))
        self.assertEqual((request['phase'], request['owner']), ('delivery', 'operator'))
        self.assertTrue(request['no_retry_or_resume_performed'])

    def test_settled_candidate_failures_use_the_same_diagnosis_at_every_verification_phase(self):
        for stage in ('whole_self_verify_0', 'build_verify_0', 'whole_regression_early_0',
                      'whole_regression_verify_0', 'whole_self_verify_final', 'whole_profile_0_0'):
            with self.subTest(stage=stage):
                flow, artifact, receipt, _ = fixture()
                flow.stage = stage
                result = {'status': 'review_required', 'execution': receipt}
                self.assertTrue(flow.authorize_source_repair(artifact, SimpleNamespace(files=[]), result))
                self.assertEqual(result['status'], 'review_required')
                self.assertEqual(result['execution']['commands'][0]['exit_code'], 125)
                self.assertIn('stage_recovery_request', result['feedback'])

    def test_diagnostic_budget_does_not_refund_an_unknown_native_completion(self):
        flow, artifact, receipt, _ = fixture()
        flow._generate.side_effect = RuntimeError('native_completion_unknown')
        with self.assertRaisesRegex(RuntimeError, 'native_completion_unknown'):
            flow.recovery.diagnose(artifact, None, receipt, checkpoint=CHECKPOINT)
        self.assertEqual(flow.counters['recovery_diagnosis'], 1)
        self.assertEqual(flow.recovery.authorized, {})

    def test_custom_profile_marker_and_tool_identity_are_bound_before_diagnosis(self):
        from orchestrator.sandbox_profiles import normalize_verification_profile, verification_profile_identity
        flow, artifact, receipt, _ = fixture()
        profile = normalize_verification_profile({'name': 'restart', 'verification': {
            'policy': 'fixture-v1', 'marker': 'COMPANION_RESULT', 'required_checks': ['gradle-compile']}})
        receipt['commands'][0]['stderr'] = receipt['commands'][0]['stderr'].replace('OFFLINE_PROJECT_RESULT', 'COMPANION_RESULT')
        identity = verification_profile_identity(profile, IMAGE)
        receipt['sandbox']['effective_runtime'] = identity
        receipt['commands'][0].update(effective_runtime=identity, profile_sources_unchanged=True)
        self.assertIsNotNone(settled_source_failure(flow, artifact, None, receipt, profile=profile))
        receipt['commands'][0]['profile_sources_unchanged'] = False
        self.assertIsNone(settled_source_failure(flow, artifact, None, receipt, profile=profile))


class RecoveryLoopTests(unittest.TestCase):
    def test_compile_loop_diagnoses_then_repairs_a_settled_environment_classified_source_failure(self):
        flow, artifact, paths, executor, dispatched = flow_fixture(count=1, enabled=False)
        flow.bundle[KEY] = deepcopy(POLICY)
        flow.recovery = StageRecovery(flow)
        original_test = executor.test

        def test(*args, **kwargs):
            receipt = original_test(*args, **kwargs)
            if receipt['commands'][0]['exit_code'] == 1:
                row = receipt['commands'][0]
                summary = json.loads(row['stderr'].split('OFFLINE_PROJECT_RESULT ')[1])
                row['exit_code'] = 125
                summary.update(exit_code=125, status='review_required')
                summary['checks']['gradle-compile']['status'] = 'review_required'
                summary['failures'][0].update(status='review_required', exit_code=125, diagnostic_excerpt=ERROR)
                row['stderr'] = 'OFFLINE_PROJECT_RESULT ' + json.dumps(summary)
            return receipt

        executor.test = test
        flow._generate = Mock(return_value={'owner': 'application', 'action': 'fix_source', 'diagnosis': 'Bad generated configuration',
            'receipt_quote': ERROR, 'source_anchors': [{'path': paths[0], 'line': 1, 'quote': 'broken'}],
            'requested_changes': ['Correct the source configuration']})
        result = compile_fixture(flow, artifact, paths)
        self.assertIsNotNone(result)
        self.assertEqual(len(dispatched), 1)
        records = flow.output['verification']['compile_checkpoints']
        self.assertEqual([r['status'] for r in records], ['review_required', 'passed'])
        self.assertEqual(records[0]['execution']['commands'][0]['exit_code'], 125)
        self.assertIn('stage_recovery_request', dispatched[0][2])


if __name__ == '__main__':
    unittest.main()
