"""Shared failure assessment and bounded, source-grounded repair requests.

Verifier exit categories are evidence, not a diagnosis of fault ownership.
Unknown execution is a hard barrier; settled failures may be diagnosed without
being promoted to a pass or changing the retained execution receipt.
"""
from copy import deepcopy
import hashlib
import re
from typing import Literal

from pydantic import Field

from .schemas import Record
from .store import digest

KEY = 'whole_project_stage_recovery_policy'
POLICY = {'policy': 'evidence-routed-stage-recovery-v1', 'max_diagnoses': 16,
          'max_requests': 64, 'max_identical_diagnoses': 2}
DYNAMIC_POLICY = {**POLICY, 'policy':'agent-consulted-stage-recovery-v2', 'max_output_corrections':4}


def enabled(bundle):
    value = bundle.get(KEY)
    if value is None:
        return False
    if (value not in (POLICY,DYNAMIC_POLICY) or any(type(v) is not int for k,v in value.items() if k!='policy')
            or bundle.get('whole_project_repair_policy') != 'verified-scoped-repair-v1'):
        raise ValueError('Invalid approved stage recovery policy')
    return True


def dynamic_enabled(bundle):
    return enabled(bundle) and bundle[KEY] == DYNAMIC_POLICY


def validate_costs(bundle, counters):
    if not enabled(bundle):
        return
    limits = {'recovery_diagnosis': POLICY['max_diagnoses'], 'recovery_request': POLICY['max_requests']}
    if dynamic_enabled(bundle):
        from .repair_allowance import output_limit,validate_floors,coordinated_limit
        limits['recovery_output_correction'] = output_limit(bundle)
        validate_floors(bundle,counters)
        limits['coordinated_patch'] = coordinated_limit(bundle)
        from .recovery_limits import approved
        if explicit:=approved(bundle):limits['build_tests_defect']=explicit['test_defect_repairs']
        from .reference_context import observation_limit
        limits['recovery_context_observation'] = observation_limit(bundle)
        from .checkpoint_repair_continuation import POLICY as EPOCH,KEY as CONTINUATION,approved_budgets
        epoch=bundle.get(CONTINUATION,{})
        if epoch.get('policy')==EPOCH and 'test_defect_repairs' in approved_budgets(epoch['fresh_budgets']):
            limits['build_tests_defect']=epoch['fresh_budgets']['test_defect_repairs']
    for key, count in counters.items():
        if key == 'recovery_output_correction' and not dynamic_enabled(bundle) and count:
            raise ValueError('Agent output correction costs require their explicit V2 approval')
        maximum = POLICY['max_identical_diagnoses'] if key.startswith('recovery_issue_') else limits.get(key)
        if maximum is not None and (type(count) is not int or not 0 <= count <= maximum):
            raise ValueError('Acceptance checkpoint has invalid stage recovery costs')


def route(stage):
    """Every stage has an owner, including failures outside model execution."""
    if stage.startswith('whole_source_contract'):
        return 'candidate_verification','repairer'
    if stage.startswith('delivery'):
        return 'delivery', 'operator'
    if 'code_review' in stage:
        return 'review', 'reviewer'
    if 'compile_' in stage:
        return 'compile', 'repairer'
    if stage.startswith('whole_self_verify_final') or stage == 'build_project_acceptance':
        return 'final_verification', 'repairer'
    if stage.startswith('whole_profile_'):
        return 'verification_profile', 'repairer'
    if stage.startswith('whole_regression_'):
        return 'supplemental_verification', 'repairer'
    if stage.startswith(('whole_self_reference', 'whole_smoke_reference')):
        return 'smoke_qualification', 'implementer'
    if stage.startswith('whole_self_tests'):
        return 'smoke_authoring', 'implementer'
    if stage.startswith(('whole_self_verify', 'whole_self_repair')):
        return 'smoke_verification', 'repairer'
    if stage.startswith(('build_control', 'build_tests_defect', 'build_tests_diagnose','whole_reference_context')):
        return 'independent_qualification', 'test_author'
    if stage.startswith(('build_tests', 'build_test_preflight')):
        return 'independent_authoring', 'test_author'
    if stage.startswith(('build_plan', 'build_project_plan')):
        return 'planning', 'planner'
    if stage.startswith('implement'):
        return 'implementation', 'implementer'
    if stage.startswith(('build_verify', 'repair_')):
        return 'candidate_verification', 'repairer'
    return 'infrastructure', 'operator'


