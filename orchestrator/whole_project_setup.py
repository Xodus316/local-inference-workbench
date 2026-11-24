"""Prepare a fresh whole-project build without importing any generated application."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from .project_build import (REFERENCE_TEST_REPAIR, REVIEWED_POLICY,
                            WHOLE_PROJECT_REPAIR_SELECTED_BYTES, offline_project_contract)
from .project_dependency_reference import validate_dependency_reference
from .role_isolation import KEY as ISOLATION_KEY, isolation_binding, validate_role_isolation
from .store import digest
from .compile_progress import KEY as COMPILE_PROGRESS_KEY, POLICY as COMPILE_PROGRESS_POLICY, additional_repairs
from .stage_recovery import KEY as RECOVERY_KEY, DYNAMIC_POLICY as RECOVERY_POLICY, enabled as recovery_enabled
from .whole_project import POLICY, WORKER_ROLES
from .whole_project_checkpoint import REPAIR_POLICY
from .whole_project_gates import POLICY as GATE_POLICY
from .whole_project_limits import DEFAULT_SOURCE_BYTES, WHOLE_PROJECT_TEST_BYTES, validate_source_bytes

FRESH_POLICY = 'fresh-whole-project-v1'
FORBIDDEN = frozenset({
    'whole_project_seed', 'whole_project_checkpoint', 'project_reuse',
    'whole_project_initial_compile_recovery', 'reference_test_repair_candidate',
    'whole_project_stage_continuation',
    'reference_test_repair_observations', 'external_source_findings',
})


def source_identity():
    root = Path(__file__).resolve().parent
    return {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*.py'))}


def configure_fresh_bundle(runtime, bundle, *, source_bytes=DEFAULT_SOURCE_BYTES,
                           test_bytes=WHOLE_PROJECT_TEST_BYTES, dependency_reference=None,
                           supplemental_suite=None, java_api_probe=None, frontend_diagnostics=None):
    """Pure preparation with explicit workers, image, reference and bounded gates.

    A supplemental suite is evaluator tooling, never a generated plan or source
    seed. It does not replace newly authored smoke or independent tests.
    """
    if (bundle.get('mode') != 'build' or bundle['request'].get('build_profile') != REVIEWED_POLICY
            or FORBIDDEN.intersection(bundle) or bundle['request'].get('control_files')):
        raise ValueError('Fresh builds require a reviewed request without application seeds or control source')
    workers = getattr(runtime, 'whole_project_workers', {})
    if set(workers) not in (WORKER_ROLES, WORKER_ROLES | {'planner'}):
        raise ValueError('Configure explicit implementation, test-author and repair workers before preparation')
    value = deepcopy(bundle)
    value.update(
        whole_project_workflow=POLICY,
        whole_project_source_bytes=validate_source_bytes(source_bytes),
        whole_project_repair_policy=REPAIR_POLICY,
        whole_project_engine_digest=source_identity(),
        whole_project_gate_policy=GATE_POLICY,
        project_review_policy='source-anchors-v1',
        test_suite_bytes=test_bytes,
        reference_test_repair=REFERENCE_TEST_REPAIR,
        build_contract={**offline_project_contract(REVIEWED_POLICY, test_bytes=test_bytes),
                        'repair_selected_bytes': WHOLE_PROJECT_REPAIR_SELECTED_BYTES},
        whole_project_workers={role: worker.identity() for role, worker in workers.items()},
        fresh_project={'policy': FRESH_POLICY, 'application_seed': None,
                       'plan_seed': None, 'self_suite_seed': None, 'independent_suite_seed': None,
                       'initial_general_repairs': 0, 'initial_self_repairs': 0,
                       'operator_intervention': 'none_after_launch'},
    )
    value[COMPILE_PROGRESS_KEY] = deepcopy(COMPILE_PROGRESS_POLICY)
    value[RECOVERY_KEY] = deepcopy(RECOVERY_POLICY)
    if java_api_probe is not None:
        from .compiler_api import KEY, validate_policy
        value[KEY] = deepcopy(validate_policy(java_api_probe))
    if frontend_diagnostics is not None:
        from .frontend_diagnostics import KEY, validate_policy
        value[KEY] = deepcopy(validate_policy(frontend_diagnostics))
    tokenizers = getattr(runtime, 'whole_project_prompt_tokenizers', {})
    if tokenizers:
        if not set(tokenizers) <= set(workers):
            raise ValueError('Prompt tokenizer must belong to an approved worker')
        value['whole_project_prompt_tokenizers'] = {role: item.identity() for role, item in tokenizers.items()}
    if dependency_reference is not None:
        value['project_dependency_reference'] = validate_dependency_reference(
            deepcopy(dependency_reference), value['sandbox']['image_id'])
    if supplemental_suite is not None:
        from .whole_project_regression import validate_regression_suite
        value['whole_project_regression_suite'] = deepcopy(supplemental_suite)
        validate_regression_suite(value['whole_project_regression_suite'], value, [])
    shared = value['roles']['test_author'] in {value['roles'][role] for role in ('implementer', 'repairer')}
    if shared:
        if not runtime.config.build.allow_shared_test_author:
            raise ValueError('Same-model roles require explicit isolated-role configuration')
        value[ISOLATION_KEY] = isolation_binding(value)
        validate_role_isolation(value)
    from .supplemental_test_capacity import projected_test_bytes
    for role, worker in value['whole_project_workers'].items():
        settings = value['generation'][role]
        ceiling = worker.get('completion_token_ceiling')
        if (worker.get('role') != role or worker.get('model') != value['roles'][role]
                or worker.get('source_bytes') != source_bytes
                or worker.get('test_bytes') != projected_test_bytes(value, role)
                or (ceiling is not None and settings.get('output_tokens') != ceiling)
                or worker.get('context') != settings['context'] or worker.get('thinking') != settings['think']):
            raise ValueError('Worker identity differs from the fresh build configuration')
    return value


def validate_fresh_run(bundle):
    if not recovery_enabled(bundle):
        raise ValueError('Fresh runs require their frozen stage recovery policy')
    additional_repairs(bundle)
    expected = {'policy': FRESH_POLICY, 'application_seed': None, 'plan_seed': None,
                'self_suite_seed': None, 'independent_suite_seed': None,
                'initial_general_repairs': 0, 'initial_self_repairs': 0,
                'operator_intervention': 'none_after_launch'}
    if (FORBIDDEN.intersection(bundle) or bundle.get('fresh_project') != expected
            or bundle.get('whole_project_engine_digest') != source_identity()
            or bundle.get('whole_project_gate_policy') != GATE_POLICY
            or bundle.get('project_review_policy') != 'source-anchors-v1'):
        raise ValueError('Fresh run imported prior work or its frozen public engine changed')
    return True
