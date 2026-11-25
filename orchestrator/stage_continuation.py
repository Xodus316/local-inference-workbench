"""Continue a settled semantic-output failure without replaying application generation."""
from copy import deepcopy
import hashlib
import json
import re

from .store import digest
from .whole_project_checkpoint import binding_digest,validate_checkpoint

KEY = 'whole_project_stage_continuation'
POLICY = 'settled-stage-output-continuation-v1'
SUITE_POLICY = 'settled-unfrozen-suite-continuation-v2'
VALIDATION_POLICY = 'settled-test-validation-continuation-v3'
REFERENCE_POLICY = 'settled-reference-repair-continuation-v4'
PENDING_POLICY = 'settled-proposal-validation-continuation-v5'
_MIGRATION = {'fresh_project','backend','config_digest','whole_project_engine_digest',
    'whole_project_workers','whole_project_prompt_tokenizers','same_model_role_isolation',KEY}


def contract_digest(bundle):
    return digest({k:v for k,v in bundle.items() if k not in _MIGRATION})


def retained_suite(row,requests,raw):
    """Replay only completed whole-suite and scoped file responses; no model/sandbox dispatch."""
    from .build_models import BuildTestSyntaxRepair
    from .build_coverage import TestSyntaxError,correct_test_references
    from .project_build import project_test_types
    from .wire import wire_schema
    from .repair_coordinator import CoordinatedPatch,compose_tests,patch_type
    suite_type,_=project_test_types(row['bundle'])
    parent=row['bundle'][KEY]
    current=deepcopy(parent.get('independent_suite') or parent['generation']['value'])
    pending=parent.get('pending_proposal')
    history=row['result']['output']['verification']['whole_project'].get('coordinated_repairs',[])
    if pending is not None:
        records=[r for r in history if r['proposal_digest']==digest(pending['proposal'])]
        if records and records[-1]['disposition']=='promoted':
            from types import SimpleNamespace
            from .project_test_lint import defects
            flow=SimpleNamespace(suite_type=suite_type,contract=row['bundle']['build_contract'],test_defects=defects)
            if digest(current)!=pending['base_suite_digest']:raise ValueError('Retained pending proposal base changed')
            current=compose_tests(flow,suite_type.model_validate(current),pending['evidence'],pending['scope'],pending['proposal']).model_dump()
            if digest(current)!=records[-1]['candidate_suite_digest']:raise ValueError('Retained pending candidate digest changed')
    revisions=[{f['path']:f['content'] for f in current['files']}]
    rejected_stages={f"whole_recovery_{r['agent_consultation']['index']}_output"
        for r in (row['result']['output'].get('recovery') or {}).get('requests',[])
        if r.get('status')=='output_correction_rejected' and r.get('agent_consultation')}
    for call in row['calls']:
        stage=call['stage'];request=requests[stage];response=raw[stage]
        if stage in rejected_stages:continue  # A settled model proposal is not an accepted source transition.
        if request.get('role')!='test_author' or 'value' not in response:continue
        payload=request.get('payload',{});schema=payload.get('schema')
        messages=payload.get('messages',[])
        data=json.loads(messages[1]['content']) if len(messages)==2 else {}
        bound_schema=wire_schema(patch_type(data['failures'])) if data.get('failures') else None
        if schema==wire_schema(CoordinatedPatch) or (bound_schema is not None and schema==bound_schema):
            from types import SimpleNamespace
            from .project_test_lint import defects
            records=[r for r in row['result']['output']['verification']['whole_project'].get('coordinated_repairs',[])
                if r.get('proposal_digest')==digest(response['value'])]
            if not records:raise ValueError('Anchored model proposal lacks its verification disposition')
            if records[-1]['disposition']!='promoted':continue
            data=json.loads(payload['messages'][1]['content'])
            proposed_base=deepcopy(data['base_suite'])
            projection=data.get('base_suite_projection')
            if projection is not None:
                coverage=current.get('coverage')
                if (projection.get('original_digest')!=digest(current)
                        or proposed_base.get('coverage')!={'host_retained':True,'digest':digest(coverage),
                            'entries':len(coverage),'editable':False}):
                    raise ValueError('Anchored proposal immutable coverage projection changed')
                proposed_base['coverage']=deepcopy(coverage)
            if current!=proposed_base or digest(current)!=data['base_suite_digest']:
                raise ValueError('Anchored proposal base differs from the retained accepted source')
            flow=SimpleNamespace(suite_type=suite_type,contract=row['bundle']['build_contract'],test_defects=defects)
            current=compose_tests(flow,suite_type.model_validate(current),data['failures'],data['scope'],response['value']).model_dump()
            if digest(current)!=records[-1]['candidate_suite_digest']:raise ValueError('Verified anchored proposal content changed')
        elif schema==wire_schema(suite_type):
            from .build_discovery import normalize_authored_test_paths
            current,_=normalize_authored_test_paths(response['value'])
        elif schema==wire_schema(BuildTestSyntaxRepair):
            data=json.loads(payload['messages'][1]['content']);selected={f['path']:f['content'] for f in data['files']}
            prior={f['path']:f['content'] for f in current['files']}
            replacements=BuildTestSyntaxRepair.model_validate(response['value']).files
            same_base=all(prior.get(path)==content for path,content in selected.items())
            rejected=(data.get('rejected_output') or {}).get('files',[])
            rejected_files={f['path']:f['content'] for f in rejected}
            continued_correction=(re.fullmatch(r'whole_recovery_[1-9]\d*_output',stage)
                and len(rejected_files)==len(rejected) and rejected_files.keys()==selected.keys()
                and all(prior.get(path)==content for path,content in rejected_files.items())
                and any(all(revision.get(path)==content for path,content in selected.items()) for revision in revisions))
            if ((not same_base and not continued_correction)
                    or {f.path for f in replacements}!=selected.keys() or len(replacements)!=len(selected)):
                raise ValueError('Retained test file transition differs from its journaled scope')
            changed={f.path:f.model_dump() for f in replacements}
            current={**current,'files':[changed.get(f['path'],f) for f in current['files']]}
        else:continue
        try:current=correct_test_references(suite_type.model_validate(current),qualify_classes=True)[0].model_dump()
        except TestSyntaxError:pass  # A later completed syntax response must fix it.
        revisions.append({f['path']:f['content'] for f in current['files']})
    return suite_type.model_validate(current).model_dump()


