"""Source-bound reference regressions are actionable evidence, never acceptance credit."""
from copy import deepcopy
import hashlib
import json
import re
from types import SimpleNamespace

from .repair_coordinator import compose_tests,executable_edits
from .project_test_lint import defects
from .store import digest


MESSAGE='Repair did not reduce failed cases without regressions; retain previous source'


class ReferencePatchError(ValueError):
    def __init__(self,feedback):
        self.feedback=deepcopy(feedback)
        super().__init__(MESSAGE)


def feedback(before,after,base,candidate):
    old=set(before['failed']);new=set(after['failed'])
    return {'policy':'reference-progress-rejection-v1','base_suite_digest':digest(base.model_dump()),
        'proposed_suite_digest':digest(candidate.model_dump()),'before_receipt_digest':before['receipt_digest'],
        'after_receipt_digest':after['receipt_digest'],'tests_before':before['tests_run'],'tests_after':after['tests_run'],
        'before_failed':sorted(old),'after_failed':sorted(new),'resolved':sorted(old-new),
        'regressions':sorted(new-old),'new_failure_observations':[deepcopy(item) for item in after['failures'] if item['case'] not in old],
        'executable_source_applied':False,'acceptance_credit':False,
        'correction_base':'The proposed suite was rolled back. Return replacement anchors against original base_suite. '
            'A lower failure count cannot authorize a newly failing case or a changed test population.'}


def pack(row,requests,raw,suite,before,receipt_bytes,checkpoint):
    """Derive old rejected-candidate diagnostics only from audited model and sandbox receipts."""
    from .reference_repair_coordinator import reference_evidence
    history=row['result']['output']['verification']['whole_project'].get('coordinated_repairs',[])
    flow=SimpleNamespace(suite_type=type(suite),contract=row['bundle']['build_contract'],test_defects=defects)
    result=[]
    for record in history:
        if (record.get('disposition')!='rolled_back' or record.get('base_suite_digest')!=digest(suite.model_dump())
                or record.get('rejection')!=MESSAGE):continue
        proposals=[stage for stage,response in raw.items() if isinstance(response.get('value'),dict)
            and digest(response['value'])==record['proposal_digest']
            and requests[stage].get('role')=='test_author' and requests[stage].get('operation')=='generate']
        if len(proposals)!=1:raise ValueError('Rejected verification proposal lacks one exact settled producer receipt')
        stage=proposals[0];request=requests[stage];value=raw[stage]['value']
        data=json.loads(request['payload']['messages'][1]['content'])
        if (data.get('base_suite_digest')!=digest(suite.model_dump()) or digest(executable_edits(value))!=record['patch_digest']):
            raise ValueError('Rejected verification proposal base or executable edits changed')
        candidate=compose_tests(flow,suite,data['failures'],data['scope'],value)
        matches=[]
        for name,material in receipt_bytes.items():
            if (not re.fullmatch(r'build_control_\d+',name)
                    or checkpoint.get('receipts',{}).get(name+'.raw.json')!=hashlib.sha256(material).hexdigest()):
                raise ValueError('Rejected reference receipt is not bound to the latest settled checkpoint')
            try:after=reference_evidence(row['bundle'],candidate,json.loads(material))
            except ValueError:continue  # Another original suite revision has a different source hash.
            if after['failed']:matches.append((name,material,after))
        if not matches:raise ValueError('Rejected proposal lacks its source-bound full reference result')
        name,material,after=matches[-1]
        details=feedback(before,after,suite,candidate)
        result.append({'patch_digest':record['patch_digest'],'proposal_digest':record['proposal_digest'],
            'model_stage':stage,'request_digest':digest(request),'raw_digest':digest(raw[stage]),
            'reference_stage':name,'reference_raw_sha256':hashlib.sha256(material).hexdigest(),'feedback':details})
    return result


def validate(items,value,suite):
    from .reference_repair_coordinator import inventory
    if not isinstance(items,list) or len(items)>6:raise ValueError('Invalid carried verification rejection history')
    cases=inventory(suite);seen=set();history=value['coordinated_history']
    for item in items:
        required={'patch_digest','proposal_digest','model_stage','request_digest','raw_digest','reference_stage','reference_raw_sha256','feedback'}
        f=item.get('feedback',{})
        fields={'policy','base_suite_digest','proposed_suite_digest','before_receipt_digest','after_receipt_digest',
            'tests_before','tests_after','before_failed','after_failed','resolved','regressions','new_failure_observations',
            'executable_source_applied','acceptance_credit','correction_base'}
        if (item.keys()!=required or f.keys()!=fields or item['patch_digest'] in seen
                or not any(r.get('patch_digest')==item['patch_digest'] and r.get('proposal_digest')==item['proposal_digest']
                    and r.get('disposition')=='rolled_back' for r in history)
                or value['source_receipts'].get(item['model_stage'])!={'request_digest':item['request_digest'],'raw_digest':item['raw_digest']}
                or f['policy']!='reference-progress-rejection-v1' or f['base_suite_digest']!=digest(suite.model_dump())
                or f['before_receipt_digest']!=value['retained_reference']['receipt_digest']
                or set(f['before_failed'])!=set(value['retained_reference']['failed'])
                or not set(f['before_failed'])<=cases.keys() or not set(f['after_failed'])<=cases.keys()
                or set(f['resolved'])!=set(f['before_failed'])-set(f['after_failed'])
                or set(f['regressions'])!=set(f['after_failed'])-set(f['before_failed'])
                or {o['case'] for o in f['new_failure_observations']}!=set(f['regressions'])
                or any({k:o[k] for k in ('id','path','test_name')}!=cases[o['case']] for o in f['new_failure_observations'])
                or f['tests_before']!=len(cases) or f['tests_after']!=len(cases)
                or f['executable_source_applied'] is not False or f['acceptance_credit'] is not False
                or any(not re.fullmatch('[a-f0-9]{64}',s) for s in [item['reference_raw_sha256'],
                    f['proposed_suite_digest'],f['before_receipt_digest'],f['after_receipt_digest']])):
            raise ValueError('Carried verification feedback differs from its source, inventory or rejected receipts')
        seen.add(item['patch_digest'])
