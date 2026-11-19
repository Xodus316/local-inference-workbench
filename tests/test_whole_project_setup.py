"""Fresh preparation binds workers and source without importing generated work."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from orchestrator.config import Config, DEFAULT_ROLES
from orchestrator.project_build import REVIEWED_POLICY
from orchestrator.role_isolation import KEY, SESSION, validate_role_isolation
from orchestrator.runtime import Runtime
from orchestrator.store import digest
from orchestrator import whole_project_setup as setup


IMAGE = 'sha256:' + 'a' * 64
SOURCE_ID = {'runtime.py': 'b' * 64, 'whole_project.py': 'c' * 64}
MODEL = 'fixture-local-model'
MANIFEST = 'd' * 64
SOURCE_BYTES = 128 * 1024
ROLES = {'planner', 'implementer', 'repairer', 'test_author'}


def backend():
    return {'name': 'fixture-native', 'model': MODEL,
            'model_manifest_sha256': MANIFEST, 'session_isolation': SESSION}


def identity(role):
    return {'name': 'fixture-worker', 'role': role, 'model': MODEL,
            'model_digest': MANIFEST, 'session_isolation': SESSION,
            'context': 98304, 'thinking': 'xhigh',
            'source_bytes': SOURCE_BYTES, 'test_bytes': 32768}


def workers():
    return {role: SimpleNamespace(identity=Mock(return_value=identity(role))) for role in ROLES}


def bundle():
    return {'mode': 'build', 'request': {'build_profile': REVIEWED_POLICY, 'control_files': []},
            'roles': {role: MODEL for role in (*sorted(ROLES), 'reviewer')},
            'generation': {role: {'context': 98304, 'think': 'xhigh'}
                           for role in (*sorted(ROLES), 'reviewer')},
            'backend': backend(), 'sandbox': {'image_id': IMAGE},
            'build': {'offline_project_enabled': True, 'allow_shared_test_author': True}}


class FreshSetupTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        self.source = patch.object(setup, 'source_identity', return_value=deepcopy(SOURCE_ID)).start()
        patch('subprocess.run', side_effect=AssertionError('No process in preparation tests')).start()
        patch('subprocess.Popen', side_effect=AssertionError('No process in preparation tests')).start()
        self.runtime = SimpleNamespace(
            config=SimpleNamespace(build=SimpleNamespace(allow_shared_test_author=True)),
            whole_project_workers=workers(), whole_project_prompt_tokenizers={})

    def configure(self, value=None):
        return setup.configure_fresh_bundle(self.runtime, value or bundle(), source_bytes=SOURCE_BYTES)

    def test_fresh_bundle_has_zero_costs_no_seed_and_exact_worker_source_binding(self):
        original = bundle()
        before = deepcopy(original)
        self.runtime.whole_project_prompt_tokenizers = {
            role: SimpleNamespace(identity=Mock(return_value={'name': 'fixture-tokenizer', 'role': role}))
            for role in ROLES}
        value = self.configure(original)
        self.assertEqual(original, before)
        self.assertEqual(value['whole_project_engine_digest'], SOURCE_ID)
        self.assertEqual(value['whole_project_workers'], {role: identity(role) for role in ROLES})
        self.assertTrue(validate_role_isolation(value))
        self.assertTrue(setup.validate_fresh_run(value))
        self.assertEqual(value['fresh_project']['initial_general_repairs'], 0)
        self.assertEqual(value['fresh_project']['initial_self_repairs'], 0)
        self.assertTrue(all(value['fresh_project'][key] is None for key in (
            'application_seed', 'plan_seed', 'self_suite_seed', 'independent_suite_seed')))
        self.assertFalse(setup.FORBIDDEN.intersection(value))
        self.assertEqual(value['build_contract']['test_bytes'], 32768)

    def test_every_prior_work_key_and_control_source_are_rejected(self):
        for key in setup.FORBIDDEN:
            with self.subTest(key=key):
                candidate = {**bundle(), key: None}
                with self.assertRaises(ValueError):
                    self.configure(candidate)
                candidate = self.configure()
                candidate[key] = {}
                with self.assertRaises(ValueError):
                    setup.validate_fresh_run(candidate)
        candidate = bundle()
        candidate['request']['control_files'] = [{'path': 'app.py', 'content': 'old source'}]
        with self.assertRaises(ValueError):
            self.configure(candidate)

    def test_engine_drift_and_nonzero_initial_costs_reject(self):
        value = self.configure()
        for change in (
            lambda x: x['whole_project_engine_digest'].update({'runtime.py': 'e' * 64}),
            lambda x: x['fresh_project'].update(initial_general_repairs=1),
            lambda x: x['fresh_project'].update(initial_self_repairs=1),
            lambda x: x['fresh_project'].update(application_seed={'source': 'old'}),
        ):
            with self.subTest(change=change):
                candidate = deepcopy(value)
                change(candidate)
                with self.assertRaises(ValueError):
                    setup.validate_fresh_run(candidate)
        self.source.return_value = {'runtime.py': 'f' * 64}
        with self.assertRaises(ValueError):
            setup.validate_fresh_run(value)

    def test_same_weight_manifest_session_and_worker_misbinding_reject(self):
        for field, wrong in (('model_digest', 'e' * 64), ('session_isolation', 'shared-session'),
                             ('model', 'other-model'), ('context', 65536)):
            with self.subTest(field=field):
                self.runtime.whole_project_workers = workers()
                self.runtime.whole_project_workers['test_author'].identity.return_value[field] = wrong
                with self.assertRaises(ValueError):
                    self.configure()
        self.runtime.whole_project_workers = workers()
        for field, wrong in (('model_manifest_sha256', 'e' * 64), ('session_isolation', 'shared-session')):
            with self.subTest(provider=field):
                candidate = bundle()
                candidate['backend'][field] = wrong
                with self.assertRaises(ValueError):
                    self.configure(candidate)
        self.runtime.config.build.allow_shared_test_author = False
        with self.assertRaises(ValueError):
            self.configure()

    def test_explicit_worker_capacity_and_tokenizer_ownership_are_required(self):
        for field, wrong in (('source_bytes', SOURCE_BYTES - 1), ('test_bytes', 49152),
                             ('thinking', 'medium')):
            with self.subTest(field=field):
                self.runtime.whole_project_workers = workers()
                self.runtime.whole_project_workers['implementer'].identity.return_value[field] = wrong
                with self.assertRaises(ValueError):
                    self.configure()
        self.runtime.whole_project_workers = workers()
        self.runtime.whole_project_prompt_tokenizers = {'unapproved': SimpleNamespace(identity=lambda: {})}
        with self.assertRaises(ValueError):
            self.configure()
        self.runtime.whole_project_prompt_tokenizers = {}
        del self.runtime.whole_project_workers['test_author']
        with self.assertRaises(ValueError):
            self.configure()

    def test_runtime_preparation_stores_unapproved_empty_journal_without_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / 'reference'
            reference.mkdir()
            (reference / 'app.py').write_text('value = 1\n', encoding='utf8')
            config = Config(roles={role: MODEL for role in DEFAULT_ROLES},
                            generation={role: {'context': 98304, 'think': 'xhigh'} for role in DEFAULT_ROLES},
                            build={'offline_project_enabled': True, 'allow_shared_test_author': True,
                                   'reference_project': str(reference)})
            provider = SimpleNamespace(identity=Mock(side_effect=backend),
                                       complete=Mock(side_effect=AssertionError('No native dispatch')))
            executor = SimpleNamespace(preflight=Mock(return_value={
                'engine': 'fixture', 'image': 'fixture:offline', 'image_id': IMAGE}),
                test=Mock(side_effect=AssertionError('No application execution')))
            runtime = Runtime(Path(directory) / 'state', config, provider, executor)
            try:
                runtime.whole_project_workers = workers()
                row = runtime.prepare_whole_project(
                    {'mode': 'build', 'prompt': 'Implement a public API.', 'build_profile': REVIEWED_POLICY},
                    source_bytes=SOURCE_BYTES)
                self.assertEqual(row['status'], 'prepared')
                self.assertFalse(row['approved'])
                self.assertEqual(row['calls'], [])
                self.assertEqual(row['digest'], digest(row['bundle']))
                self.assertTrue(setup.validate_fresh_run(row['bundle']))
                self.assertEqual(row['bundle']['fresh_project']['initial_general_repairs'], 0)
                self.assertIsNone(row['result'])
                provider.complete.assert_not_called()
                executor.test.assert_not_called()
            finally:
                runtime.close()
