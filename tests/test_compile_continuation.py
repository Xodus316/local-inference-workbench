"""Settled initial drafts retain their exact source, lineage, contracts and spent counters."""
from collections import Counter
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orchestrator.compile_continuation import KEY, pack, restore, validate
from orchestrator.store import digest
from orchestrator.whole_project_repair_evidence import repair_feedback, repair_record
from test_compile_progress import flow_fixture, compile_fixture
from test_compiler_api import evidence
from test_whole_project_flow import PLAN


def stopped(files):
    return {'files': files, 'receipt': {'scratch_cleanup_confirmed': True,
        'events': [{'type': 'step_finish', 'part': {'reason': 'stop'}}]}}


def source_fixture():
    flow, original, paths, executor, dispatched = flow_fixture()
    with patch('orchestrator.compile_progress.progress_proof', return_value=None):
        assert compile_fixture(flow, original, paths) is None
    bundle = {**flow.bundle, 'backend': {'model': 'pinned'}, 'project_environment': {
        'verification': {'policy': 'fixture-v1'}, 'compile_checkpoints': [
            {'name': 'backend', 'source_root': 'api', 'required_checks': ['gradle-compile']}]}}
    plan = {**PLAN, 'implementation_files': paths}
    output = deepcopy(flow.output)
    output.update(plan=plan, suite=None, repair_attempts=0)
    output['verification']['whole_project'] = {'budget_counters': dict(flow.counters)}
    requests = {'implement': {'editable_paths': paths, 'visible_files': {}}}
    raw = {'implement': stopped({f.path: f.content for f in original.files})}
    calls = [{'stage': 'implement', 'status': 'terminal'}]
    current = original
    stages = {}
    for number, (stage, selected, feedback) in enumerate(dispatched, 1):
        requests[stage] = {'editable_paths': selected, 'visible_files': {f.path: digest(f.content) for f in current.files}}
        changes = {path: f'fixed-{number}' for path in selected}
        raw[stage] = stopped(changes)
        after = current.model_copy(update={'files': [f.model_copy(update={'content': changes.get(f.path, f.content)})
                                                    for f in current.files]})
        stages[stage] = {'repair_attempt_evidence': repair_record(stage, current, after, feedback)}
        current = after
        calls.append({'stage': stage, 'status': 'terminal'})
    row = {'id': '00000000-0000-0000-0000-000000000018', 'bundle': bundle, 'digest': digest(bundle),
           'approval': {'digest': digest(bundle)}, 'status': 'review_required', 'approved': True,
           'calls': calls, 'result': {'output': output, 'stages': stages}}
    return flow, row, requests, raw, paths, dispatched


class CompileContinuationTests(unittest.TestCase):
    def test_reconstructed_draft_reuses_source_and_continues_at_attempt_three(self):
        flow, row, requests, raw, paths, dispatched = source_fixture()
        value = pack(row, requests, raw, row['bundle'])
        self.assertEqual(value['compile_repair_attempts'], 2)
        flow.bundle = {**row['bundle'], KEY: value}
        flow.counters = Counter()  # A newly constructed runtime, not the parent workflow object.
        _, artifact = restore(flow)
        with patch('orchestrator.compiler_api.inspect_failure', return_value=evidence()):
            result = compile_fixture(flow, artifact, paths)
        self.assertIsNotNone(result)
        self.assertEqual(dispatched[-1][0], 'build_compile_repair_1_3')
        self.assertEqual(flow.output['compile_repair_attempts'], 3)
        self.assertEqual(flow.counters['whole_compile_initial_1'], 3)
        self.assertFalse(flow.evidence['compiler_continuation']['prior_acceptance_credit'])

    def test_changed_source_unknown_worker_or_changed_contract_cannot_be_imported(self):
        _, row, requests, raw, paths, _ = source_fixture()
        changed = deepcopy(raw)
        changed['build_compile_repair_1_2']['files'][paths[1]] = 'tampered'
        with self.assertRaisesRegex(ValueError, 'recovered source'):
            pack(row, requests, changed, row['bundle'])
        unknown = deepcopy(raw)
        unknown['implement']['receipt']['scratch_cleanup_confirmed'] = False
        with self.assertRaisesRegex(ValueError, 'stopped worker'):
            pack(row, requests, unknown, row['bundle'])
        with self.assertRaisesRegex(ValueError, 'unchanged contracts'):
            pack(row, requests, raw, {**row['bundle'], 'generation': {'changed': True}})

    def test_refunded_counters_or_unsettled_compile_receipts_refuse_resume(self):
        _, row, requests, raw, _, _ = source_fixture()
        value = pack(row, requests, raw, row['bundle'])
        refunded = deepcopy(value)
        refunded['counters']['whole_compile_initial_1'] = 0
        refunded['compile_repair_attempts'] = 0
        with self.assertRaisesRegex(ValueError, 'spent attempts'):
            validate(refunded, row['bundle'])
        changed = deepcopy(value)
        changed['compile_checks'][-1]['execution']['commands'][0]['cleanup_confirmed'] = False
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            validate(changed, row['bundle'])


if __name__ == '__main__':
    unittest.main()
