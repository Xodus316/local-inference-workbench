"""Evaluate a completed, already charged proposal under an explicit continuation approval."""
from copy import deepcopy
import json
import re

from .repair_coordinator import CoordinatedPatch,migrate_legacy_proposal,test_scope
from .store import digest
from .wire import wire_schema


def pack(row,requests,raw,suite,reference):
    candidates=[]
    for call in row['calls']:
        stage=call['stage'];request=requests[stage];response=raw[stage]
        if (call['status']=='terminal' and request.get('role')=='test_author' and request.get('operation')=='generate'
                and re.fullmatch(r'whole_recovery_(?:[1-9]|1[0-6])_output',stage)
                and request.get('payload',{}).get('schema')==wire_schema(CoordinatedPatch) and 'value' in response):
            candidates.append(stage)
    if not candidates:raise ValueError('No completed legacy repair proposal can be continued')
    stage=candidates[-1];request=requests[stage];response=raw[stage]
    data=json.loads(request['payload']['messages'][1]['content'])
    index=int(stage.split('_')[2]);recovery=row['result']['output'].get('recovery',{}).get('requests',[])
    authorization=[r for r in recovery if r.get('agent_consultation',{}).get('index')==index
        and r.get('action')=='correct_stage_output' and r.get('status')=='output_correction_rejected']
    if (not authorization or data.get('base_suite')!=suite.model_dump() or data.get('base_suite_digest')!=digest(suite.model_dump())
            or data.get('failures')!=reference['failures'] or data.get('scope')!=test_scope(suite,reference['failures'])):
        raise ValueError('Completed proposal does not bind the retained source, evidence and original repair authority')
    proposal,migration=migrate_legacy_proposal(response['value'],reference['failures'])
    return {'policy':'completed-proposal-evaluation-v1','source_stage':stage,'role':'test_author',
        'request_digest':digest(request),'raw_digest':digest(response),'base_suite_digest':digest(suite.model_dump()),
        'source_reference_receipt_digest':reference['receipt_digest'],'raw_value':deepcopy(response['value']),
        'proposal':proposal,'migration':migration,'evidence':deepcopy(reference['failures']),'scope':deepcopy(data['scope']),
        'model_redispatch_allowed':False}


def validate(value,suite,reference):
    fields={'policy','source_stage','role','request_digest','raw_digest','base_suite_digest',
        'source_reference_receipt_digest','raw_value','proposal','migration','evidence','scope','model_redispatch_allowed'}
    if (not isinstance(value,dict) or value.keys()!=fields or value['policy']!='completed-proposal-evaluation-v1'
            or value['role']!='test_author' or value['model_redispatch_allowed'] is not False
            or not re.fullmatch(r'whole_recovery_(?:[1-9]|1[0-6])_output',value['source_stage'])
            or value['base_suite_digest']!=digest(suite.model_dump()) or value['evidence']!=reference['failures']
            or value['scope']!=test_scope(suite,reference['failures'])
            or value['source_reference_receipt_digest']!=reference['receipt_digest']
            or any(not re.fullmatch('[a-f0-9]{64}',value[k]) for k in ('request_digest','raw_digest'))):
        raise ValueError('Retained repair proposal differs from its source, scope or evidence')
    proposal,migration=migrate_legacy_proposal(value['raw_value'],value['evidence'])
    if proposal!=value['proposal'] or migration!=value['migration']:
        raise ValueError('Retained proposal normalization changed its executable edits or evidence bindings')
    return proposal
