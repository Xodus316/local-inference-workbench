"""Resume settled smoke qualification without reauthoring the application or tests."""
from copy import deepcopy
import hashlib
import json
import re

from .store import digest
from .stage_continuation import KEY,contract_digest
from .whole_project_checkpoint import binding_digest,validate_checkpoint

POLICY='settled-smoke-qualification-continuation-v10'


def pack(row,requests,raw,target,*,closed_execution_audit,checkpoint_bytes,reference_bytes):
    from .whole_project import _stopped_opencode_result
    from .project_build import project_test_types
    from .reference_repair_coordinator import reference_evidence
    source=row['bundle'];out=row['result']['output'];e=out['verification']['whole_project']
    if (row['status'] not in {'failed','review_required'} or not row.get('approved')
            or row['digest']!=digest(source) or row['approval']['digest']!=row['digest']
            or contract_digest(source)!=contract_digest(target)
            or not re.fullmatch('[a-f0-9]{64}',closed_execution_audit)
            or not row.get('checkpoints') or out.get('suite') is not None
            or any(call['status']!='terminal' for call in row['calls'])):
        raise ValueError('Smoke continuation requires audited settled qualification with unchanged contracts')
    for field in ('model','context','thinking','model_manifest_sha256','backend_identity','sampling'):
        if source['backend'].get(field)!=target['backend'].get(field):
            raise ValueError('Smoke continuation cannot change the pinned model')
    parent=source.get(KEY)
    files={f['path']:f['content'] for f in parent['artifact']['files']} if parent and parent.get('policy')==POLICY else {}
    receipts={}
    for call in row['calls']:
        stage=call['stage'];request=requests[stage];response=raw[stage]
        if not _stopped_opencode_result(response):raise ValueError('Worker cleanup is unresolved')
        receipts[stage]={'request_digest':digest(request),'raw_digest':digest(response)}
        if request.get('operation')=='run':
            changes=response.get('files')
            if (not re.fullmatch(r'implement(?:_inventory_retry)?|build_compile_repair_[1-4]_[1-4](?:_edit_retry)?',stage)
                    or not isinstance(changes,dict) or not changes
                    or not changes.keys()<=set(request.get('editable_paths',[]))
                    or any(path not in files or digest(files[path])!=sha for path,sha in request.get('visible_files',{}).items())):
                raise ValueError('Application continuation differs from its scoped worker receipts')
            files.update(changes)
    if files!={f['path']:f['content'] for f in out['artifact']['files']}:
        raise ValueError('Application checkpoint differs from its completed source chain')
    record=row['checkpoints'][-1];cp=json.loads(checkpoint_bytes)
    cpout=cp.get('state',{}).get('output') or cp.get('state',{}).get('update',{}).get('code_build',{}).get('output',{})
    if (hashlib.sha256(checkpoint_bytes).hexdigest()!=record['content_sha256']
            or cp.get('run_id')!=row['id'] or cp.get('approval_digest')!=row['digest']
            or cp.get('engine_digest')!=digest(source['whole_project_engine_digest'])
            or any(cp.get(k)!=record[k] for k in ('kind','stage','binding'))
            or cpout.get('artifact')!=out['artifact']
            or cpout.get('verification',{}).get('whole_project',{}).get('self_suite')!=e['self_suite']
            or cpout.get('verification',{}).get('whole_project',{}).get('budget_counters')!=e['budget_counters']
            or not any(re.fullmatch(r'build_control_\d+\.raw.json',name)
                       and sha==hashlib.sha256(reference_bytes).hexdigest() for name,sha in cp['receipts'].items())):
        raise ValueError('Latest checkpoint does not bind retained smoke source, costs and reference execution')
    smoke=project_test_types(source)[0].model_validate(e['self_suite'])
    observed=reference_evidence(source,smoke,json.loads(reference_bytes))
    if not observed['failed']:raise ValueError('Smoke continuation requires observed test failures')
    value={'policy':POLICY,'source_run_id':row['id'],'source_approval_digest':row['digest'],
        'source_contract_digest':contract_digest(source),'target_binding_digest':binding_digest(target),
        'source_checkpoint':deepcopy(record),'closed_execution_audit':closed_execution_audit,'source_receipts':receipts,
        'plan':deepcopy(out['plan']),'artifact':deepcopy(out['artifact']),'self_suite':smoke.model_dump(),
        'artifact_digest':digest(out['artifact']),'self_suite_digest':digest(smoke.model_dump()),
        'counters':deepcopy(e['budget_counters']),'repair_attempts':out['repair_attempts'],
        'compile_repair_attempts':out['compile_repair_attempts'],
        'coordinated_history':deepcopy(e.get('coordinated_repairs',[])), 'retained_reference':observed}
    validate(value,{**target,KEY:value})
    return value


