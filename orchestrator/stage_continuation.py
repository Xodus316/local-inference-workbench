"""Continue a settled semantic-output failure without replaying application generation."""
from copy import deepcopy
import re

from .store import digest
from .whole_project_checkpoint import binding_digest,validate_checkpoint

KEY = 'whole_project_stage_continuation'
POLICY = 'settled-stage-output-continuation-v1'
_MIGRATION = {'fresh_project','backend','config_digest','whole_project_engine_digest',
    'whole_project_workers','whole_project_prompt_tokenizers','same_model_role_isolation',KEY}


def contract_digest(bundle):
    return digest({k:v for k,v in bundle.items() if k not in _MIGRATION})


def pack(row, requests, raw, target, *, closed_execution_audit):
    """Trusted host first verifies frozen files, checkpoints, native DONE and exact owner cleanup."""
    from .whole_project import WholeProjectPlan,whole_project_types,_stopped_opencode_result
    source=row['bundle'];output=row['result']['output'];evidence=output['verification']['whole_project']
    if (row['status'] not in {'cancelled','failed','review_required'} or not row.get('approved')
            or row['digest'] != digest(source) or row['approval']['digest'] != row['digest']
            or contract_digest(source) != contract_digest(target)
            or not re.fullmatch('[a-f0-9]{64}',closed_execution_audit)
            or any(c['status'] != 'terminal' for c in row['calls'])):
        raise ValueError('Stage continuation requires settled approval, unchanged contracts and a closed execution audit')
    for field in ('model','context','thinking','model_manifest_sha256','backend_identity','sampling'):
        if source['backend'].get(field) != target['backend'].get(field):
            raise ValueError('Stage continuation cannot change the pinned inference profile')
    plan=WholeProjectPlan.model_validate(output['plan'])
    artifact_type,_=whole_project_types(source);artifact=artifact_type.model_validate(output['artifact'])
    files={};receipts={}
    frozen_tests={f['path']:f['content'] for f in evidence['self_suite']['files']}
    if KEY in source:
        parent_plan,parent_artifact=validate(source[KEY],source)
        if parent_plan.model_dump()!=plan.model_dump():raise ValueError('Inherited continuation plan changed')
        files={f.path:f.content for f in parent_artifact.files}
    for call in row['calls']:
        stage=call['stage'];request=requests[stage];response=raw[stage]
        if response.get('worker_error'):
            if (row['status']!='cancelled' or call is not row['calls'][-1]
                    or response != {'worker_error':'OpenCode cancelled','backend_cleanup_confirmed':True}):
                raise ValueError('Only the final settled operator-cancelled generation may lack output')
        elif not _stopped_opencode_result(response):
            raise ValueError('Source stage lacks stopped worker and scratch cleanup evidence')
        receipts[stage]={'request_digest':digest(request),'raw_digest':digest(response)}
        if request.get('operation')!='run':continue
        if not re.fullmatch(r'implement(?:_inventory_retry)?|build_compile_repair_[1-4]_[1-4](?:_edit_retry)?|whole_self_repair(?:_[23])?(?:_edit_retry)?',stage):
            raise ValueError('Unsupported application source transition in continuation')
        changes=response.get('files')
        before=request.get('visible_files',{});editable=set(request['editable_paths'])
        if (not isinstance(changes,dict) or not changes or not changes.keys() <= set(plan.implementation_files)
                or any(path not in {**frozen_tests,**files}
                       or digest(({**frozen_tests,**files})[path])!=binding for path,binding in before.items())
                or any(path not in editable and digest(text)!=before.get(path) for path,text in changes.items())):
            raise ValueError('Continuation edits exceed their journaled inventory or scope: '+stage)
        files.update(changes)
    if files != {f.path:f.content for f in artifact.files}:
        raise ValueError('Retained application differs from its completed worker chain')
    smoke=evidence['self_suite'];last=evidence['self_checks'][-1]
    if (last.get('status')!='passed' or evidence['self_reference_validation'].get('status')!='passed'
            or evidence['self_suite_digest']!=digest(smoke)):
        raise ValueError('Continuation requires a reference-qualified smoke suite and passing candidate')
    stage='build_tests';request=requests[stage];response=raw[stage]
    if (request.get('role')!='test_author' or request.get('operation')!='generate'
            or request.get('visible_files') or 'value' not in response
            or not row['result']['stages'].get(stage,{}).get('validation_error')):
        raise ValueError('Continuation requires a completed rejected independent response under its original contract')
    payload=request['payload']
    value={'policy':POLICY,'source_run_id':row['id'],'source_approval_digest':row['digest'],
        'source_contract_digest':contract_digest(source),'target_binding_digest':binding_digest(target),
        'plan':plan.model_dump(),'artifact':artifact.model_dump(),'self_suite':deepcopy(smoke),
        'counters':deepcopy(evidence['budget_counters']),'repair_attempts':output['repair_attempts'],
        'compile_repair_attempts':output['compile_repair_attempts'],
        'generation':{'stage':stage,'role':'test_author','payload':deepcopy(payload),'value':deepcopy(response['value'])},
        'source_receipts':receipts,'closed_execution_audit':closed_execution_audit}
    validate(value,target)
    return value


