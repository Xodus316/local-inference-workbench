"""Agent consultation for unfamiliar failures; advice never becomes executable code."""
from copy import deepcopy
from pathlib import Path
import re
import traceback

from pydantic import Field,create_model,model_validator
from typing import Literal,ClassVar

from .schemas import Record
from .store import digest, encoded
from .repair_allowance import output_limit

INLINE_RECOVERY_POLICY = 'inline-agent-recovery-v1'
AGENT_STAGE = re.compile(r'whole_recovery_(?:[1-9]|1[0-6])_(?:consult|retry_consult|output)')


class AgentOutputValidationError(ValueError):
    """A settled agent response failed its original structured-output schema."""
    def __init__(self, value, error):
        from .build_workflow import public_error
        self.value = deepcopy(value)
        super().__init__(public_error(error))


class RecoveryAdvice(Record):
    diagnosis: str = Field(min_length=1, max_length=1600)
    evidence_quote: str = Field(min_length=1, max_length=1200)
    action: Literal['correct_stage_output', 'request_capability', 'stop']
    requested_changes: list[str] = Field(default_factory=list, max_length=8)
    required_capability: str = Field(default='', max_length=600)


class EvidenceRecoveryAdvice(Record):
    conditional_actions:ClassVar[bool]=False
    diagnosis: str = Field(min_length=1,max_length=1600)
    evidence_ref: str = Field(min_length=1,max_length=64)
    action: Literal['correct_stage_output','request_capability','stop']
    requested_changes: list[str] = Field(default_factory=list,max_length=8)
    required_capability: str = Field(default='',max_length=600)

    @model_validator(mode='before')
    @classmethod
    def narrative(cls,value):
        from .repair_coordinator import project_narrative
        return project_narrative(value,{'diagnosis':1600,'required_capability':600})

    @model_validator(mode='after')
    def actionable(self):
        if self.conditional_actions:
            if self.action=='correct_stage_output':
                if not self.requested_changes or any(not x.strip() for x in self.requested_changes):
                    raise ValueError('correct_stage_output requires a nonempty requested_changes list describing the executable correction')
                if self.required_capability:
                    raise ValueError('correct_stage_output requires an empty required_capability')
            if self.action=='request_capability' and not self.required_capability.strip():
                raise ValueError('Recovery agent did not describe the missing capability')
        return self

    @classmethod
    def model_json_schema(cls,*args,**kwargs):
        value=super().model_json_schema(*args,**kwargs)
        if cls.conditional_actions:
            reference=value['properties'].get('evidence_ref',{})
            if 'const' in reference:reference['enum']=[reference['const']]
            value['oneOf']=[
                {'properties':{'action':{'type':'string','enum':['correct_stage_output']},
                    'requested_changes':{'type':'array','items':{'type':'string','minLength':1},'minItems':1,'maxItems':8},
                    'required_capability':{'type':'string','enum':[''],'maxLength':0}},'required':['action','requested_changes']},
                {'properties':{'action':{'type':'string','enum':['request_capability']},
                    'required_capability':{'type':'string','minLength':1,'maxLength':600}},'required':['action','required_capability']},
                {'properties':{'action':{'type':'string','enum':['stop']}},'required':['action']}]
        return value


def advice_type(evidence_ref):
    model=create_model('BoundRecoveryAdvice',__base__=EvidenceRecoveryAdvice,
        evidence_ref=(Literal[evidence_ref],...))
    model.conditional_actions=True
    return model


def excerpt(value, limit=12000):
    material = encoded(value).encode('utf8')
    return {'digest':digest(value), 'complete':len(material)<=limit,
            'omitted_utf8_bytes':max(0,len(material)-limit),
            'json_excerpt':material[:limit].decode('utf8','ignore')}


def advice_retry_rejection(model_type,raw):
    """Keep the settled raw digest; the single format retry needs bounded advice, not repeated prose."""
    from .repair_coordinator import project_narrative
    projected=project_narrative(raw,{'diagnosis':1600,'required_capability':600})
    try:projected=model_type.model_validate(projected).model_dump()
    except ValueError:pass
    value=excerpt(projected,2400)
    return {**value,'digest':digest(raw),'projected_digest':digest(projected),
        'policy':'bounded-non-executable-advice-feedback-v1','raw_response_host_retained':True}


def barrier(flow, message):
    """Do not start another inference while physical execution is unresolved."""
    if any(word in message.lower() for word in ('native_completion_unknown', 'completion is unknown',
            'unresolved owned', 'cleanup incomplete', 'ownership mismatch')):
        return 'Physical execution or ownership is unresolved'
    store = flow.runtime.store
    if store.has_unknown_calls() is True or store.unsettled_operations(flow.state['run_id']):
        return 'A model or sandbox operation has no settled receipt'
    return None