def validate(value,bundle):
    from .recovery_limits import approved
    from .project_build import project_test_types
    fields={'policy','source_run_id','source_approval_digest','source_contract_digest','target_binding_digest',
        'source_checkpoint','closed_execution_audit','source_receipts','plan','artifact','self_suite',
        'artifact_digest','self_suite_digest','counters','repair_attempts','compile_repair_attempts',
        'coordinated_history','retained_reference'}
    if (not isinstance(value,dict) or value.keys()!=fields or value['policy']!=POLICY
            or not approved(bundle) or value['target_binding_digest']!=binding_digest(bundle)
            or value['source_contract_digest']!=contract_digest(bundle)
            or any(k in bundle for k in ('fresh_project','whole_project_seed','whole_project_checkpoint','whole_project_initial_compile_recovery'))
            or value['artifact_digest']!=digest(value['artifact']) or value['self_suite_digest']!=digest(value['self_suite'])
            or any(not re.fullmatch('[a-f0-9]{64}',value[k]) for k in ('source_approval_digest','closed_execution_audit'))):
        raise ValueError('Smoke continuation changed its approved source, contracts or evidence')
    plan,artifact,_,_=validate_checkpoint({'policy':'whole-project-checkpoint-v1',
        'source_run_id':value['source_run_id'],'source_approval_digest':value['source_approval_digest'],
        'binding_digest':binding_digest(bundle),'phase':'tests','plan':value['plan'],'artifact':value['artifact'],
        'suite':value['self_suite'],'self_suite':value['self_suite'],'suite_digest':value['self_suite_digest'],
        'artifact_digest':value['artifact_digest'],'counters':value['counters'],'repair_attempts':value['repair_attempts'],
        'compile_repair_attempts':value['compile_repair_attempts'],'unverified_criteria':[],'quarantined':[]},bundle)
    from .reference_repair_coordinator import inventory
    cases=inventory(project_test_types(bundle)[0].model_validate(value['self_suite']));r=value['retained_reference']
    if (not r['failed'] or r['tests_run']!=len(cases) or set(r['failed'])!={x['case'] for x in r['failures']}
            or not set(r['failed'])<=cases.keys() or len(value['coordinated_history'])!=value['counters'].get('coordinated_patch',0)):
        raise ValueError('Smoke continuation lost exact test identities or spent validation history')
    return plan,artifact


def restore(flow):
    value=flow.bundle[KEY];plan,artifact=validate(value,flow.bundle)
    flow.counters.update(value['counters'])
    flow.output.update(repair_attempts=value['repair_attempts'],compile_repair_attempts=value['compile_repair_attempts'])
    flow.evidence.update(budget_counters=dict(flow.counters),coordinated_repairs=deepcopy(value['coordinated_history']),
        stage_continuation={'source_run_id':value['source_run_id'],'source_approval_digest':value['source_approval_digest'],
            'source_checkpoint':value['source_checkpoint'],'closed_execution_audit':value['closed_execution_audit'],
            'acceptance_credit':False})
    return plan,artifact
