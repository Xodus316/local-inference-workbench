"""Explicitly renewed shared repair epoch over settled, retained independent tests."""
from copy import deepcopy
import hashlib
import json
import re

from .store import digest
from .whole_project_checkpoint import binding_digest,validate_checkpoint
from .independent_stage_restart import KEY,contract_digest

POLICY='settled-checkpoint-repair-epoch-v9'


def retained_counters(counters):
    return {k:v for k,v in counters.items() if not k.startswith('recovery_') and k!='coordinated_patch'}


def budgets(expanded=False):
    if expanded:return {'diagnoses':16,'requests':64,'output_corrections':16,'coordinated_validations':16,
        'test_defect_repairs':16,'context_observations':2}
    return {'diagnoses':16,'requests':64,'output_corrections':4,'coordinated_validations':6}


def approved_budgets(value):
    if (not isinstance(value,dict) or any(type(n) is not int for n in value.values())
            or value not in (budgets(),budgets(True))):
        raise ValueError('Unsupported explicit checkpoint repair allowance')
    return value


def accepted_suite(row,reconstructed=None):
    evidence=row['result']['output']['verification']['whole_project']
    declared=evidence.get('independent_draft',{}).get('suite') or row['bundle'][KEY].get('independent_suite')
    if declared is not None:return declared
    from .smoke_continuation import POLICY as SMOKE_POLICY
    if row['bundle'][KEY].get('policy')==SMOKE_POLICY and reconstructed is not None:
        # Exact completed author/syntax/promotion replay is bound below to the
        # latest checkpoint's preflight and full reference source hashes.
        return reconstructed
    raise ValueError('Source has no retained independent suite')


def pack(row,requests,raw,target,*,closed_execution_audit,checkpoint_bytes,preflight_bytes,reference_bytes,reference_history_bytes=None,fresh_repair_budgets=None):
    from .stage_continuation import validate as validate_parent,retained_suite
    from .whole_project import _stopped_opencode_result
    from .project_build import execution_settled,project_test_types
    from .reference_repair_coordinator import reference_evidence
    from .stage_recovery import validate_costs
    source=row['bundle'];out=row['result']['output'];e=out['verification']['whole_project']
    if (row['status'] not in {'failed','review_required'} or not row.get('approved')
            or row['digest']!=digest(source) or row['approval']['digest']!=row['digest']
            or contract_digest(source)!=contract_digest(target)
            or not re.fullmatch('[a-f0-9]{64}',closed_execution_audit)
            or not row.get('checkpoints') or any(c['status']!='terminal' for c in row['calls'])):
        raise ValueError('Repair continuation requires audited settled work and unchanged contracts')
    plan,artifact=validate_parent(source[KEY],source)
    if plan.model_dump()!=out['plan'] or artifact.model_dump()!=out['artifact']:
        raise ValueError('Retained application differs from its verified source chain')
    receipts={}
    for call in row['calls']:
        name=call['stage']
        if requests[name].get('operation')=='run' or not _stopped_opencode_result(raw[name]):
            raise ValueError('Repair continuation requires a settled unchanged application')
        receipts[name]={'request_digest':digest(requests[name]),'raw_digest':digest(raw[name])}
    for key in ('model','context','thinking','model_manifest_sha256','backend_identity','sampling'):
        if source['backend'].get(key)!=target['backend'].get(key):raise ValueError('Repair continuation cannot change models')
    suite_type=project_test_types(source)[0]
    suite=retained_suite(row,requests,raw)
    if suite!=accepted_suite(row,suite):raise ValueError('Accepted test source does not match completed promotion receipts')
    record=row['checkpoints'][-1];cp=json.loads(checkpoint_bytes);pre=json.loads(preflight_bytes)
    cp_output=cp.get('state',{}).get('output') or cp.get('state',{}).get('update',{}).get('code_build',{}).get('output',{})
    if (hashlib.sha256(checkpoint_bytes).hexdigest()!=record['content_sha256']
            or cp.get('run_id')!=row['id'] or cp.get('approval_digest')!=row['digest']
            or cp.get('engine_digest')!=digest(source['whole_project_engine_digest'])
            or any(cp.get(k)!=record[k] for k in ('kind','stage','binding'))
            or cp_output.get('artifact')!=out['artifact']
            or cp_output.get('verification',{}).get('whole_project',{}).get('budget_counters')!=e['budget_counters']
            or not execution_settled(pre) or pre.get('status')!='passed'
            or len(pre.get('commands',[]))!=1 or pre['commands'][0].get('source_sha256')!={
                f['path']:hashlib.sha256(f['content'].encode('utf8')).hexdigest() for f in suite['files']}
            or not any(re.fullmatch(r'build_test_preflight_\d+\.raw.json',n) and h==hashlib.sha256(preflight_bytes).hexdigest()
                for n,h in cp['receipts'].items())
            or not any(re.fullmatch(r'build_control_\d+\.raw.json',n) and h==hashlib.sha256(reference_bytes).hexdigest()
                for n,h in cp['receipts'].items())):
        raise ValueError('Repair continuation checkpoint does not bind current test source and costs')
    reference=reference_evidence(source,project_test_types(source)[0].model_validate(suite),json.loads(reference_bytes))
    if not reference['failed']:raise ValueError('Repair epoch requires observed reference failures')
    counters=deepcopy(e['budget_counters']);validate_costs(source,counters)
    value={'policy':POLICY,'source_run_id':row['id'],'source_approval_digest':row['digest'],
        'source_contract_digest':contract_digest(source),'target_binding_digest':binding_digest(target),
        'source_checkpoint':deepcopy(record),'source_receipts':receipts,'closed_execution_audit':closed_execution_audit,
        'plan':plan.model_dump(),'artifact':artifact.model_dump(),'artifact_digest':digest(artifact.model_dump()),
        'self_suite':deepcopy(e['self_suite']),'self_suite_digest':e['self_suite_digest'],
        'independent_suite':suite,'independent_suite_digest':digest(suite),'retained_reference':reference,
        'coordinated_history':deepcopy(e.get('coordinated_repairs',[])),
        'source_spent_counters':counters,'source_spent_counters_digest':digest(counters),
        'counters':retained_counters(counters),'fresh_budgets':deepcopy(approved_budgets(fresh_repair_budgets or budgets())),
        'prior_budget_epoch':deepcopy(e.get('budget_epoch',{})),
        'repair_attempts':out['repair_attempts'],'compile_repair_attempts':out['compile_repair_attempts'],
        'restart_scope':'shared_recovery_over_retained_independent_tests'}
    from .reference_repair_feedback import pack as pack_feedback
    if reference_history_bytes is not None:
        value['verification_rejections']=pack_feedback(row,requests,raw,suite_type.model_validate(suite),reference,reference_history_bytes,cp,current_only=True)
    validate(value,{**target,KEY:value})
    return value