def correction_stage(stage):
    return (stage.startswith(('repair_', 'whole_self_repair', 'whole_self_reference_repair', 'build_tests_defect', 'build_tests_diagnose',
                              'build_tests_syntax', 'build_tests_fixture', 'build_tests_coverage', 'build_tests_strengthen'))
            or 'compile_repair_' in stage or stage.endswith(('_retry', '_citation_retry')))


class SourceAnchor(Record):
    path: str = Field(min_length=1, max_length=240)
    line: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=600)


class FailureDiagnosis(Record):
    owner: Literal['application', 'test_author', 'verifier', 'infrastructure', 'unknown']
    action: Literal['fix_source', 'request_operator']
    diagnosis: str = Field(min_length=1, max_length=1600)
    receipt_quote: str = Field(min_length=1, max_length=1200)
    source_anchors: list[SourceAnchor] = Field(default_factory=list, max_length=16)
    requested_changes: list[str] = Field(default_factory=list, max_length=8)


def settled_source_failure(flow, artifact, suite, receipt, *, checkpoint=None, profile=None):
    """Bind a failed process to exact current source and immutable tooling."""
    from .project_build import execution_settled, matching_image_checks, sandbox_stage_results
    files = [*artifact.files, *(suite.files if suite is not None else [])]
    expected = {f.path: hashlib.sha256(f.content.encode('utf-8')).hexdigest() for f in files}
    rows, sandbox = receipt.get('commands') or [], receipt.get('sandbox') or {}
    image = flow.bundle['sandbox']['image_id']
    if (len(rows) != 1 or not execution_settled(receipt) or sandbox.get('image_id') != image
            or sandbox.get('network') != 'disabled' or sandbox.get('read_only_root') is not True
            or sandbox.get('read_only_project') is not True or rows[0].get('source_unchanged') is not True
            or rows[0].get('source_sha256') != expected or rows[0].get('exit_code') not in (1, 125)):
        return None
    requirements = (profile.get('verification') if profile else None) or flow.environment['verification']
    marker = requirements.get('marker', 'OFFLINE_PROJECT_RESULT')
    if marker == 'OFFLINE_PROJECT_RESULT':
        summary = sandbox_stage_results(rows[0])
    else:
        from .build_sandbox import final_json_record
        summary = final_json_record(rows[0].get('stderr', ''), marker)
    if checkpoint is not None:
        requirements = {'policy': flow.environment['verification']['policy'],
                        'required_checks': checkpoint['required_checks']}
        if not summary or summary.get('checkpoint') != checkpoint['name']:
            return None
        profile = checkpoint.get('verification_profile')
    if (summary is None or not matching_image_checks(receipt, [summary], requirements)
            or summary.get('status') not in ('failed', 'review_required') or not summary.get('failures')):
        return None
    if profile is not None:
        from .sandbox_profiles import verification_profile_identity
        from .build_sandbox_lifecycle import complete_stdout_capture
        identity = verification_profile_identity(profile, image)
        if (sandbox.get('effective_runtime') != identity or rows[0].get('effective_runtime') != identity
                or rows[0].get('profile_sources_unchanged') is not True
                or profile.get('require_complete_stdout', False)
                and not complete_stdout_capture(rows[0], profile.get('stdout_capture_bytes', 4000))):
            return None
    elif sandbox.get('effective_runtime') is not None:
        return None
    return summary


def validate_diagnosis(raw, files, diagnostic, byte_limit):
    value = FailureDiagnosis.model_validate(raw)
    if not value.receipt_quote.strip() or value.receipt_quote not in diagnostic:
        raise ValueError('Recovery diagnosis does not quote the retained failure evidence')
    if value.action != 'fix_source':
        if value.owner == 'application':
            raise ValueError('Application diagnosis requires a scoped source request')
        return value, []
    if (value.owner != 'application' or not value.source_anchors or not value.requested_changes
            or any(not change.strip() or len(change) > 600 for change in value.requested_changes)):
        raise ValueError('Source recovery requires a causal diagnosis and current-source anchors')
    inventory = {f.path: f.content for f in files}
    paths = set()
    for anchor in value.source_anchors:
        lines = inventory.get(anchor.path, '').splitlines()
        if (anchor.path not in inventory or anchor.line > len(lines)
                or not anchor.quote.strip() or anchor.quote not in lines[anchor.line - 1]):
            raise ValueError('Recovery anchor does not match an eligible current source line')
        paths.add(anchor.path)
    if sum(len(inventory[p].encode('utf-8')) for p in paths) > byte_limit:
        raise ValueError('Recovery selection exceeds the approved repair source limit')
    return value, sorted(paths)