def pack(row, requests, raw, target, *, closed_execution_audit, checkpoint_bytes=None, test_preflight_bytes=None, reference_bytes=None, retain_proposal=False):
    """Trusted host first verifies frozen files, checkpoints, native DONE and exact owner cleanup."""
    from .whole_project import WholeProjectPlan,whole_project_types,_stopped_opencode_result
    source=row['bundle'];output=row['result']['output'];evidence=output['verification']['whole_project']
    if (row['status'] not in {'cancelled','failed','review_required'} or not row.get('approved')
            or row['digest'] != digest(source) or row['approval']['digest'] != row['digest']
            or contract_digest(source) != contract_digest(target)
            or not re.fullmatch('[a-f0-9]{64}',closed_execution_audit)
            or any(c['status'] not in {'terminal','not_dispatched'} for c in row['calls'])):
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
        if call['status']=='not_dispatched':
            if response.get('dispatch_status')!='not_dispatched' or 'value' in response:
                raise ValueError('Unexecuted stage lacks its explicit non-dispatch receipt')
        elif response.get('worker_error'):
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
    if KEY in source:
        stage=(output.get('error') or {}).get('stage');call=next((c for c in row['calls'] if c['stage']==stage),{})
        generation=deepcopy(source[KEY]['generation'])
        suite=retained_suite(row,requests,raw)
        from .project_test_lint import defects
        from .project_build import project_test_types
        typed=project_test_types(source)[0].model_validate(suite);inventory={f.path:f.content for f in typed.files}
        if (call.get('status')=='not_dispatched' and re.fullmatch(r'build_tests_defect_[1-9]\d*',stage or '')
                and requests[stage].get('role')=='test_author'):
            data=json.loads(requests[stage]['payload']['messages'][1]['content'])
            if (defects(typed)!=data.get('diagnostics')
                    or any(inventory.get(f['path'])!=f['content'] for f in data['files'])):
                raise ValueError('Reconstructed suite does not reproduce the pending repair evidence')
        else:
            from .stage_recovery import dynamic_enabled,DYNAMIC_POLICY,route
            from .project_build import execution_settled
            static_findings=defects(typed)
            if (not dynamic_enabled(target) or row['status'] not in {'failed','review_required'} or output.get('suite') is not None
                    or route(stage or '')[0] not in {'independent_authoring','independent_qualification'}
                    or (evidence['budget_counters'].get('recovery_output_correction',0)>=DYNAMIC_POLICY['max_output_corrections'] and not retain_proposal)
                    or (not static_findings and not reference_bytes) or not checkpoint_bytes or not test_preflight_bytes or not row.get('checkpoints')):
                raise ValueError('Retained test validation requires a settled checkpoint, static defects and shared budget')
            record=row['checkpoints'][-1];checkpoint=json.loads(checkpoint_bytes);preflight=json.loads(test_preflight_bytes)
            if (hashlib.sha256(checkpoint_bytes).hexdigest()!=record['content_sha256']
                    or checkpoint.get('policy')!='workflow-step-checkpoint-v1'
                    or checkpoint.get('run_id')!=row['id'] or checkpoint.get('approval_digest')!=row['digest']
                    or checkpoint.get('engine_digest')!=digest(source['whole_project_engine_digest'])
                    or any(checkpoint.get(k)!=record[k] for k in ('kind','stage','binding'))
                    or not any(re.fullmatch(r'build_test_preflight_\d+\.raw.json',name)
                        and binding==hashlib.sha256(test_preflight_bytes).hexdigest()
                        for name,binding in checkpoint.get('receipts',{}).items())
                    or not execution_settled(preflight) or preflight.get('status')!='passed'
                    or len(preflight.get('commands',[]))!=1
                    or preflight['commands'][0].get('source_sha256')!={
                        path:hashlib.sha256(text.encode('utf8')).hexdigest() for path,text in inventory.items()}):
                raise ValueError('Latest step checkpoint does not bind the retained test preflight and source')
            if not static_findings:
                from .reference_repair_coordinator import reference_evidence
                reference=reference_evidence(source,typed,json.loads(reference_bytes))
                if (not reference['failed'] or not any(re.fullmatch(r'build_control_\d+\.raw.json',name)
                        and binding==hashlib.sha256(reference_bytes).hexdigest()
                        for name,binding in checkpoint['receipts'].items())):
                    raise ValueError('Latest checkpoint does not bind the exact failed reference qualification')
                if retain_proposal:
                    from .retained_repair_proposal import pack as pack_proposal
                    pending_proposal=pack_proposal(row,requests,raw,typed,reference)
    else:
        stage='build_tests';request=requests[stage];response=raw[stage]
        if (request.get('role')!='test_author' or request.get('operation')!='generate'
                or request.get('visible_files') or 'value' not in response
                or not row['result']['stages'].get(stage,{}).get('validation_error')):
            raise ValueError('Continuation requires a completed rejected independent response under its original contract')
        generation={'stage':stage,'role':'test_author','payload':deepcopy(request['payload']),'value':deepcopy(response['value'])}
    value={'policy':POLICY,'source_run_id':row['id'],'source_approval_digest':row['digest'],
        'source_contract_digest':contract_digest(source),'target_binding_digest':binding_digest(target),
        'plan':plan.model_dump(),'artifact':artifact.model_dump(),'self_suite':deepcopy(smoke),
        'counters':deepcopy(evidence['budget_counters']),'repair_attempts':output['repair_attempts'],
        'compile_repair_attempts':output['compile_repair_attempts'],
        'generation':generation,
        'source_receipts':receipts,'closed_execution_audit':closed_execution_audit}
    if KEY in source:
        value.update(policy=SUITE_POLICY,independent_suite=suite,independent_suite_digest=digest(suite))
        if call.get('status')!='not_dispatched':
            value.update(policy=VALIDATION_POLICY,source_checkpoint=deepcopy(record),retained_diagnostics=defects(typed))
            if not static_findings:
                value.pop('retained_diagnostics');value.update(policy=REFERENCE_POLICY,retained_reference=reference,
                    coordinated_history=deepcopy(evidence.get('coordinated_repairs',[])))
    if value['counters'].get('coordinated_patch',0):value['coordinated_history']=deepcopy(evidence.get('coordinated_repairs',[]))
    if retain_proposal:
        if value['policy']!=REFERENCE_POLICY:raise ValueError('Pending proposal evaluation requires complete failed reference qualification')
        value.update(policy=PENDING_POLICY,pending_proposal=pending_proposal)
    validate(value,target)
    return value