def validate(value,bundle):
    from .whole_project import WholeProjectPlan,whole_project_types
    from .project_build import project_test_types
    from .wire import wire_schema
    required={'policy','source_run_id','source_approval_digest','source_contract_digest','target_binding_digest',
        'plan','artifact','self_suite','counters','repair_attempts','compile_repair_attempts','generation',
        'source_receipts','closed_execution_audit'}
    if (not isinstance(value,dict) or value.keys()!=required or value['policy']!=POLICY
            or {'fresh_project','whole_project_seed','whole_project_checkpoint','whole_project_initial_compile_recovery'}.intersection(bundle)
            or value['target_binding_digest']!=binding_digest(bundle)
            or value['source_contract_digest']!=contract_digest(bundle)):
        raise ValueError('Stage continuation differs from its frozen target or contracts')
    artifact_type,_=whole_project_types(bundle);suite_type,_=project_test_types(bundle)
    plan=WholeProjectPlan.model_validate(value['plan']);artifact=artifact_type.model_validate(value['artifact'])
    smoke=suite_type.model_validate(value['self_suite'])
    # Reuse the complete existing counter/inventory validator without crediting this smoke suite as independent tests.
    validate_checkpoint({'policy':'whole-project-checkpoint-v1','source_run_id':value['source_run_id'],
        'source_approval_digest':value['source_approval_digest'],'binding_digest':binding_digest(bundle),
        'phase':'tests','plan':plan.model_dump(),'artifact':artifact.model_dump(),
        'suite':smoke.model_dump(),'self_suite':smoke.model_dump(),'suite_digest':digest(smoke.model_dump()),
        'artifact_digest':digest(artifact.model_dump()),'counters':value['counters'],
        'repair_attempts':value['repair_attempts'],'compile_repair_attempts':value['compile_repair_attempts'],
        'unverified_criteria':[],'quarantined':[]},bundle)
    generation=value['generation'];payload=generation.get('payload',{});messages=payload.get('messages',[])
    if (generation.keys()!={'stage','role','payload','value'} or generation['stage']!='build_tests'
            or generation['role']!='test_author' or payload.get('schema')!=wire_schema(suite_type)
            or len(messages)!=2 or [m.get('role') for m in messages]!=['system','user']
            or not all(isinstance(m.get('content'),str) for m in messages)
            or not isinstance(generation['value'],dict)):
        raise ValueError('Retained generation differs from its original author/schema contract')
    for key in ('source_approval_digest','source_contract_digest','closed_execution_audit'):
        if not re.fullmatch('[a-f0-9]{64}',value[key]):raise ValueError('Invalid continuation evidence digest')
    return plan,artifact


def restore(flow):
    value=flow.bundle[KEY];plan,artifact=validate(value,flow.bundle)
    flow.counters.update(value['counters'])
    flow.output.update(repair_attempts=value['repair_attempts'],compile_repair_attempts=value['compile_repair_attempts'])
    flow.evidence.update(budget_counters=dict(flow.counters),stage_continuation={
        'source_run_id':value['source_run_id'],'source_approval_digest':value['source_approval_digest'],
        'closed_execution_audit':value['closed_execution_audit'],'acceptance_credit':False})
    flow._continued_generation=deepcopy(value['generation'])
    return plan,artifact
