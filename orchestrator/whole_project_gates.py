"""Reference-qualified supplemental gates with one shared, finite repair budget."""
from copy import deepcopy

from .build_workflow import validate_baseline
from .project_build import execution_settled
from .store import digest
from .whole_project_regression import validate_regression_suite, regression_cases_passed
from .whole_project_sandbox import execute_self_test

POLICY = 'bounded-reference-first-gates-v1'


class VerificationGateStop(RuntimeError):
    pass


class VerificationUnavailable(VerificationGateStop, ValueError):
    """Tooling or evidence is unavailable; rewriting worker output cannot prove a pass."""


def snapshot(flow):
    flow.evidence['budget_counters'] = dict(flow.counters)
    flow.runtime.store.snapshot_result(flow.state['run_id'], {'output': flow.output, 'stages': flow.stages})


def stop(flow, message):
    flow.output['error'] = {'stage': flow.stage, 'message': message}
    snapshot(flow)
    raise VerificationGateStop(message)


def repair(flow, artifact, suite, result, kind):
    if (flow.output['repair_attempts'] >= flow.limits['max_repair_attempts']
            or not flow.repair_time_available(f"repair_{flow.counters['repair'] + 1}")):
        stop(flow, 'Verification failed and the frozen repair budget is exhausted')
    allowed = result.get('status') == 'failed'
    if not allowed and hasattr(flow, 'authorize_source_repair'):
        profiles = flow.bundle['build'].get('verification_profiles', [])
        profile = next((p for p in profiles if p['name'] == kind), None)
        allowed = flow.authorize_source_repair(artifact, suite, result, profile=profile)
    if (not allowed or result.get('execution_settled') is not True
            or not isinstance(result.get('feedback'), dict)):
        stop(flow, 'Verification was inconclusive; application repair is not authorized')
    stage = flow.bounded('repair', 'max_repair_attempts')
    flow.output['repair_attempts'] += 1
    snapshot(flow)  # Spend before dispatch; failures never refund an attempt.
    feedback = {'failed_execution': deepcopy(result['feedback'])}
    if 'source_recovery_request' in result:
        feedback['stage_recovery_request'] = result['source_recovery_request']
    replacement = flow._patch(stage, 'repairer', artifact, suite,
                              feedback)
    if replacement is None or digest(replacement.model_dump()) == digest(artifact.model_dump()):
        stop(flow, 'Verification repair did not produce a changed, compiled candidate')
    flow.evidence.setdefault('additional_gate_repairs', []).append({
        'stage': stage, 'gate': kind, 'predecessor_digest': digest(artifact.model_dump()),
        'artifact_digest': digest(replacement.model_dump()), 'result_digest': digest(result),
        'cumulative_general_repairs': flow.output['repair_attempts'],
    })
    flow.output['artifact'] = replacement.model_dump()
    snapshot(flow)
    return replacement


def supplemental(flow, artifact, suite):
    binding = flow.bundle.get('whole_project_regression_suite')
    if binding is None:
        return artifact
    regression = validate_regression_suite(binding, flow.bundle, flow.output['plan']['implementation_files'])
    checks = flow.evidence.setdefault('early_regression_checks', [])
    if len(checks) > flow.limits['max_repair_attempts']:
        stop(flow, 'Supplemental verification exhausted its frozen execution budget')
    index = len(checks)
    stage = f'whole_regression_early_{index}'
    result = execute_self_test(flow, artifact, regression, stage, 8400 + index)
    if result['status'] == 'passed' and not regression_cases_passed(result['execution'], regression):
        result['status'] = 'review_required'
    result.update(stage=stage, artifact_digest=digest(artifact.model_dump()),
                  binding_digest=digest(binding), execution_settled=execution_settled(result['execution']),
                  final_regression_credit=False)
    checks.append(result)
    flow.stages[stage] = {key: result[key] for key in (
        'status', 'artifact_digest', 'binding_digest', 'execution_settled', 'final_regression_credit')}
    snapshot(flow)
    if result['status'] == 'passed':
        if getattr(flow, 'recovery', None) is not None:
            flow.recovery.verified(stage, result['execution'])
        return artifact
    return repair(flow, artifact, regression, result, 'early_supplemental')


def profiles(flow, artifact, suite, phase):
    from .sandbox_profiles import normalize_verification_profile, verification_profile_identity
    from .whole_project_sandbox import execute_verification_profile
    for profile_index, configured in enumerate(flow.bundle['build'].get('verification_profiles', [])):
        profile = normalize_verification_profile(configured)
        if profile['phase'] != phase:
            continue
        expected = verification_profile_identity(profile, flow.bundle['sandbox']['image_id'])
        checks = flow.evidence.setdefault('verification_profile_checks', {}).setdefault(profile['name'], [])
        if len(checks) > flow.limits['max_repair_attempts']:
            stop(flow, 'Verification profile exhausted its frozen execution budget')
        stage = f'whole_profile_{profile_index}_{len(checks)}'
        flow.stage = stage
        result = execute_verification_profile(
            flow, artifact, suite, stage, 8600 + profile_index * 32 + len(checks), profile)
        if (result.get('artifact_digest') != digest(artifact.model_dump())
                or result.get('image_id') != flow.bundle['sandbox']['image_id']
                or result.get('effective_runtime') != expected
                or result.get('run_id') != flow.state['run_id']):
            stop(flow, 'Verification profile receipt does not bind this run, source and runtime')
        checks.append(result)
        flow.stages[stage] = {key: result[key] for key in (
            'status', 'artifact_digest', 'profile_digest', 'execution_settled')}
        snapshot(flow)
        if result['status'] != 'passed' or result.get('execution_settled') is not True:
            return repair(flow, artifact, suite, result, profile['name'])
        if getattr(flow, 'recovery', None) is not None:
            flow.recovery.verified(stage, result['candidate']['execution'])
    return artifact


def candidate_gates(flow, artifact, suite, baseline):
    if flow.bundle.get('whole_project_gate_policy') != POLICY:
        return artifact
    if not execution_settled(baseline) or not validate_baseline(baseline, suite):
        return artifact
    independent_stage = flow.stage
    from .candidate_source_contract import gate as source_gate
    checked=source_gate(flow,artifact,suite)
    if checked is not artifact:return checked
    checked = supplemental(flow, artifact, suite)
    if checked is not artifact:
        return checked
    regression = flow.bundle.get('whole_project_regression_suite')
    tests = validate_regression_suite(regression, flow.bundle, flow.output['plan']['implementation_files']) if regression else suite
    checked = profiles(flow, artifact, tests, 'candidate')
    flow.stage = independent_stage
    return checked


def final_gates(flow, artifact):
    if flow.bundle.get('whole_project_gate_policy') != POLICY:
        return True, artifact
    suite = flow.suite_type.model_validate(flow.output['suite'])
    from .candidate_source_contract import gate as source_gate
    checked=source_gate(flow,artifact,suite)
    if checked is not artifact:return False,checked
    checked = profiles(flow, artifact, suite, 'final')
    return checked is artifact, checked
