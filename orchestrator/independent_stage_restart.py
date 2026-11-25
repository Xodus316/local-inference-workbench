"""An explicitly authorized new test/verification epoch preserves the compiled application."""
from copy import deepcopy
import hashlib
import json
import re

from .store import digest
from .whole_project_checkpoint import binding_digest,validate_checkpoint

POLICY='independent-verification-stage-restart-v8'
KEY='whole_project_stage_continuation'


def retained_counters(counters):
    return {k:v for k,v in counters.items()
        if k.startswith(('whole_compile_initial_','whole_compile_self_')) or k=='whole_self_repair'}


def budgets(bundle):
    from .stage_recovery import DYNAMIC_POLICY
    return {'diagnoses':DYNAMIC_POLICY['max_diagnoses'],'requests':DYNAMIC_POLICY['max_requests'],
        'output_corrections':DYNAMIC_POLICY['max_output_corrections'],'coordinated_validations':6,
        'general_repairs':bundle['build']['max_repair_attempts'],
        'test_syntax_repairs':bundle['build']['max_test_syntax_repairs'],
        'test_coverage_repairs':bundle['build']['max_test_coverage_repairs'],
        'test_defect_repairs':bundle['build_contract']['max_test_defect_repairs'],
        'test_fixture_repairs':bundle['build_contract']['max_test_fixture_repairs']}


def contract_digest(bundle):
    from .candidate_source_contract import KEY as SOURCE_KEY,validate as validate_source
    from .stage_continuation import contract_digest as original
    validate_source(bundle)
    material=deepcopy(bundle);material.pop(SOURCE_KEY,None)
    return original(material)


def pack(row,requests,raw,target,*,closed_execution_audit,checkpoint_bytes):
    from .stage_continuation import validate as validate_parent
    from .whole_project import _stopped_opencode_result
    source=row['bundle'];output=row['result']['output'];evidence=output['verification']['whole_project']
    if (row['status'] not in {'failed','review_required','cancelled'} or not row.get('approved')
            or digest(source)!=row['digest'] or row['approval']['digest']!=row['digest']
            or contract_digest(source)!=contract_digest(target)
            or not re.fullmatch('[a-f0-9]{64}',closed_execution_audit) or not row.get('checkpoints')
            or any(c['status']!='terminal' for c in row['calls'])):
        raise ValueError('Stage restart requires a settled, audited source with unchanged public contracts')
    plan,artifact=validate_parent(source[KEY],source)
    if (plan.model_dump()!=output['plan'] or artifact.model_dump()!=output['artifact']
            or any(requests[c['stage']].get('operation')=='run' for c in row['calls'])):
        raise ValueError('Preserved application differs from its retained checkpoint')
    receipts={}
    for call in row['calls']:
        stage=call['stage']
        if not _stopped_opencode_result(raw[stage]):raise ValueError('Stage restart lacks native settlement and scratch cleanup')
        receipts[stage]={'request_digest':digest(requests[stage]),'raw_digest':digest(raw[stage])}
    for field in ('model','context','thinking','model_manifest_sha256','backend_identity','sampling'):
        if source['backend'].get(field)!=target['backend'].get(field):raise ValueError('Stage restart cannot change inference assignments')
    record=row['checkpoints'][-1];checkpoint=json.loads(checkpoint_bytes)
    if (hashlib.sha256(checkpoint_bytes).hexdigest()!=record['content_sha256']
            or checkpoint.get('run_id')!=row['id'] or checkpoint.get('approval_digest')!=row['digest']
            or checkpoint.get('engine_digest')!=digest(source['whole_project_engine_digest'])
            or any(checkpoint.get(k)!=record[k] for k in ('kind','stage','binding'))
            or evidence['self_suite_digest']!=digest(evidence['self_suite'])
            or evidence['self_reference_validation'].get('status')!='passed'
            or evidence['self_checks'][-1].get('status')!='passed'):
        raise ValueError('Stage restart application/smoke checkpoint is not settled and bound')
    counters=deepcopy(evidence['budget_counters']);keep=retained_counters(counters)
    from .stage_recovery import validate_costs
    validate_costs(source,counters)
    value={'policy':POLICY,'source_run_id':row['id'],'source_approval_digest':row['digest'],
        'source_contract_digest':contract_digest(source),'target_binding_digest':binding_digest(target),
        'source_checkpoint':deepcopy(record),'source_receipts':receipts,'closed_execution_audit':closed_execution_audit,
        'plan':plan.model_dump(),'artifact':artifact.model_dump(),'artifact_digest':digest(artifact.model_dump()),
        'self_suite':deepcopy(evidence['self_suite']),'self_suite_digest':evidence['self_suite_digest'],
        'source_spent_counters':counters,'archived_stage_counters':{k:v for k,v in counters.items() if k not in keep},
        'source_spent_counters_digest':digest(counters),
        'counters':keep,'repair_attempts':0,'compile_repair_attempts':sum(n for k,n in keep.items() if k.startswith('whole_compile_')),
        'fresh_budgets':budgets(target),'reference_digest':source['project_reference']['digest'],
        'retired_independent_suite_digest':digest(source[KEY]['independent_suite']),
        'retired_repair_history_digest':digest(evidence.get('coordinated_repairs',[])),
        'restart_scope':'independent_test_authoring_and_verification','operator_intervention':'none_after_launch'}
    validate(value,{**target,KEY:value})
    return value