def consult(recovery, stage, error, context=None):
    from .build_workflow import public_error
    from .stage_recovery import DYNAMIC_POLICY, route
    flow = recovery.flow
    message = public_error(error) if isinstance(error, Exception) else str(error)
    message = message[:1600]
    from .repair_coordinator import executable_edits
    identity={'stage':stage,'error':message}
    if context is not None and context['data'].get('repair_policy') in {'grouped-anchored-recovery-v2','per-edit-evidence-recovery-v3'}:
        identity['repair_revision']={'base_suite_digest':context['data'].get('base_suite_digest'),
            'failure_evidence_digest':digest(context['data'].get('failures',[])),
            'prior_rejections_digest':digest(context['data'].get('previous_attempts',[])),
            'executable_proposal_digest':digest(executable_edits(context.get('value',{})))}
        if context['data'].get('reference_context_observation'):
            identity['repair_revision']['context_receipt_digest']=context['data']['reference_context_observation']['receipt_digest']
    key = digest(identity)
    memo = getattr(recovery, '_consultations', {})
    if key in memo:
        return memo[key]
    recovery._consultations = memo
    if getattr(recovery, '_consulting', False) or AGENT_STAGE.fullmatch(stage):
        return None
    blocked = barrier(flow, message)
    repeated = 'recovery_issue_' + key[:32]
    if (blocked or flow.counters['recovery_diagnosis'] >= DYNAMIC_POLICY['max_diagnoses']
            or flow.counters[repeated] >= DYNAMIC_POLICY['max_identical_diagnoses']):
        recovery.request(stage, 'operator', 'defer_agent_consultation', evidence=message,
            status='blocked', reason=blocked or 'Approved consultation budget exhausted')
        memo[key] = None
        return None
    guard = getattr(flow.runtime, '_guard', None)
    if callable(guard):
        try:guard(flow.state['run_id'])
        except Exception as failure:
            recovery.request(stage,'operator','defer_agent_consultation',evidence=message,
                status='blocked',reason=public_error(failure))
            memo[key] = None
            return None
    flow.counters['recovery_diagnosis'] += 1
    flow.counters[repeated] += 1
    index = flow.counters['recovery_diagnosis']
    flow.evidence['budget_counters'] = dict(flow.counters)
    flow.runtime.store.snapshot_result(flow.state['run_id'], {'output':flow.output, 'stages':flow.stages})
    capabilities = (['correct_stage_output'] if context is not None
        and flow.counters['recovery_output_correction'] < output_limit(flow.bundle) else [])
    # Independent author material stays with that isolated author. Other
    # unfamiliar failures are diagnosed by the approved repair agent.
    role = 'test_author' if route(stage)[0] in {'independent_authoring','independent_qualification'} else 'repairer'
    data = {'failed_stage':stage, 'phase':route(stage)[0], 'failure':message,
            'error_type':type(error).__name__ if isinstance(error,Exception) else 'StageFailure',
            'available_capabilities':capabilities, 'spent_budgets':dict(flow.counters),
            'immutable_contracts':['task','application inventory','test identities and assertions',
                'verifier','model assignments','budgets','original and downstream acceptance gates']}
    data['workflow_state'] = {'run_id':flow.state['run_id'], 'approval_digest':digest(flow.bundle),
        'completed_stage_names':list(flow.stages)[-64:],
        'artifact_digest':digest(flow.output['artifact']) if flow.output.get('artifact') is not None else None,
        'suite_digest':flow.output.get('suite_digest')}
    from .recovery_limits import approved
    if explicit:=approved(flow.bundle):data['effective_repair_capacity']=explicit
    if isinstance(error,Exception):
        data['runtime_frames'] = [{'module':Path(frame.filename).name,'function':frame.name,'line':frame.lineno}
            for frame in traceback.extract_tb(error.__traceback__)[-8:]
            if Path(frame.filename).parent == Path(__file__).parent]
        from .worker_progress import OpenCodeProgressError
        if isinstance(error,OpenCodeProgressError):data['worker_progress']=dict(error.proof)
        from .repair_diagnostics import PatchValidationError
        from .reference_repair_feedback import ReferencePatchError
        if isinstance(error,(PatchValidationError,ReferencePatchError)):data['composed_patch_rejection']=deepcopy(error.feedback)
        from .build_coverage import TestSyntaxError
        if isinstance(error,TestSyntaxError):data['parser_diagnostics']=deepcopy(error.diagnostics)
        if getattr(error,'not_dispatched',False) is True:
            data['dispatch_proof']={'not_dispatched':True,'original_output_available':False,
                'recovery_scope':'Produce the original schema-bound output through the approved recovery capability; no model, budget or gate changes.'}
    if context is not None:
        contract_inputs=context['data']
        if context['data'].get('repair_policy') in {'grouped-anchored-recovery-v1','grouped-anchored-recovery-v2','per-edit-evidence-recovery-v3'}:
            data['coordinated_repair']={
                'input_kind':'host_anchored_patch_request; no initial model patch is claimed',
                'failure_observations':excerpt(context['data'].get('failures',[]),24000),
                'scope':context['data'].get('scope',[]),
                'root_cause_hints':excerpt(context['data'].get('root_cause_hints',[]),6000),
                'previous_attempts':deepcopy(context['data'].get('previous_attempts',[])),
                'base_suite_digest':context['data'].get('base_suite_digest'),
                'verification_rule':'Promote only a strictly smaller failed-case set with no regressions; all original gates remain required.'}
            for name in ('reference_context_observation','effective_repair_capacity'):
                if name in context['data']:data['coordinated_repair'][name]=deepcopy(context['data'][name])
            base=context['data'].get('base_suite')
            if isinstance(base,dict) and isinstance(base.get('files'),list):
                if digest(base)!=context['data'].get('base_suite_digest'):
                    raise ValueError('Coordinator source context differs from its captured suite digest')
                scope=set(context['data'].get('scope',[]));files=base['files']
                if not scope<={f['path'] for f in files}:
                    raise ValueError('Coordinator source scope exceeds its captured suite')
                metadata=deepcopy({k:v for k,v in base.items() if k not in {'files','coverage'}})
                if 'coverage' in base:
                    metadata['coverage']={'host_retained':True,'digest':digest(base['coverage']),
                        'entries':len(base['coverage']),'editable':False}
                data['coordinated_repair'].update(source_complete=True,
                    editable_source=deepcopy([f for f in files if f['path'] in scope]),
                    read_only_support_source=deepcopy([f for f in files if f['path'] not in scope]),
                    immutable_suite_metadata=metadata,
                    producer_receives_original_complete_inputs=True)
                # Source requiring byte-exact anchors must never be hidden in a generic excerpt.
                # Keep one complete copy and let the pinned tokenizer admit the entire prompt.
                contract_inputs={**context['data'],'base_suite':{'digest':digest(base),
                    'complete_source_location':'coordinated_repair.editable_source and read_only_support_source',
                    'metadata_location':'coordinated_repair.immutable_suite_metadata'}}
                for key,location in [('failures','failure_observations'),('root_cause_hints','root_cause_hints'),('previous_attempts','previous_attempts'),('reference_context_observation','reference_context_observation'),('effective_repair_capacity','effective_repair_capacity')]:
                    if key in contract_inputs:
                        contract_inputs[key]={'digest':digest(context['data'][key]),'location':'coordinated_repair.'+location}
                if 'read_only_application_inventory' in contract_inputs:
                    contract_inputs['read_only_application_inventory']={
                        'digest':digest(context['data']['read_only_application_inventory']),
                        'location':'coordinated_repair.immutable_suite_metadata.implementation_files'}
        data.update(expected_schema=excerpt(context['model_type'].model_json_schema()),
                    rejected_output=excerpt(context['value']),
                    original_stage_contract={
                        'stage':context['stage'], 'role':context['role'],
                        'instruction':excerpt(context['instruction'],8000),
                        'inputs':excerpt(contract_inputs,24000),
                        'correction_scope':'Correct the rejected generated output under this original contract. '
                            'A validator rejection does not authorize changing retained inputs, coverage mappings '
                            'or other immutable artifacts. The same validator must accept the correction.'})
        if context['data'].get('repair_policy') in {'grouped-anchored-recovery-v2','per-edit-evidence-recovery-v3'} and context['value'].get('edits'):
            data['rejected_output']={'host_retained':True,'digest':digest(context['value']),
                'executable_edits_digest':digest(executable_edits(context['value'])),
                'replacement_base':'Original base_suite; the rejected proposal was never applied.'}
    evidence_ref='F'+digest(message)[:12]
    model_type=advice_type(evidence_ref)
    data['failure_evidence_id']=evidence_ref
    saved_stage = flow.stage
    recovery._consulting = True
    def checked_advice(raw):
        advice=model_type.model_validate(raw)
        if advice.action=='correct_stage_output':
            if not capabilities:raise ValueError('Recovery agent requested an unavailable output correction')
            if not advice.requested_changes or any(not change.strip() for change in advice.requested_changes):
                raise ValueError('correct_stage_output requires a nonempty requested_changes list describing the executable correction')
        if advice.action=='request_capability' and not advice.required_capability.strip():
            raise ValueError('Recovery agent did not describe the missing capability')
        return advice
    try:
        raw, consultation_stage, _ = flow._repair_decision(f'whole_recovery_{index}_consult', model_type,
            'The orchestrator has no successful handler for this failure. Diagnose the retained evidence '+
            'and choose a next action. Select the exact failure_evidence_id in evidence_ref. '+
            'Use correct_stage_output only when it '
            'is an available host capability. For correct_stage_output, put 1 to 8 concrete executable corrections in requested_changes (never leave it empty); diagnosis is evidence, not a substitute for this list, and required_capability must be empty. Preserve all immutable contracts. '
            'Otherwise request the missing capability with actionable requirements, or stop if the approved '
            'budget cannot permit recovery. Excerpts explicitly marked incomplete are not complete source. '
            'Do not invent evidence, execute commands, modify runtime code, change models/budgets or waive gates.',
            data, role=role, tool_policy=INLINE_RECOVERY_POLICY,validator=checked_advice)
        advice = checked_advice(raw)
        request = recovery.request(stage, role, advice.action,
            evidence={'error':message, 'advice':advice.model_dump(),
                **({'repair_revision':identity['repair_revision']} if 'repair_revision' in identity else {})},
            status='authorized' if advice.action in capabilities else 'blocked', reason=advice.diagnosis)
        if request is None:raise ValueError('Recovery request journal is exhausted')
        proof = {'index':index, 'request_id':request['id'], 'advice':advice.model_dump()}
        request['agent_consultation'] = {'stage':consultation_stage, **proof}
        memo[key] = proof
        return proof
    except Exception as failure:
        recovery.request(stage, role, 'agent_consultation_failed', evidence=message,
            status='blocked', reason=public_error(failure))
        memo[key] = None
        return None
    finally:
        recovery._consulting = False
        flow.stage = saved_stage


