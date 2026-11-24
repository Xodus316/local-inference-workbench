"""Explicit same-weight role isolation; never claims independent model families."""
import re
from .store import digest

KEY='same_model_role_isolation'
POLICY='same-model-isolated-roles-v1'
SESSION='fresh-role-session-v1'
MIXED_POLICY='explicit-mixed-model-isolated-roles-v1'


def isolation_binding(bundle):
    workers, backend = bundle['whole_project_workers'], bundle['backend']
    if len({worker['model'] for worker in workers.values()} | {backend['model']}) == 1:
        return {'policy': POLICY, 'model': bundle['roles']['test_author'],
            'model_manifest_sha256': backend.get('model_manifest_sha256'),
            'context': bundle['generation']['test_author']['context'],
            'same_model_weights': True, 'session_isolation': SESSION,
            'worker_identity_digests': {role: digest(worker) for role,worker in workers.items()},
            'provider_identity_digest': digest(backend)}
    routes = {**workers, 'reviewer': backend.get('review_worker', {})}
    return {'policy': MIXED_POLICY, 'session_isolation': SESSION,
        'worker_identity_digests': {role: digest(worker) for role,worker in workers.items()},
        'provider_identity_digest': digest(backend),
        'role_bindings': {role: {'model': worker.get('model'), 'manifest': worker.get('model_digest'),
                                 'context': worker.get('context')} for role,worker in routes.items()}}


def _validate_mixed(bundle, value):
    workers, backend = bundle.get('whole_project_workers') or {}, bundle.get('backend') or {}
    routes = {**workers, 'reviewer': backend.get('review_worker', {})}
    if (value.keys() != {'policy','session_isolation','worker_identity_digests','provider_identity_digest','role_bindings'}
            or value['session_isolation'] != SESSION
            or set(workers) != {'planner','implementer','repairer','test_author'}
            or value['worker_identity_digests'] != {role:digest(worker) for role,worker in workers.items()}
            or value['provider_identity_digest'] != digest(backend)
            or set(value['role_bindings']) != set(routes) or 'project_reviewer' in bundle):
        raise ValueError('Mixed-model roles require exact explicit worker/provider bindings')
    profiles = bundle.get('role_profiles') or bundle.get('generation') or {}
    for role,worker in routes.items():
        binding = value['role_bindings'][role]
        if (binding.keys() != {'model','manifest','context'} or not isinstance(binding['model'],str)
                or not binding['model'] or not re.fullmatch('[a-f0-9]{64}',str(binding['manifest']))
                or type(binding['context']) is not int or binding['context'] <= 0
                or worker.get('role') != role or worker.get('session_isolation') != SESSION
                or worker.get('model') != binding['model'] or worker.get('model_digest') != binding['manifest']
                or worker.get('context') != binding['context']
                or bundle.get('roles',{}).get(role) != binding['model']
                or profiles.get(role,{}).get('context') != binding['context']):
            raise ValueError('Mixed-model worker manifest, role, session or context changed')
    review = value['role_bindings']['reviewer']
    if (backend.get('model') != review['model'] or backend.get('model_manifest_sha256') != review['manifest']
            or backend.get('context') != review['context'] or backend.get('session_isolation') != SESSION):
        raise ValueError('Mixed-model reviewer differs from its explicit provider')
    return True


def validate_role_isolation(bundle):
    value=bundle.get(KEY)
    if value is None:
        return False
    if isinstance(value,dict) and value.get('policy') == MIXED_POLICY:
        return _validate_mixed(bundle,value)
    fields={'policy','model','model_manifest_sha256','context','same_model_weights',
            'session_isolation','worker_identity_digests','provider_identity_digest'}
    workers=bundle.get('whole_project_workers') or {}
    backend=bundle.get('backend') or {}
    roles=bundle.get('roles') or {}
    profiles=bundle.get('role_profiles') or bundle.get('generation') or {}
    if (not isinstance(value,dict) or set(value)!=fields or value['policy']!=POLICY
            or value['same_model_weights'] is not True or value['session_isolation']!=SESSION
            or not isinstance(value['model'],str) or not value['model']
            or not isinstance(value['model_manifest_sha256'],str)
            or re.fullmatch(r'[a-f0-9]{64}',value['model_manifest_sha256']) is None
            or type(value['context']) is not int or value['context']<=0
            or set(workers) not in ({'implementer','repairer','test_author'},
                                   {'planner','implementer','repairer','test_author'})
            or set(value['worker_identity_digests'])!=set(workers)
            or value['provider_identity_digest']!=digest(backend)
            or not isinstance(backend.get('name'),str) or not backend['name']
            or backend.get('model')!=value['model']
            or backend.get('model_manifest_sha256')!=value['model_manifest_sha256']
            or backend.get('session_isolation')!=SESSION
            or 'project_reviewer' in bundle):
        raise ValueError('Same-model roles require the exact explicit native backend/session binding')
    for role,worker in workers.items():
        if (value['worker_identity_digests'][role]!=digest(worker)
                or worker.get('role')!=role or worker.get('model')!=value['model']
                or worker.get('model_digest')!=value['model_manifest_sha256']
                or worker.get('session_isolation')!=SESSION
                or worker.get('context')!=value['context']
                or roles.get(role)!=value['model']
                or profiles.get(role,{}).get('context')!=value['context']):
            raise ValueError('Same-model role worker identity, manifest or context changed')
    if roles.get('reviewer')!=value['model'] or profiles.get('reviewer',{}).get('context')!=value['context']:
        raise ValueError('Reviewer must explicitly name the same pinned model and isolated context')
    return True