def validate(value,bundle):
    from .whole_project import WholeProjectPlan,whole_project_types
    from .project_build import project_test_types
    from .candidate_source_contract import validate as validate_source
    fields={'policy','source_run_id','source_approval_digest','source_contract_digest','target_binding_digest',
        'source_checkpoint','source_receipts','closed_execution_audit','plan','artifact','artifact_digest',
        'self_suite','self_suite_digest','source_spent_counters','source_spent_counters_digest','archived_stage_counters','counters','repair_attempts',
        'compile_repair_attempts','fresh_budgets','reference_digest','retired_independent_suite_digest',
        'retired_repair_history_digest','restart_scope','operator_intervention'}
    if (not isinstance(value,dict) or value.keys()!=fields or value['policy']!=POLICY
            or value['restart_scope']!='independent_test_authoring_and_verification'
            or value['operator_intervention']!='none_after_launch'
            or value['target_binding_digest']!=binding_digest(bundle)
            or value['source_contract_digest']!=contract_digest(bundle)
            or not validate_source(bundle) or value['fresh_budgets']!=budgets(bundle)
            or value['reference_digest']!=bundle['project_reference']['digest']
            or any(k in bundle for k in ('fresh_project','whole_project_seed','whole_project_checkpoint','whole_project_initial_compile_recovery'))):
        raise ValueError('Independent-stage restart differs from its frozen application, contract or fresh budget epoch')
    prior=value['source_spent_counters']
    if not isinstance(prior,dict):raise ValueError('Historical stage costs must be a counter map')
    keep=retained_counters(prior)
    record=value['source_checkpoint']
    if (value['source_spent_counters_digest']!=digest(prior)
            or any(type(n) is not int or n<0 for n in prior.values())
            or not isinstance(record,dict) or record.keys()!={'kind','stage','binding','content_sha256','created'}
            or record['kind'] not in {'graph','native','sandbox'}
            or any(not re.fullmatch('[a-f0-9]{64}',record[k]) for k in ('binding','content_sha256'))
            or value['counters']!=keep or value['repair_attempts']!=0
            or value['archived_stage_counters']!={k:v for k,v in prior.items() if k not in keep}
            or any(not re.fullmatch('[a-f0-9]{64}',value[k]) for k in ('source_approval_digest','closed_execution_audit',
                'artifact_digest','self_suite_digest','retired_independent_suite_digest','retired_repair_history_digest'))):
        raise ValueError('Stage restart lost historical costs or changed retained compile/smoke counters')
    if (not isinstance(value['source_receipts'],dict) or any(
            receipt.keys()!={'request_digest','raw_digest'}
            or any(not re.fullmatch('[a-f0-9]{64}',s) for s in receipt.values())
            for receipt in value['source_receipts'].values())):
        raise ValueError('Invalid settled source receipt bindings')
    plan=WholeProjectPlan.model_validate(value['plan']);artifact=whole_project_types(bundle)[0].model_validate(value['artifact'])
    smoke=project_test_types(bundle)[0].model_validate(value['self_suite'])
    if value['artifact_digest']!=digest(artifact.model_dump()) or value['self_suite_digest']!=digest(smoke.model_dump()):
        raise ValueError('Preserved application or smoke bytes changed')
    validate_checkpoint({'policy':'whole-project-checkpoint-v1','source_run_id':value['source_run_id'],
        'source_approval_digest':value['source_approval_digest'],'binding_digest':binding_digest(bundle),'phase':'tests',
        'plan':plan.model_dump(),'artifact':artifact.model_dump(),'suite':smoke.model_dump(),'self_suite':smoke.model_dump(),
        'suite_digest':value['self_suite_digest'],'artifact_digest':value['artifact_digest'],'counters':keep,
        'repair_attempts':0,'compile_repair_attempts':value['compile_repair_attempts'],'unverified_criteria':[],'quarantined':[]},bundle)
    return plan,artifact


def restore(flow):
    value=flow.bundle[KEY];plan,artifact=validate(value,flow.bundle)
    flow.counters.update(value['counters'])
    flow.output.update(repair_attempts=0,compile_repair_attempts=value['compile_repair_attempts'])
    flow.evidence.update(budget_counters=dict(flow.counters),stage_continuation={
        'source_run_id':value['source_run_id'],'source_approval_digest':value['source_approval_digest'],
        'closed_execution_audit':value['closed_execution_audit'],'acceptance_credit':False},
        budget_epoch={'policy':POLICY,'fresh_budgets':deepcopy(value['fresh_budgets']),
            'source_spent_counters':deepcopy(value['source_spent_counters']),
            'archived_stage_counters':deepcopy(value['archived_stage_counters']),
            'retained_counters':deepcopy(value['counters']),'costs_refunded':False},
        application_checkpoint_digest=value['artifact_digest'],
        retired_independent_suite_digest=value['retired_independent_suite_digest'])
    return plan,artifact