def validate_output(recovery, value, validator, context):
    """Correct arbitrary schema/semantic failures through the same validator."""
    from .stage_recovery import DYNAMIC_POLICY
    flow = recovery.flow
    attempted = getattr(recovery, '_output_attempts', set())
    recovery._output_attempts = attempted
    pending = None
    while True:
        try:
            result = validator(value)
        except Exception as error:
            if pending is not None:
                from .build_workflow import public_error
                pending.update(status='output_correction_rejected', rejection=public_error(error), acceptance_credit=False)
            if context is None or context['value_digest'] != digest(value):raise
            if context['data'].get('repair_policy') in {'grouped-anchored-recovery-v2','per-edit-evidence-recovery-v3'} and isinstance(context['data'].get('base_suite'),dict):
                from .repair_coordinator import prior_attempts
                suite=flow.suite_type.model_validate(context['data']['base_suite'])
                context['data']['previous_attempts']=prior_attempts(flow.evidence.get('coordinated_repairs',[]),flow=flow,suite=suite)
            proof = consult(recovery, context['stage'], error, context)
            if proof is None or proof['advice']['action'] != 'correct_stage_output':raise
            if (proof['request_id'] in attempted or flow.counters['recovery_output_correction']
                    >= output_limit(flow.bundle)):raise
            attempted.add(proof['request_id'])
            flow.counters['recovery_output_correction'] += 1
            flow.evidence['budget_counters'] = dict(flow.counters)
            flow.runtime.store.snapshot_result(flow.state['run_id'], {'output':flow.output, 'stages':flow.stages})
            saved_stage = flow.stage
            authority = {'stage':f"whole_recovery_{proof['index']}_output", 'role':context['role'],
                         'schema_digest':digest(context['model_type'].model_json_schema())}
            previous_authority = getattr(flow,'_agent_output_authority',None)
            flow._agent_output_authority = authority
            request = next(r for r in recovery.requests if r['id']==proof['request_id'])
            try:
                corrected = flow._generate(f"whole_recovery_{proof['index']}_output", context['role'],
                    context['model_type'], context['instruction'] +
                    ' Correct the rejected output using the retained validation failure and agent advice. '
                    'Preserve the original scope and contract. The host will repeat the same validator; '
                    'no gate or budget can be waived.',
                    {**deepcopy(context['data']), 'rejected_output':value,
                     'validation_failure':str(error)[:1600], 'agent_advice':proof['advice'],
                     **({'composed_patch_rejection':deepcopy(error.feedback)} if hasattr(error,'feedback') else {})},
                    {}, tool_policy=INLINE_RECOVERY_POLICY)
            except Exception as failure:
                from .build_workflow import public_error
                request.update(status='output_correction_rejected', rejection=public_error(failure), acceptance_credit=False)
                raise  # Never retry unknown execution or recursively repair an agent's malformed response.
            finally:
                flow.stage = saved_stage
                flow._generation_context = context
                flow._agent_output_authority = previous_authority
            context.update(value=deepcopy(corrected), value_digest=digest(corrected))
            value, pending = corrected, request
            # A distinct rejection can use remaining shared budget. Memoized advice cannot
            # execute twice, and every new consultation checks physical settlement again.
        else:
            if pending is not None:
                pending.update(status='output_revalidated', corrected_output_digest=digest(value), acceptance_credit=False)
            return result
