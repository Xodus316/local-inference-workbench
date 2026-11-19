from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
import unittest

from orchestrator.project_build import project_test_types
from orchestrator.store import digest
from orchestrator.supplemental_regression import POLICY, RegressionSuite48, capacity_binding, supplemental_suite_type
from orchestrator.supplemental_test_capacity import validate_visible_test_capacity
from orchestrator.whole_project_sandbox import smoke_test_type


class SupplementalProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tests = {'tests/test_evaluator.py': '# frozen evaluator\n' + 'x' * 40000}
        suite = {'files': [{'path': path, 'content': content} for path, content in self.tests.items()],
                 'commands': [], 'cases': []}
        self.bundle = {'build_contract': {'test_bytes': 32768}, 'whole_project_regression_suite': {
            'policy': POLICY, 'capacity': capacity_binding(), 'suite': suite, 'suite_digest': digest(suite),
            'source_files_sha256': {path: sha256(content.encode()).hexdigest() for path, content in self.tests.items()}}}

    def test_only_exact_approved_large_evaluator_reaches_writers(self):
        for role in ('implementer', 'repairer'):
            validate_visible_test_capacity(self.bundle, role, {**self.tests, 'src/app.py': 'pass'})
        for mutation in ({'tests/test_evaluator.py': self.tests['tests/test_evaluator.py'] + '\n'},
                         {**self.tests, 'tests/extra.py': 'pass'}):
            with self.assertRaises(ValueError):
                validate_visible_test_capacity(self.bundle, 'repairer', mutation)

    def test_large_evaluator_does_not_expand_author_or_planner_capacity(self):
        for role in ('test_author', 'planner'):
            with self.assertRaises(ValueError):
                validate_visible_test_capacity(self.bundle, role, self.tests)

    def test_changed_binding_or_test_digest_is_not_authority(self):
        for field in ('capacity', 'suite_digest', 'source_files_sha256'):
            changed = deepcopy(self.bundle)
            changed['whole_project_regression_suite'][field] = {} if field != 'suite_digest' else '0' * 64
            with self.assertRaises(ValueError):
                validate_visible_test_capacity(changed, 'repairer', self.tests)

    def test_ordinary_small_test_projection_needs_no_supplemental_binding(self):
        validate_visible_test_capacity({'build_contract': {'test_bytes': 32768}},
                                       'test_author', {'tests/test_authored.py': 'pass'})

    def test_64k_test_suites_use_an_exact_utf8_boundary(self):
        limit = 65536
        content = 'import unittest\nclass Suite(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(1, 1)\n#'
        files = [{'path': 'tests/test_one.py', 'content': content + 'x' * (32768 - len(content))},
                 {'path': 'tests/helper.py', 'content': '#' + 'x' * 32767}]
        bundle = {'test_suite_bytes': limit, 'build_contract': {'test_bytes': limit}}
        independent, _ = project_test_types(bundle)
        smoke = smoke_test_type(SimpleNamespace(bundle=bundle))
        supplemental = supplemental_suite_type(capacity_binding(limit))
        for suite in (independent, smoke, supplemental):
            suite.manifest_type(summary='64 KiB boundary', files=files)
            too_large = [*files, {'path': 'tests/extra.py', 'content': 'x'}]
            with self.assertRaisesRegex(ValueError, '65536-byte manifest'):
                suite.manifest_type(summary='64 KiB boundary', files=too_large)
        RegressionSuite48.manifest_type(summary='Legacy boundary', files=files[:1])
        with self.assertRaisesRegex(ValueError, '49152-byte manifest'):
            RegressionSuite48.manifest_type(summary='Legacy boundary', files=files)
        visible = {file['path']: file['content'] for file in files}
        validate_visible_test_capacity(bundle, 'test_author', visible)
        with self.assertRaisesRegex(ValueError, 'approved capacity'):
            validate_visible_test_capacity(bundle, 'test_author', {**visible, 'tests/extra.py': 'x'})


if __name__ == '__main__':
    unittest.main()
