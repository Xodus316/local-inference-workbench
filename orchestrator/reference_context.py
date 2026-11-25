"""Bounded context gathering before reference repairs; original gates stay authoritative."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .store import digest


def observation_limit(bundle):
    from .checkpoint_repair_continuation import POLICY, approved_budgets, KEY
    value = bundle.get(KEY, {})
    return approved_budgets(value['fresh_budgets']).get('context_observations', 0) if value.get('policy') == POLICY else 0


def profile_for(bundle, cases):
    path = Path(__file__).with_name('reference_context_probe.py')
    return {'policy': 'verification-profile-v1', 'name': 'reference-context', 'phase': 'candidate',
        'entrypoint': '/usr/bin/tini', 'environment': {'WORKBENCH_REPAIR_CASES': json.dumps(sorted(cases))},
        'readonly_files': [{'source': str(path), 'target': '/opt/evaluation/reference_context_probe.py',
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}],
        'commands': ['python3 -I /opt/evaluation/reference_context_probe.py'],
        'stdout_capture_bytes': 131072, 'require_complete_stdout': True,
        'verification': {**bundle['project_environment']['verification'], 'marker': 'OFFLINE_PROJECT_RESULT'}}


def verified_observation(bundle, suite, receipt, profile, cases):
    from .reference_repair_coordinator import reference_evidence, inventory
    from .project_build import sandbox_stage_results
    from .sandbox_profiles import verification_profile_identity
    from .reference_context_probe import POLICY
    from .build_sandbox_lifecycle import complete_stdout_capture
    proof = reference_evidence(bundle, suite, receipt)
    identity = verification_profile_identity(profile, bundle['sandbox']['image_id'])
    command = receipt['commands'][0]
    witness = sandbox_stage_results(command).get('reference_context_probe', {})
    sequence = witness.get('sequence', [])
    observations = witness.get('observations', [])
    if (receipt['sandbox'].get('effective_runtime') != identity or command.get('effective_runtime') != identity
            or command.get('profile_sources_unchanged') is not True
            or not complete_stdout_capture(command,profile['stdout_capture_bytes'])
            or witness.get('policy') != POLICY or witness.get('acceptance_credit') is not False
            or witness.get('cases') != sorted(cases)
            or len(sequence) != len(set(sequence)) or set(sequence) != set(inventory(suite))
            or not isinstance(observations, list) or not observations or len(observations) > 16):
        raise ValueError('Context diagnostic lacks complete source-bound original test execution')
    for row in observations:
        if (row.get('case') not in cases or any(not re.fullmatch('[a-f0-9]{64}', row.get(key, ''))
                for key in ('original_spec_sha256', 'instrumented_spec_sha256'))
                or type(row.get('exit_code')) is not int
                or not isinstance(row.get('snapshots'), list) or not 1 <= len(row['snapshots']) <= 2):
            raise ValueError('Context diagnostic lacks actual DOM observations')
        for snap in row['snapshots']:
            if (not isinstance(snap, dict) or snap.keys() != {'pathname', 'body_text', 'row_count', 'rows', 'navigation', 'buttons', 'headings'}
                    or not isinstance(snap['body_text'], str) or not snap['body_text']
                    or not isinstance(snap['pathname'], str) or len(json.dumps(snap)) > 18000):
                raise ValueError('Context diagnostic snapshot is empty or unbounded')
    return {'policy': POLICY, 'base_suite_digest': digest(suite.model_dump()),
        'receipt_digest': digest(receipt), 'execution': proof, 'observed_test_order': sequence,
        'observations': deepcopy(observations), 'acceptance_credit': False}


def project(value):
    """One bounded model copy; complete receipt remains in the durable sandbox journal."""
    result = {k: deepcopy(value[k]) for k in ('policy', 'base_suite_digest', 'receipt_digest', 'acceptance_credit')}
    if value.get('status')=='unavailable':
        return {**result,'status':'unavailable','diagnostic_error':value['diagnostic_error'],
            'context':'The read-only observation attempt produced no usable context. Diagnose from retained original failures; no acceptance credit.'}
    result['context'] = 'Full original test order with preceding tests; public DOM after selected specs. Read-only diagnostic; the unchanged full reference gate remains required.'
    result['tests_run'] = len(value['observed_test_order'])
    result['observations'] = []
    for row in value['observations'][:4]:
        item = {k: row[k] for k in ('case', 'original_spec_sha256', 'exit_code')}
        item['snapshots'] = [{**s, 'body_text': s['body_text'][:1000], 'rows': s['rows'][:3],
            'navigation': s['navigation'][-10:], 'buttons': s['buttons'][-4:]} for s in row['snapshots'][:1]]
        result['observations'].append(item)
    result['complete_receipt_retained'] = True
    return result


def observe(flow, suite, before):
    limit = observation_limit(flow.bundle)
    if not limit:
        return None
    sources = {f.path: f.content for f in suite.files}
    cases = sorted(x['case'] for x in before['failures'] if '#workbench-dom' in sources.get(x['path'], ''))
    if not cases or len(cases) > 8:
        return None
    cache = flow.evidence.setdefault('reference_context_observations', [])
    base = digest(suite.model_dump())
    for value in cache:
        if value.get('base_suite_digest') == base:
            return project(value)
    if flow.counters['recovery_context_observation'] >= limit:
        return None
    from .agent_recovery import barrier
    if blocked := barrier(flow, ''):
        raise ValueError(blocked)
    flow.counters['recovery_context_observation'] += 1
    index = flow.counters['recovery_context_observation']
    flow.evidence['budget_counters'] = dict(flow.counters)
    profile = profile_for(flow.bundle, cases)
    reference = flow.artifact_type(summary='Trusted reference', files=flow.reference['files'])
    artifact = flow.project_type(summary=reference.summary, files=[*reference.files, *suite.files])
    saved = flow.stage
    flow.stage = f'whole_reference_context_{index}'
    try:
        receipt = flow.runtime._build_execution(flow.state, flow.stage, 10000 + index, artifact, suite, verification_profile=profile)
        from .project_build import execution_settled
        if not execution_settled(receipt):
            raise ValueError('Context diagnostic execution or cleanup is unresolved')
        try:
            value = verified_observation(flow.bundle, suite, receipt, profile, cases)
        except ValueError as error:
            from .build_workflow import public_error
            value={'policy':'full-reference-context-diagnostic-v1','status':'unavailable',
                'base_suite_digest':base,'receipt_digest':digest(receipt),'diagnostic_error':public_error(error),
                'acceptance_credit':False}
        cache.append(value)
        return project(value)
    finally:
        flow.stage = saved
