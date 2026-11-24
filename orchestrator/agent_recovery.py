"""Agent consultation for unfamiliar failures; advice never becomes executable code."""
from copy import deepcopy
from pathlib import Path
import re
import traceback

from pydantic import Field
from typing import Literal

from .schemas import Record
from .store import digest, encoded

INLINE_RECOVERY_POLICY = 'inline-agent-recovery-v1'
AGENT_STAGE = re.compile(r'whole_recovery_(?:[1-9]|1[0-6])_(?:consult|retry_consult|output)')


class RecoveryAdvice(Record):
    diagnosis: str = Field(min_length=1, max_length=1600)
    evidence_quote: str = Field(min_length=1, max_length=1200)
    action: Literal['correct_stage_output', 'request_capability', 'stop']
    requested_changes: list[str] = Field(default_factory=list, max_length=8)
    required_capability: str = Field(default='', max_length=600)


def excerpt(value, limit=12000):
    material = encoded(value).encode('utf8')
    return {'digest':digest(value), 'complete':len(material)<=limit,
            'omitted_utf8_bytes':max(0,len(material)-limit),
            'json_excerpt':material[:limit].decode('utf8','ignore')}


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
    key = digest({'stage':stage, 'error':message})
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
        and flow.counters['recovery_output_correction'] < DYNAMIC_POLICY['max_output_corrections'] else [])
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
    if isinstance(error,Exception):
        data['runtime_frames'] = [{'module':Path(frame.filename).name,'function':frame.name,'line':frame.lineno}
            for frame in traceback.extract_tb(error.__traceback__)[-8:]
            if Path(frame.filename).parent == Path(__file__).parent]
    if context is not None:
        data.update(expected_schema=excerpt(context['model_type'].model_json_schema()),
                    rejected_output=excerpt(context['value']))
    saved_stage = flow.stage
    recovery._consulting = True
    try:
        raw, consultation_stage, _ = flow._repair_decision(f'whole_recovery_{index}_consult', RecoveryAdvice,
            'The orchestrator has no successful handler for this failure. Diagnose the retained evidence '
            'and choose a next action. Quote the failure exactly. Use correct_stage_output only when it '
            'is an available host capability; specify minimal corrections preserving all immutable contracts. '
            'Otherwise request the missing capability with actionable requirements, or stop if the approved '
            'budget cannot permit recovery. Excerpts explicitly marked incomplete are not complete source. '
            'Do not invent evidence, execute commands, modify runtime code, change models/budgets or waive gates.',
            data, role=role, tool_policy=INLINE_RECOVERY_POLICY)
        advice = RecoveryAdvice.model_validate(raw)
        if advice.evidence_quote not in message:
            raise ValueError('Recovery agent did not quote the retained failure')
        if advice.action == 'correct_stage_output' and (not capabilities or not advice.requested_changes):
            raise ValueError('Recovery agent requested an unavailable output correction')
        if advice.action == 'request_capability' and not advice.required_capability.strip():
            raise ValueError('Recovery agent did not describe the missing capability')
        request = recovery.request(stage, role, advice.action,
            evidence={'error':message, 'advice':advice.model_dump()},
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
    try:
        return validator(value)
    except Exception as error:
        if context is None or context['value_digest'] != digest(value):raise
        proof = consult(recovery, context['stage'], error, context)
        if proof is None or proof['advice']['action'] != 'correct_stage_output':raise
        attempted = getattr(recovery, '_output_attempts', set())
        recovery._output_attempts = attempted
        if (proof['request_id'] in attempted or flow.counters['recovery_output_correction']
                >= DYNAMIC_POLICY['max_output_corrections']):raise
        attempted.add(proof['request_id'])
        flow.counters['recovery_output_correction'] += 1
        flow.evidence['budget_counters'] = dict(flow.counters)
        flow.runtime.store.snapshot_result(flow.state['run_id'], {'output':flow.output, 'stages':flow.stages})
        saved_stage = flow.stage
        authority = {'stage':f"whole_recovery_{proof['index']}_output", 'role':context['role'],
                     'schema_digest':digest(context['model_type'].model_json_schema())}
        previous_authority = getattr(flow,'_agent_output_authority',None)
        flow._agent_output_authority = authority
        try:
            corrected = flow._generate(f"whole_recovery_{proof['index']}_output", context['role'],
                context['model_type'], context['instruction'] +
                ' Correct the rejected output using the retained validation failure and agent advice. '
                'Preserve the original scope and contract. The host will repeat the same validator; '
                'no gate or budget can be waived.',
                {**deepcopy(context['data']), 'rejected_output':value,
                 'validation_failure':str(error)[:1600], 'agent_advice':proof['advice']},
                {}, tool_policy=INLINE_RECOVERY_POLICY)
            result = validator(corrected)
            context.update(value=deepcopy(corrected), value_digest=digest(corrected))
            request = next(r for r in recovery.requests if r['id']==proof['request_id'])
            request.update(status='output_revalidated', corrected_output_digest=digest(corrected),
                           acceptance_credit=False)
            return result
        except Exception as failure:
            from .build_workflow import public_error
            request = next(r for r in recovery.requests if r['id']==proof['request_id'])
            request.update(status='output_correction_rejected', rejection=public_error(failure), acceptance_credit=False)
            raise
        finally:
            flow.stage = saved_stage
            flow._generation_context = context
            flow._agent_output_authority = previous_authority
