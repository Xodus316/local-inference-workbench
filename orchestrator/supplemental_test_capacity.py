"""Bind larger read-only test projections to an exact approved supplemental suite."""
from hashlib import sha256

from .store import digest
from .supplemental_regression import POLICY, capacity_binding


def projected_test_bytes(bundle, role):
    independent = bundle['build_contract']['test_bytes']
    regression = bundle.get('whole_project_regression_suite') or {}
    if regression.get('policy') == POLICY and role in {'implementer', 'repairer'}:
        if regression.get('capacity') != capacity_binding():
            raise ValueError('Unapproved supplemental test capacity')
        return capacity_binding()['test_bytes']
    return independent


def validate_visible_test_capacity(bundle, role, visible):
    tests = {path: content for path, content in visible.items() if path.startswith('tests/')}
    size = sum(len(text.encode('utf-8')) for text in tests.values())
    if size <= bundle['build_contract']['test_bytes']:
        return
    if size > projected_test_bytes(bundle, role):
        raise ValueError('Visible test source exceeds its approved capacity')
    regression = bundle.get('whole_project_regression_suite') or {}
    suite = regression.get('suite') or {}
    expected = {row['path']: row['content'] for row in suite.get('files', [])}
    hashes = {path: sha256(content.encode('utf-8')).hexdigest() for path, content in expected.items()}
    if (regression.get('policy') != POLICY or tests != expected
            or digest(suite) != regression.get('suite_digest')
            or hashes != regression.get('source_files_sha256')):
        raise ValueError('Only the exact approved supplemental suite may use larger projection capacity')
