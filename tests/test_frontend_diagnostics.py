"""Frontend recovery uses complete, source-bound compiler facts without passing credit."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from orchestrator.build_sandbox import DockerBuildExecutor
from orchestrator.config import BuildConfig
from orchestrator.frontend_diagnostic_probe import capture
from orchestrator.frontend_diagnostics import KEY, POLICY, execute, inspect_failure, literal_findings, validate_policy
from orchestrator.project_build import ProjectArtifact
from orchestrator.store import STAGES
from orchestrator.whole_project_selection import compiler_diagnostic_selection


IMAGE = 'sha256:' + 'a' * 64
APP = ProjectArtifact(summary='Frontend', files=[{'path': 'frontend/src/A.vue',
    'content': '<v-dialog scroll-strategy="contains" />'}])
MESSAGE = ('src/A.vue(1,11): error TS2322: Type \'"contains"\' is not assignable to type '
           '\'"block" | "close" | "none" | "reposition" | ScrollStrategyFunction | undefined\'.')
APPROVED = {'policy': POLICY, 'node_modules': '/opt/frontend/node_modules'}


def feedback():
    return {'failed_execution': {'commands': [{'exit_code': 1, 'sandbox_stage_results': {
        'status': 'failed', 'cleanup_confirmed': True, 'failures': [{'stage': 'frontend-typecheck',
            'status': 'failed', 'diagnostic_excerpt': MESSAGE}]}}]}}


def receipt():
    value = {'policy': POLICY, 'diagnostic_only': True, 'typecheck_exit_code': 2,
             'stdout': capture(MESSAGE.encode()), 'stderr': capture(b''), 'acceptance_credit': False,
             'versions': {'typescript': 'fixture'}}
    script = Path(__file__).parents[1] / 'orchestrator/frontend_diagnostic_probe.py'
    return {'status': 'passed', 'image_id': IMAGE, 'policy': APPROVED,
        'source_sha256': {f.path: hashlib.sha256(f.content.encode()).hexdigest() for f in APP.files},
        'source_unchanged': True, 'probe_sha256': hashlib.sha256(script.read_bytes()).hexdigest(),
        'evidence': value, 'commands': [{'exit_code': 0, 'cleanup_confirmed': True,
            'stdout_capture': {'complete': True}, 'stdout': 'WORKBENCH_FRONTEND_DIAGNOSTICS ' + json.dumps(value)}]}


class FrontendDiagnosticTests(unittest.TestCase):
    def flow(self, result):
        return SimpleNamespace(bundle={KEY: APPROVED, 'sandbox': {'image_id': IMAGE}},
            state={'run_id': '00000000-0000-0000-0000-000000000001'},
            runtime=SimpleNamespace(_build_receipt=Mock(return_value=result)))

    def test_only_approved_typecheck_failures_trigger_the_source_bound_probe(self):
        flow = self.flow(receipt())
        value = inspect_failure(flow, 'build_compile_checkpoint_2_2_diagnostics', APP, feedback())
        self.assertEqual(value['stdout']['text'], MESSAGE)
        self.assertFalse(value['acceptance_credit'])
        self.assertEqual(value['image_id'], IMAGE)
        self.assertIn('build_compile_checkpoint_2_2_diagnostics', STAGES)
        self.assertIn('build_repair_compile_checkpoint_1_2_2_diagnostics', STAGES)
        self.assertIn('build_self_compile_checkpoint_1_2_2_diagnostics', STAGES)
        flow.runtime._build_receipt.reset_mock()
        self.assertIsNone(inspect_failure(flow, 'unused', APP, {'failed_execution': {'commands': []}}))
        flow.bundle.pop(KEY)
        self.assertIsNone(inspect_failure(flow, 'unused', APP, feedback()))
        flow.runtime._build_receipt.assert_not_called()

    def test_changed_source_image_probe_unknown_cleanup_or_incomplete_output_is_rejected(self):
        for change in ('source', 'image', 'script', 'cleanup', 'timeout', 'cancel', 'capture', 'text', 'credit', 'disagreement'):
            value = receipt()
            if change == 'source': value['source_sha256'] = {}
            if change == 'image': value['image_id'] = 'sha256:' + '0' * 64
            if change == 'script': value['probe_sha256'] = '0' * 64
            if change == 'cleanup': value['commands'][0]['cleanup_confirmed'] = False
            if change == 'timeout': value['commands'][0]['timed_out'] = True
            if change == 'cancel': value['commands'][0]['cancelled'] = True
            if change == 'capture': value['evidence']['stdout']['complete'] = False
            if change == 'text': value['evidence']['stdout']['text'] += 'unbound'
            if change == 'credit': value['evidence']['acceptance_credit'] = True
            if change == 'disagreement': value['evidence']['typecheck_exit_code'] = 0
            # Test the inner validations as well as marker/receipt consistency.
            value['commands'][0]['stdout'] = 'WORKBENCH_FRONTEND_DIAGNOSTICS ' + json.dumps(value['evidence'])
            with self.subTest(change=change), self.assertRaises(ValueError):
                inspect_failure(self.flow(value), 'build_compile_checkpoint_2_2_diagnostics', APP, feedback())

    def test_literal_alternatives_come_from_compiler_evidence_and_match_a_source_anchor(self):
        sources = {f.path: f.content for f in APP.files}
        findings = literal_findings(sources, feedback())
        self.assertEqual(findings[0]['rejected_literal'], 'contains')
        self.assertEqual(findings[0]['compiler_literal_alternatives'], ['block', 'close', 'none', 'reposition'])
        self.assertFalse(findings[0]['alternatives_are_exhaustive'])
        self.assertEqual(literal_findings({'frontend/src/A.vue': '<v-dialog />'}, feedback()), [])
        self.assertEqual(literal_findings(sources, {'comments': MESSAGE}), [])
        malformed = deepcopy(feedback())
        malformed['failed_execution']['commands'][0]['sandbox_stage_results']['failures'][0]['diagnostic_excerpt'] = MESSAGE.replace('contains', r'\x01')
        self.assertEqual(literal_findings(sources, malformed), [])
        # No Vuetify-specific hardcoded list: another compiler union projects its own literals.
        other = deepcopy(feedback())
        other['failed_execution']['commands'][0]['sandbox_stage_results']['failures'][0]['diagnostic_excerpt'] = MESSAGE.replace('"block" | "close" | "none" | "reposition"', '"safe"')
        self.assertEqual(literal_findings(sources, other)[0]['compiler_literal_alternatives'], ['safe'])

    def test_full_replay_locations_extend_the_bounded_selection_without_a_model_call(self):
        files = [*APP.files, ProjectArtifact(summary='Router', files=[
            {'path': 'frontend/src/router.ts', 'content': 'const routes = []'}]).files[0]]
        evidence = feedback()
        evidence['compiler_diagnostics'] = {'diagnostic_only': True, 'stdout': {'text':
            'src/router.ts(1,1): error TS2322: Type mismatch'}, 'stderr': {'text': ''}}
        selected = compiler_diagnostic_selection(files, evidence, byte_limit=65536)
        self.assertEqual(selected['paths'], ['frontend/src/A.vue', 'frontend/src/router.ts'])

    def test_cpu_probe_uses_owned_network_disabled_readonly_mounts_and_retains_settled_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            executor = DockerBuildExecutor(Path(directory), BuildConfig())
            executor._bounded_container = Mock(return_value=receipt()['commands'][0])
            value = execute(executor, '00000000-0000-0000-0000-000000000001', APP, IMAGE, APPROVED, None, None)
            self.assertEqual(value['status'], 'passed')
            args = executor._bounded_container.call_args.args[0]
            self.assertNotIn('--gpus', args)
            self.assertNotIn('--privileged', args)
            self.assertEqual(args[args.index('--network') + 1], 'none')
            self.assertIn('--read-only', args)
            self.assertEqual(args.count('--mount'), 2)
            mounts = [args[i+1] for i, arg in enumerate(args) if arg == '--mount']
            self.assertTrue(all(m.endswith(',readonly') for m in mounts))
            self.assertFalse(any('reference' in m or 'tests' in m for m in mounts))
            executor._bounded_container.return_value = {'exit_code': 1, 'cleanup_confirmed': True}
            failed = execute(executor, '00000000-0000-0000-0000-000000000001', APP, IMAGE, APPROVED, None, None)
            self.assertEqual(failed['status'], 'unavailable')
            self.assertEqual(failed['commands'][0]['exit_code'], 1)
            executor._bounded_container.return_value = {'exit_code': None, 'cleanup_confirmed': False}
            with self.assertRaisesRegex(ValueError, 'unresolved'):
                execute(executor, '00000000-0000-0000-0000-000000000001', APP, IMAGE, APPROVED, None, None)

    def test_probe_uses_the_same_typescript_config_choice_and_full_messages(self):
        from orchestrator.frontend_diagnostic_probe import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, work, modules = root/'project', root/'work', root/'node_modules'
            project.mkdir()
            (project/'tsconfig.app.json').write_text('{}')
            for name in ('typescript', 'vue-tsc', 'vue', 'vue-router', 'vuetify'):
                (modules/name).mkdir(parents=True)
                (modules/name/'package.json').write_text('{"version":"fixture"}')
            realpath = Path
            def path(value):
                return project if value == '/project' else work if value == '/tmp/frontend-diagnostics' else realpath(value)
            # Avoid requiring Windows symlink privileges in this unit test.
            with patch('orchestrator.frontend_diagnostic_probe.Path', side_effect=path), \
                    patch.object(Path, 'symlink_to'), \
                    patch('orchestrator.frontend_diagnostic_probe.subprocess.run', return_value=SimpleNamespace(
                        returncode=2, stdout=MESSAGE.encode(), stderr=b'')) as run:
                value = main({'node_modules': str(modules)})
            args = run.call_args.args[0]
            self.assertIn('--noErrorTruncation', args)
            self.assertEqual(args[-1], 'tsconfig.app.json')
            self.assertEqual(value['typecheck_exit_code'], 2)
            self.assertFalse(value['acceptance_credit'])

    def test_dependency_path_and_capability_are_bound_explicitly(self):
        for path in ('/opt/../node_modules', '/opt//frontend/node_modules', '/', '/opt/node_modules;run', 'node_modules'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_policy({'policy': POLICY, 'node_modules': path})
        self.assertIsNone(validate_policy(None))
        from orchestrator.whole_project_checkpoint import binding_digest
        self.assertNotEqual(binding_digest({}), binding_digest({KEY: APPROVED}))


if __name__ == '__main__':
    unittest.main()
