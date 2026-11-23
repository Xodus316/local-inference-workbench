"""Pinned API evidence must replace guesses without widening repair or execution authority."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from orchestrator.compiler_api import (KEY, POLICY, dependency_recovery_proof, execute_probe,
                                       missing_import_findings, query, validate_policy)
from orchestrator.java_api_probe import inspect
from orchestrator.project_build import ProjectArtifact
from orchestrator.store import STAGES
from test_compile_progress import flow_fixture, compile_fixture


def evidence():
    return {'policy': POLICY, 'complete_index': True, 'classpath_roots': ['/opt/cache'],
            'image_id': 'sha256:' + 'a' * 64, 'symbols': ['MissingQuery'],
            'missing_imports': ['org.example.MissingQuery'], 'receipt_digest': 'b' * 64,
            'documents': [{'name': 'org.example.Query', 'content': 'public interface org.example.Query {}',
                           'jar': '/opt/cache/api-1.jar', 'cached_versions': ['/opt/cache/api-1.jar'], 'truncated': False}]}


class CompilerApiTests(unittest.TestCase):
    def test_query_uses_only_reported_files_and_excludes_application_and_jdk_imports(self):
        files = ProjectArtifact(summary='Application', files=[
            {'path': 'api/A.java', 'content': 'package app;\nimport app.B;\nimport java.util.List;\nimport org.example.MissingQuery;\nclass A {}'},
            {'path': 'api/B.java', 'content': 'package app;\nimport hidden.Unrelated;\nclass B {}'}]).files
        feedback = {'failed_execution': {'commands': [{'exit_code': 1, 'sandbox_stage_results': {
            'status': 'failed', 'cleanup_confirmed': True, 'failures': [{'stage': 'gradle-compile', 'status': 'failed',
                'diagnostic_excerpt': 'api/A.java:4: error: cannot find symbol | symbol: class MissingQuery'}]}}]}}
        self.assertIsNone(query(files, feedback, None))
        value = query(files, feedback, {'policy': POLICY, 'classpath_roots': ['/opt/cache']})
        self.assertEqual(value['imports'], ['org.example.MissingQuery'])
        self.assertEqual(value['symbols'], ['MissingQuery'])
        for roots in (['/'], ['/opt/../secret'], ['/opt/cache;command'], [], ['/opt/cache'] * 5):
            with self.subTest(roots=roots), self.assertRaises(ValueError):
                validate_policy({'policy': POLICY, 'classpath_roots': roots})

    def test_probe_indexes_bytecode_and_reports_actual_public_alternatives_and_cache_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('api-1.jar', 'api-2.jar'):
                with zipfile.ZipFile(root / name, 'w') as archive:
                    archive.writestr('org/example/Query.class', b'not executed')
                    archive.writestr('org/example/Update.class', b'not executed')
            request = {'classpath_roots': [str(root)], 'imports': ['org.example.MissingQuery'],
                       'symbols': ['MissingQuery', 'update']}
            def javap(args, **kwargs):
                return SimpleNamespace(returncode=0, stdout=('public interface ' + args[-1] + ' {}').encode())
            with patch('orchestrator.java_api_probe.shutil.which', return_value='/jdk/javap'), \
                    patch('orchestrator.java_api_probe.subprocess.run', side_effect=javap) as run:
                result = inspect(request)
            self.assertTrue(result['complete_index'])
            self.assertEqual(result['missing_imports'], ['org.example.MissingQuery'])
            self.assertEqual({d['name'] for d in result['documents']}, {'org.example.Query', 'org.example.Update'})
            self.assertTrue(all(len(d['cached_versions']) == 2 for d in result['documents']))
            self.assertTrue(all(call.args[0][1:3] == ['-public', '-classpath'] for call in run.call_args_list))

    def test_probe_mounts_only_query_and_tooling_and_refuses_unknown_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            value = evidence()
            row = {'exit_code': 0, 'cleanup_confirmed': True, 'stdout_capture': {'complete': True},
                   'stdout': 'WORKBENCH_JAVA_API ' + json.dumps(value)}
            executor = SimpleNamespace(directory=Path(directory), _bounded_container=Mock(return_value=row))
            request = {'classpath_roots': ['/opt/cache'], 'imports': [], 'symbols': []}
            result = execute_probe(executor, '00000000-0000-0000-0000-000000000001', request,
                                   value['image_id'], None, None)
            args = executor._bounded_container.call_args.args[0]
            self.assertEqual(args[args.index('--network') + 1], 'none')
            self.assertIn('--read-only', args)
            self.assertEqual(sum(arg == '--mount' for arg in args), 1)
            self.assertNotIn('/workspace', repr(args))
            self.assertEqual(result['status'], 'passed')
            executor._bounded_container.return_value = {**row, 'exit_code': 1}
            failed = execute_probe(executor, '00000000-0000-0000-0000-000000000001', request,
                                   value['image_id'], None, None)
            self.assertEqual(failed['status'], 'unavailable')
            self.assertEqual(failed['commands'][0]['exit_code'], 1)
            executor._bounded_container.return_value = {**row, 'cleanup_confirmed': False}
            with self.assertRaisesRegex(ValueError, 'confirmed cleanup'):
                execute_probe(executor, '00000000-0000-0000-0000-000000000001', request,
                              value['image_id'], None, None)

    def test_new_dependency_facts_can_use_reserve_once_without_acceptance_credit(self):
        value = evidence()
        proof = dependency_recovery_proof([], value)
        self.assertFalse(proof['acceptance_credit'])
        changed_metadata = {**value, 'receipt_digest': 'c' * 64}
        self.assertIsNone(dependency_recovery_proof([{'dependency_recovery': proof}], changed_metadata))
        for change in ({'complete_index': False}, {'documents': []}, {'missing_imports': []}, {'symbols': ['Other']}):
            with self.subTest(change=change):
                self.assertIsNone(dependency_recovery_proof([], {**value, **change}))

    def test_api_reserve_remains_bounded_and_never_refunds_two_spent_compiler_repairs(self):
        flow, artifact, paths, executor, dispatched = flow_fixture(repeat=True)
        self.assertIsNone(compile_fixture(flow, artifact, paths))
        self.assertEqual(len(dispatched), 2)
        value = evidence()
        with patch('orchestrator.compiler_api.inspect_failure', return_value=value):
            self.assertIsNone(compile_fixture(flow, artifact, paths))
        self.assertEqual(len(dispatched), 3)
        self.assertEqual(flow.counters['whole_compile_initial_1'], 3)
        self.assertEqual(dispatched[-1][0], 'build_compile_repair_1_3')
        self.assertIn('compiler_dependency_evidence', dispatched[-1][2])
        self.assertTrue(any(record.get('dependency_recovery') for record in flow.output['verification']['compile_checkpoints']))
        self.assertIn('build_compile_checkpoint_1_2_api', STAGES)
        self.assertIn('build_compile_repair_1_3_api_after', STAGES)

    def test_missing_import_check_is_scoped_to_verified_negative_inventory(self):
        files = ProjectArtifact(summary='Draft', files=[{'path': 'api/A.java', 'content':
            'import org.example.MissingQuery;\nimport org.unknown.NotInPartialCatalog;\nclass A {}'}]).files
        self.assertEqual(missing_import_findings(files, evidence()), [{'path': 'api/A.java', 'line': 1,
            'import': 'org.example.MissingQuery', 'kind': 'import-absent-from-pinned-cache'}])

    def test_unavailable_replacement_import_gets_one_scoped_edit_correction(self):
        from orchestrator.whole_project import WholeProjectBuildRun
        from test_whole_project_flow import PLAN
        flow = object.__new__(WholeProjectBuildRun)
        flow.scoped_repairs, flow._regression_feedback = True, None
        flow.dependency_reference, flow.source_bytes = None, 65536
        flow.artifact_type, flow.stages = ProjectArtifact, {}
        flow.model_inputs = lambda data: {'plan': PLAN, **data}
        path = 'api/A.java'
        before = ProjectArtifact(summary='Candidate', files=[{'path': path,
            'content': 'import org.example.MissingQuery;\nclass A {}'}])
        bad = 'import org.example.MissingQuery;\nclass A { int changed; }'
        fixed = 'import org.example.Query;\nclass A { int changed; }'
        def worker(stage, *args, **kwargs):
            flow.stages[stage] = {}
            return {'files': {path: fixed if stage.endswith('_edit_retry') else bad},
                'receipt': {'scratch_cleanup_confirmed': True,
                    'events': [{'type': 'step_finish', 'part': {'reason': 'stop'}}]}}
        flow._opencode = Mock(side_effect=worker)
        with patch('orchestrator.compiler_api.inspect_failure', return_value=evidence()):
            result = flow._patch('build_compile_repair_1_2', 'repairer', before,
                SimpleNamespace(files=[], commands=[]), {'compiler_dependency_evidence': evidence()},
                paths=[path], compile_patch=False)
        self.assertEqual(flow._opencode.call_count, 2)
        self.assertEqual(result.files[0].content, fixed)
        retry = flow._opencode.call_args
        self.assertEqual(retry.args[0], 'build_compile_repair_1_2_edit_retry')
        self.assertEqual(retry.kwargs['inputs']['remaining_dependency_imports'][0]['import'], 'org.example.MissingQuery')


if __name__ == '__main__':
    unittest.main()
