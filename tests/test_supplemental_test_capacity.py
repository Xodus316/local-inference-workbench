from copy import deepcopy
from hashlib import sha256
import unittest

from orchestrator.store import digest
from orchestrator.supplemental_regression import POLICY, capacity_binding
from orchestrator.supplemental_test_capacity import validate_visible_test_capacity


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


if __name__ == '__main__':
    unittest.main()