class StageRecovery:
    def __init__(self, flow):
        self.flow = flow
        self.requests = flow.output.setdefault('recovery', {'policy':flow.bundle[KEY]['policy'], 'requests': []})['requests']
        self.authorized = {}
        self.dispatch_requests = set()

    def request(self, stage, owner, action, *, evidence=None, paths=(), status='requested', reason=''):
        flow = self.flow
        if flow.counters['recovery_request'] >= POLICY['max_requests']:
            return None
        phase, _ = route(stage)
        value = {'stage': stage, 'phase': phase, 'owner': owner, 'action': action,
                 'status': status, 'source_paths': sorted(paths), 'reason': reason[:1600],
                 'evidence_digest': digest(evidence), 'verification': 'repeat_original_gate_then_all_downstream_gates',
                 'immutable_contracts': ['task', 'test_identities', 'test_assertions', 'verifier', 'model', 'budgets']}
        value['id'] = digest(value)
        if any(r['id'] == value['id'] for r in self.requests):
            return value
        self.requests.append(value)
        flow.counters['recovery_request'] += 1
        flow.evidence['budget_counters'] = dict(flow.counters)
        index = flow.counters['recovery_request']
        flow.runtime.store.save_artifact(flow.state['run_id'], f'whole_recovery_request_{index}', 'raw', value)
        return value

    def verified(self, stage, receipt):
        """Record closure only after the original verifier accepted a recheck."""
        scope = stage.rsplit('_', 1)[0]
        for request in self.requests:
            if (request['status'] == 'authorized' and route(request['stage'])[0] == route(stage)[0]
                    and request['stage'].rsplit('_', 1)[0] == scope):
                request['status'] = 'gate_rechecked'
                request['recheck'] = {'stage': stage, 'receipt_digest': digest(receipt)}

    def dispatch(self, stage, role, arguments):
        if correction_stage(stage) and stage not in self.dispatch_requests:
            paths = arguments.get('editable_paths') or []
            request = self.request(stage, role, 'correct_stage_output', evidence=arguments, paths=paths, status='dispatch_requested',
                                   reason='Existing bounded stage correction; original scope and verification remain authoritative')
            if request is None:
                raise ValueError('Recovery request budget is exhausted; worker dispatch was not authorized')
            self.dispatch_requests.add(stage)

    def terminal(self, stage, message, *, execution_unknown=False, error=None):
        _, owner = route(stage)
        unknown = execution_unknown or any(marker in message.lower() for marker in (
            'native_completion_unknown', 'completion is unknown', 'unresolved owned', 'cleanup incomplete', 'ownership mismatch'))
        if dynamic_enabled(self.flow.bundle):
            from .agent_recovery import barrier
            unknown = unknown or bool(barrier(self.flow,message))
        consultation = self.consult(stage,error if error is not None else message) if dynamic_enabled(self.flow.bundle) and not unknown else None
        value = self.request(stage, 'operator' if unknown else owner,
            'resolve_execution' if unknown else 'correct_stage_output', evidence=message,
            status='blocked', reason=message)
        if value is None:
            self.flow.output['recovery']['terminal_request'] = {
                'stage': stage, 'phase': route(stage)[0], 'owner': 'operator',
                'action': 'assess_exhausted_recovery', 'status': 'blocked', 'reason': message[:1600]}
        elif consultation is not None:
            value['agent_consultation'] = consultation
        return value

    def consult(self, stage, error, context=None):
        if not dynamic_enabled(self.flow.bundle):return None
        from .agent_recovery import consult
        return consult(self,stage,error,context)

    def validate_output(self, value, validator, context=None):
        if not dynamic_enabled(self.flow.bundle):return validator(value)
        from .agent_recovery import validate_output
        return validate_output(self,value,validator,context)

    def repair_patch(self, **capability):
        """Every gate can supply its scope and verifier to the same bounded repair capability."""
        if not dynamic_enabled(self.flow.bundle):raise ValueError('Coordinated repair requires dynamic recovery approval')
        from .repair_coordinator import repair
        return repair(self,**capability)

    def diagnose(self, artifact, suite, receipt, *, checkpoint=None, profile=None):
        flow, failed_stage = self.flow, self.flow.stage
        summary = settled_source_failure(flow, artifact, suite, receipt, checkpoint=checkpoint, profile=profile)
        if summary is None:
            self.terminal(failed_stage, 'Execution, source or verifier identity is unresolved; diagnosis cannot authorize a retry',
                          execution_unknown=True)
            return None
        from .project_build import failure_feedback
        from .whole_project_selection import INLINE_SELECTION_POLICY, selection_source
        from .whole_project_repair_evidence import repair_feedback
        feedback = failure_feedback(receipt, include_stage_results=True)
        evidence = repair_feedback(artifact, {'failed_execution': feedback}, getattr(flow, '_repair_history', []))['repair_evidence']
        # Only candidate failure excerpts enter a diagnostic prompt. Never use
        # an entire profile result containing reference source or reference logs.
        diagnostic = '\n'.join(f.get('diagnostic_excerpt') or f.get('message', '') for f in summary['failures'])
        fingerprint = digest({'phase': route(failed_stage)[0], 'diagnostic': diagnostic})
        repeated_key = 'recovery_issue_' + fingerprint[:32]
        if (flow.counters['recovery_diagnosis'] >= POLICY['max_diagnoses']
                or flow.counters[repeated_key] >= POLICY['max_identical_diagnoses']):
            self.request(failed_stage, 'operator', 'assess_exhausted_recovery', evidence=feedback, status='blocked',
                         reason='The frozen diagnostic or repeated-issue budget is exhausted')
            return None
        flow.counters['recovery_diagnosis'] += 1
        flow.counters[repeated_key] += 1
        flow.evidence['budget_counters'] = dict(flow.counters)
        flow.runtime.store.snapshot_result(flow.state['run_id'], {'output': flow.output, 'stages': flow.stages})
        stage = f"whole_recovery_{flow.counters['recovery_diagnosis']}_select"
        tree = [{'path': f.path, 'bytes': len(f.content.encode('utf-8')), 'sha256': digest(f.content)} for f in artifact.files]
        instruction = (
            'Diagnose why this settled candidate execution failed using current source, pinned public dependency context '
            'and retained diagnostics. The verifier exit category is not proof of fault ownership. A generated build '
            'configuration, source or startup setting may cause a dependency or bootstrap failure. Distinguish that '
            'from a test-fixture concern, verifier defect, missing preloaded tools, unavailable dependencies, resource limits or infrastructure. '
            'For an application cause, request minimal changes to existing application files and cite their exact '
            'current line numbers and source quotes plus a quote from the failure evidence. Otherwise request operator '
            'attention. Never change the task, tests, verifier, dependency pins, model, budgets or execution commands. '
            'Do not grant passing credit or invent a missing environment capability. Account for prior_attempts and '
            'source_facts, separating observed facts from hypotheses. Return concise explanations. Make one decision without tools.')
        data = {'failed_stage': failed_stage, 'failure_evidence': feedback, 'implementation_tree': tree,
                'repair_evidence': evidence,
                'current_source': selection_source(artifact.files, {**feedback, 'repair_evidence': evidence}),
                'project_dependency_reference': getattr(flow, 'dependency_reference', None)}
        try:
            raw, _, _ = flow._repair_decision(stage, FailureDiagnosis, instruction, data)
            value, paths = flow.validate_generated(raw, lambda response:validate_diagnosis(
                response,artifact.files,diagnostic,flow.contract['repair_selected_bytes']))
        finally:
            flow.stage = failed_stage
        owner = 'repairer' if paths else 'test_author' if value.owner == 'test_author' else 'operator'
        action = 'request_test_correction' if value.owner == 'test_author' else value.action
        request = self.request(failed_stage, owner, action,
            evidence={'execution_digest': digest(receipt), 'diagnosis': value.model_dump()}, paths=paths,
            status='authorized' if paths else 'blocked', reason=value.diagnosis)
        if not paths or request is None:
            return None
        proof = {'request_id': request['id'], 'artifact_digest': digest(artifact.model_dump()),
                 'execution_digest': digest(receipt), 'paths': paths, 'diagnosis': value.model_dump()}
        self.authorized[request['id']] = deepcopy(proof)
        return proof

    def selection(self, proof, artifact):
        if (self.authorized.get(proof.get('request_id')) != proof
                or proof.get('artifact_digest') != digest(artifact.model_dump())):
            raise ValueError('Recovery request does not bind this source and retained diagnosis')
        return list(proof['paths'])


def external_fix_request(stage, error):
    """Host export/bootstrap failures belong to the operator, never a model replay."""
    return {'policy': POLICY['policy'], 'stage': stage, 'phase': route(stage)[0],
            'owner': 'operator', 'action': 'correct_host_stage', 'status': 'blocked',
            'reason': str(error)[:1600], 'error_type': type(error).__name__,
            'no_retry_or_resume_performed': True,
            'verification': 'validate_frozen_inputs_then_repeat_only_a_settled_authorized_operation'}