def validate(value,bundle):
    from .whole_project import WholeProjectPlan,whole_project_types
    from .project_build import project_test_types
    from .reference_repair_coordinator import inventory
    from .repair_coordinator import executable_edits
    from .project_test_lint import defects
    fields={'policy','source_run_id','source_approval_digest','source_contract_digest','target_binding_digest',
        'source_checkpoint','source_receipts','closed_execution_audit','plan','artifact','artifact_digest','self_suite',
        'self_suite_digest','independent_suite','independent_suite_digest','retained_reference','coordinated_history',
        'source_spent_counters','source_spent_counters_digest','counters','fresh_budgets','prior_budget_epoch',
        'repair_attempts','compile_repair_attempts','restart_scope'}
    if 'verification_rejections' in value:fields.add('verification_rejections')
    approved_budgets(value.get('fresh_budgets'))
    if (value.keys()!=fields or value['policy']!=POLICY
            or value['restart_scope']!='shared_recovery_over_retained_independent_tests'
            or value['target_binding_digest']!=binding_digest(bundle)
            or value['source_contract_digest']!=contract_digest(bundle)
            or any(k in bundle for k in ('fresh_project','whole_project_seed','whole_project_checkpoint','whole_project_initial_compile_recovery'))):
        raise ValueError('Repair epoch changed its approved source, contracts or allowance')
    counters=value['source_spent_counters'];record=value['source_checkpoint']
    if (not isinstance(counters,dict) or any(type(n) is not int or n<0 for n in counters.values())
            or value['source_spent_counters_digest']!=digest(counters)
            or value['counters']!=retained_counters(counters)
            or record.keys()!={'kind','stage','binding','content_sha256','created'}
            or record['kind'] not in {'graph','native','sandbox'}
            or any(not re.fullmatch('[a-f0-9]{64}',record[k]) for k in ('binding','content_sha256'))
            or any(not re.fullmatch('[a-f0-9]{64}',value[k]) for k in ('source_approval_digest','closed_execution_audit'))):
        raise ValueError('Repair epoch lost historical costs or checkpoint bindings')
    if any(r.keys()!={'request_digest','raw_digest'} or any(not re.fullmatch('[a-f0-9]{64}',h) for h in r.values())
            for r in value['source_receipts'].values()):raise ValueError('Invalid source receipt binding')
    plan=WholeProjectPlan.model_validate(value['plan']);artifact=whole_project_types(bundle)[0].model_validate(value['artifact'])
    suite_type=project_test_types(bundle)[0];suite=suite_type.model_validate(value['independent_suite']);smoke=suite_type.model_validate(value['self_suite'])
    if (value['artifact_digest']!=digest(artifact.model_dump()) or value['independent_suite_digest']!=digest(suite.model_dump())
            or value['self_suite_digest']!=digest(smoke.model_dump()) or defects(suite)
            or set(suite.implementation_files)!=set(plan.implementation_files)):
        raise ValueError('Retained source or independent suite changed')
    ref=value['retained_reference'];cases=inventory(suite)
    if (ref.keys()!={'failed','tests_run','receipt_digest','failures'} or ref['tests_run']!=len(cases)
            or not ref['failed'] or len(set(ref['failed']))!=len(ref['failed'])
            or {x['case'] for x in ref['failures']}!=set(ref['failed'])
            or any({k:x[k] for k in ('id','path','test_name')}!=cases.get(x['case']) for x in ref['failures'])
            or not re.fullmatch('[a-f0-9]{64}',ref['receipt_digest'])):
        raise ValueError('Retained reference evidence changed')
    history=value['coordinated_history']
    if any(r.get('disposition') not in {'promoted','rolled_back'} or r['patch_digest']!=digest(executable_edits(r['edits'])) for r in history):
        raise ValueError('Retained repair history changed')
    if 'verification_rejections' in value:
        from .reference_repair_feedback import validate as validate_feedback
        validate_feedback(value['verification_rejections'],value,suite)
    validate_checkpoint({'policy':'whole-project-checkpoint-v1','source_run_id':value['source_run_id'],
        'source_approval_digest':value['source_approval_digest'],'binding_digest':binding_digest(bundle),'phase':'tests',
        'plan':plan.model_dump(),'artifact':artifact.model_dump(),'suite':suite.model_dump(),'self_suite':smoke.model_dump(),
        'suite_digest':value['independent_suite_digest'],'artifact_digest':value['artifact_digest'],'counters':value['counters'],
        'repair_attempts':value['repair_attempts'],'compile_repair_attempts':value['compile_repair_attempts'],
        'unverified_criteria':[],'quarantined':[]},bundle)
    return plan,artifact


