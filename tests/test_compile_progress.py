"""Late compiler diagnostics earn bounded repairs only with settled progress."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from orchestrator.compile_progress import KEY, POLICY, additional_repairs, progress_proof
from orchestrator.project_build import ProjectArtifact
from orchestrator.store import STAGES
from orchestrator.whole_project import WholeProjectBuildRun


IMAGE = 'sha256:' + 'a' * 64
CHECKPOINT = {'name': 'backend', 'source_root': 'api', 'required_checks': ['gradle-compile']}


class Executor:
    def __init__(self, paths, repeat=False, problem=None):
        self.paths, self.repeat, self.problem = paths, repeat, problem
        self.calls = []

    def materialize(self, run_id, workspace, artifact):
        self.calls.append(workspace)
        return artifact

    def test(self, run_id, workspace, artifact, commands, **kwargs):
        source = {f.path: f.content for f in artifact.files}
        pending = [p for p in self.paths if source[p] == 'broken']
        path = self.paths[0] if self.repeat else pending[0] if pending else None
        status, code = ('failed', 1) if path else ('passed', 0)
        summary = {'checkpoint': 'backend', 'policy': 'fixture-v1', 'status': status, 'exit_code': code,
                   'cleanup_confirmed': True, 'checks': {'gradle-compile': {'status': status}},
                   'failures': [{'stage': 'gradle-compile', 'status': 'failed', 'exit_code': 1,
                                 'diagnostic_excerpt': f'{path}:3: error: cannot find symbol'}] if path else []}
        row = {'exit_code': code, 'timed_out': False, 'cancelled': False, 'cleanup_confirmed': True,
               'source_unchanged': True, 'source_sha256': {p: hashlib.sha256(t.encode()).hexdigest() for p, t in source.items()},
               'stderr': 'OFFLINE_PROJECT_RESULT ' + json.dumps(summary), 'stdout': ''}
        if len(self.calls) == 3 and self.problem:
            row.update(self.problem)
        return {'status': status, 'commands': [row], 'sandbox': {'image_id': IMAGE, 'network': 'disabled',
                'read_only_root': True, 'read_only_project': True}}


def flow_fixture(count=3, *, enabled=True, repeat=False, problem=None):
    paths = [f'api/F{i}.java' for i in range(count)]
    artifact = ProjectArtifact(summary='Original application', files=[{'path': p, 'content': 'broken'} for p in paths])
    flow = object.__new__(WholeProjectBuildRun)
    flow.scoped_repairs, flow._self_compile_round, flow._compile_budget_key = True, None, None
    flow.contract = {'max_compile_repairs': 2, 'max_compile_checkpoints': 4, 'repair_selected_bytes': 65536}
    flow.bundle = {'build_contract': dict(flow.contract), 'sandbox': {'image_id': IMAGE},
                   'whole_project_workflow': 'opencode-whole-project-v1',
                   'whole_project_repair_policy': 'verified-scoped-repair-v1'}
    if enabled:
        flow.bundle[KEY] = deepcopy(POLICY)
    flow.state, flow.environment = {'run_id': 'fixture-run'}, {'verification': {'policy': 'fixture-v1'}}
    flow.output = {'compile_repair_attempts': 0, 'verification': {'compile_checkpoints': []}}
    flow.counters, flow.evidence, flow.stages = Counter(), {'budget_counters': {}}, {}
    flow.dependency_reference = None
    executor = Executor(paths, repeat, problem)
    flow.runtime = SimpleNamespace(build_executor=executor, bundle=lambda state: {},
        events={'fixture-run': None}, deadline=None, store=Mock())
    flow.runtime._build_receipt = lambda run_id, stage, binding, execute: execute()
    flow._timed_check = lambda name, action: action()
    flow.repair_time_available = lambda *args, **kwargs: True
    flow._generate = Mock(side_effect=AssertionError('No model required for path selection'))
    dispatched = []

    def patch(stage, role, component, suite, feedback, *, paths, compile_patch):
        dispatched.append((stage, list(paths), feedback))
        return ProjectArtifact(summary='Scoped deterministic worker', files=[
            {'path': f.path, 'content': f'fixed-{len(dispatched)}' if f.path in paths else f.content}
            for f in component.files])

    flow._patch = patch
    return flow, artifact, paths, executor, dispatched


def compile_fixture(flow, artifact, paths):
    return flow.compile_component(artifact, None, None, 0, 1, CHECKPOINT, set(paths))


class CompileProgressTests(unittest.TestCase):
    def test_three_exposed_frontiers_advance_after_one_separately_accounted_repair(self):
        flow, artifact, paths, executor, dispatched = flow_fixture()
        result = compile_fixture(flow, artifact, paths)
        self.assertIsNotNone(result)
        self.assertEqual(len(dispatched), 3)
        self.assertEqual(flow.output['compile_progress_repair_attempts'], 1)
        self.assertEqual(flow.counters['whole_compile_initial_1'], 3)
        self.assertEqual(len(executor.calls), len(set(executor.calls)))
        self.assertEqual(dispatched[-1][1], [paths[-1]])
        self.assertEqual(dispatched[-1][2]['compile_progress_recovery']['newly_exposed_paths'], [paths[-1]])
        self.assertTrue(all(stage in STAGES for stage, _, _ in dispatched))
        self.assertEqual(flow.output['verification']['compile_checkpoints'][-1]['status'], 'passed')

    def test_old_approval_keeps_two_repairs(self):
        flow, artifact, paths, _, dispatched = flow_fixture(enabled=False)
        self.assertIsNone(compile_fixture(flow, artifact, paths))
        self.assertEqual(len(dispatched), 2)
        self.assertNotIn('compile_progress_repair_attempts', flow.output)

    def test_additional_frontiers_still_stop_at_the_four_repair_hard_bound(self):
        flow, artifact, paths, executor, dispatched = flow_fixture(count=5)
        self.assertIsNone(compile_fixture(flow, artifact, paths))
        self.assertEqual(len(dispatched), 4)
        self.assertEqual(len(executor.calls), 5)
        self.assertEqual(flow.output['compile_progress_repair_attempts'], 2)
        self.assertEqual(flow.counters['whole_compile_initial_1'], 4)

    def test_repeated_diagnostic_is_not_progress_even_when_source_changes(self):
        flow, artifact, paths, _, dispatched = flow_fixture(repeat=True)
        self.assertIsNone(compile_fixture(flow, artifact, paths))
        self.assertEqual(len(dispatched), 2)

    def test_unsettled_or_unbound_execution_never_gets_an_extra_repair(self):
        for problem in ({'cleanup_confirmed': False}, {'timed_out': True}, {'source_unchanged': False},
                        {'source_sha256': {}}, {'cancelled': True}):
            with self.subTest(problem=problem):
                flow, artifact, paths, _, dispatched = flow_fixture(problem=problem)
                self.assertIsNone(compile_fixture(flow, artifact, paths))
                self.assertEqual(len(dispatched), 2)

    def test_error_introduced_in_an_edited_file_cannot_earn_progress_recovery(self):
        flow, artifact, paths, _, _ = flow_fixture()
        result = compile_fixture(flow, artifact, paths)
        records = deepcopy(flow.output['verification']['compile_checkpoints'][:3])
        records[0]['execution']['commands'][0]['source_sha256'][paths[-1]] = '0' * 64
        # The current error file differs from the original receipt: a regression,
        # even though it has a new location and previous compiler errors cleared.
        current = ProjectArtifact(summary='At frontier', files=[
            {'path': f.path, 'content': 'broken' if f.path == paths[-1] else f.content} for f in result.files])
        self.assertIsNone(progress_proof(records, current.files, CHECKPOINT, 'fixture-v1', IMAGE))

    def test_spent_progress_budget_is_not_restored_on_another_compile_call(self):
        flow, artifact, paths, executor, dispatched = flow_fixture(count=5)
        compile_fixture(flow, artifact, paths)
        self.assertIsNone(compile_fixture(flow, artifact, paths))
        self.assertEqual(len(dispatched), 4)
        self.assertEqual(executor.calls[-1], executor.calls[-2])

    def test_policy_cannot_increase_its_bound_or_enable_it_on_an_unscoped_workflow(self):
        flow, *_ = flow_fixture()
        self.assertEqual(additional_repairs(flow.bundle), 2)
        for mutation in ({**POLICY, 'max_additional_repairs': 10}, {**POLICY, 'extra': True}):
            with self.assertRaises(ValueError):
                additional_repairs({**flow.bundle, KEY: mutation})
        with self.assertRaises(ValueError):
            additional_repairs({KEY: POLICY})

    def test_progress_policy_is_bound_into_acceptance_checkpoint_identity(self):
        from orchestrator.whole_project_checkpoint import binding_digest
        flow, *_ = flow_fixture()
        original = binding_digest(flow.bundle)
        old = {k: v for k, v in flow.bundle.items() if k != KEY}
        self.assertNotEqual(original, binding_digest(old))
        self.assertNotEqual(original, binding_digest({**flow.bundle, KEY: {**POLICY, 'max_additional_repairs': 1}}))

    def test_lost_previously_passing_check_blocks_progress(self):
        flow, artifact, paths, _, _ = flow_fixture()
        result = compile_fixture(flow, artifact, paths)
        records = deepcopy(flow.output['verification']['compile_checkpoints'][:3])
        records[0]['stage_results']['checks']['other-check'] = {'status': 'passed'}
        current = ProjectArtifact(summary='At frontier', files=[
            {'path': f.path, 'content': 'broken' if f.path == paths[-1] else f.content} for f in result.files])
        self.assertIsNone(progress_proof(records, current.files, CHECKPOINT, 'fixture-v1', IMAGE))


if __name__ == '__main__':
    unittest.main()