def validate(value,bundle):
    from .whole_project import WholeProjectPlan,whole_project_types
    from .project_build import project_test_types
    from .wire import wire_schema
    required={'policy','source_run_id','source_approval_digest','source_contract_digest','target_binding_digest',
        'plan','artifact','self_suite','counters','repair_attempts','compile_repair_attempts','generation',
        'source_receipts','closed_execution_audit'}
    if isinstance(value,dict) and value.get('policy') in {SUITE_POLICY,VALIDATION_POLICY,REFERENCE_POLICY,PENDING_POLICY}:required|={'independent_suite','independent_suite_digest'}
    if isinstance(value,dict) and value.get('policy')==VALIDATION_POLICY:required|={'source_checkpoint','retained_diagnostics'}
    if isinstance(value,dict) and value.get('policy') in {REFERENCE_POLICY,PENDING_POLICY}:required|={'source_checkpoint','retained_reference','coordinated_history'}
    if isinstance(value,dict) and value.get('policy')==PENDING_POLICY:required.add('pending_proposal')
    if isinstance(value,dict) and value.get('counters',{}).get('coordinated_patch',0):required.add('coordinated_history')
    if (not isinstance(value,dict) or value.keys()!=required or value['policy'] not in {POLICY,SUITE_POLICY,VALIDATION_POLICY,REFERENCE_POLICY,PENDING_POLICY}
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
    if value['policy'] in {SUITE_POLICY,VALIDATION_POLICY,REFERENCE_POLICY,PENDING_POLICY}:
        suite=suite_type.model_validate(value['independent_suite'])
        if (digest(suite.model_dump())!=value['independent_suite_digest']
                or set(suite.implementation_files)!=set(plan.implementation_files)):
            raise ValueError('Retained unfrozen suite inventory or digest changed')
    if value['policy'] in {VALIDATION_POLICY,REFERENCE_POLICY,PENDING_POLICY}:
        from .project_test_lint import defects
        from .stage_recovery import dynamic_enabled,DYNAMIC_POLICY
        record=value['source_checkpoint']
        if (not dynamic_enabled(bundle) or (value['counters'].get('recovery_output_correction',0)>=DYNAMIC_POLICY['max_output_corrections'] and value['policy']!=PENDING_POLICY)
                or (value['policy']==VALIDATION_POLICY and (not defects(suite) or defects(suite)!=value['retained_diagnostics']))
                or not isinstance(record,dict) or record.keys()!={'kind','stage','binding','content_sha256','created'}
                or record['kind'] not in {'graph','native','sandbox'}
                or not all(re.fullmatch('[a-f0-9]{64}',record[k]) for k in ('binding','content_sha256'))):
            raise ValueError('Retained test validation checkpoint, diagnostics or shared budget changed')
        if value['policy'] in {REFERENCE_POLICY,PENDING_POLICY}:
            from .reference_repair_coordinator import inventory
            reference=value['retained_reference'];cases=inventory(suite)
            if (defects(suite) or reference.keys()!={'failed','tests_run','receipt_digest','failures'}
                    or reference['tests_run']!=len(cases) or not reference['failed']
                    or len(reference['failed'])!=len(set(reference['failed'])) or not set(reference['failed'])<=cases.keys()
                    or {x['case'] for x in reference['failures']}!=set(reference['failed'])
                    or any({k:item[k] for k in ('id','path','test_name')}!=cases[item['case']] for item in reference['failures'])
                    or not re.fullmatch('[a-f0-9]{64}',reference['receipt_digest'])):
                raise ValueError('Retained reference qualification differs from the exact current test inventory')
            if value['policy']==PENDING_POLICY:
                from .retained_repair_proposal import validate as validate_proposal
                validate_proposal(value['pending_proposal'],suite,reference)
                pending=value['pending_proposal'];receipt=value['source_receipts'].get(pending['source_stage'],{})
                if receipt!={'request_digest':pending['request_digest'],'raw_digest':pending['raw_digest']}:
                    raise ValueError('Retained proposal request/response journal binding changed')
    if 'coordinated_history' in value:
        history=value['coordinated_history']
        if (not isinstance(history,list) or len(history)!=value['counters'].get('coordinated_patch',0)
                or any(r.get('disposition') not in {'promoted','rolled_back'} or r.get('patch_digest')!=digest(r.get('edits')) for r in history)):
            raise ValueError('Retained repair history does not account for each spent proposal validation')
    return plan,artifact


def restore(flow):
    value=flow.bundle[KEY];plan,artifact=validate(value,flow.bundle)
    flow.counters.update(value['counters'])
    flow.output.update(repair_attempts=value['repair_attempts'],compile_repair_attempts=value['compile_repair_attempts'])
    flow.evidence.update(budget_counters=dict(flow.counters),stage_continuation={
        'source_run_id':value['source_run_id'],'source_approval_digest':value['source_approval_digest'],
        'closed_execution_audit':value['closed_execution_audit'],'acceptance_credit':False})
    if value['policy'] in {SUITE_POLICY,VALIDATION_POLICY,REFERENCE_POLICY,PENDING_POLICY}:flow._continued_independent_suite=deepcopy(value['independent_suite'])
    else:flow._continued_generation=deepcopy(value['generation'])
    if 'coordinated_history' in value:flow.evidence['coordinated_repairs']=deepcopy(value['coordinated_history'])
    if value['policy']==PENDING_POLICY:flow._pending_repair=deepcopy(value['pending_proposal'])
    return plan,artifact