def restore(flow):
    value=flow.bundle[KEY];plan,artifact=validate(value,flow.bundle)
    flow.counters.update(value['counters'])
    if 'test_defect_repairs' in value['fresh_budgets']:flow.limits['max_test_defect_repairs']=value['fresh_budgets']['test_defect_repairs']
    flow.output.update(repair_attempts=value['repair_attempts'],compile_repair_attempts=value['compile_repair_attempts'])
    if 'verification_rejections' in value:flow.evidence['carried_verification_rejections']=deepcopy(value['verification_rejections'])
    flow._continued_independent_suite=deepcopy(value['independent_suite'])
    flow.evidence.update(coordinated_repairs=deepcopy(value['coordinated_history']),budget_counters=dict(flow.counters),
        application_checkpoint_digest=value['artifact_digest'],stage_continuation={
            'source_run_id':value['source_run_id'],'source_approval_digest':value['source_approval_digest'],
            'closed_execution_audit':value['closed_execution_audit'],'acceptance_credit':False},
        budget_epoch={'policy':POLICY,'fresh_budgets':deepcopy(value['fresh_budgets']),
            'source_spent_counters':deepcopy(value['source_spent_counters']),
            'prior_budget_epoch':deepcopy(value['prior_budget_epoch']),'costs_refunded':False})
    return plan,artifact
