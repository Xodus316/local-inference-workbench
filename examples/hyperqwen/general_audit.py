"""Read-only audit of any completed or stopped General coding workflow."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import re

sys.path.insert(0,str(Path(__file__).resolve().parent))
from general_workflow import load,sha,save,validate_launch,digest,verify_closed_lease


def audit(data,expected):
    data=Path(data).resolve();manifest,bundle=validate_launch(data,expected)
    terminal=load(data/'terminal.json');outcome=load(data/'outcome.json')
    if (terminal['id']!=manifest['run_id'] or terminal['digest']!=manifest['approval_digest']
            or digest(terminal['bundle'])!=terminal['digest'] or terminal['bundle']!=bundle
            or terminal['status'] not in {'completed','failed','review_required','blocked','cancelled','paused'}
            or outcome['status']!=terminal['status']):
        raise ValueError('Terminal evidence differs from the frozen General approval')
    engine=digest(bundle['whole_project_engine_digest'])
    for record in terminal['checkpoints']:
        if not re.fullmatch('[a-f0-9]{64}',record['content_sha256']):raise ValueError('Invalid checkpoint hash')
        path=data/'state/checkpoints'/terminal['id']/(record['content_sha256']+'.json')
        if sha(path)!=record['content_sha256']:raise ValueError('Checkpoint content changed')
        value=load(path)
        if (value['approval_digest']!=terminal['digest'] or value['engine_digest']!=engine
                or any(value[k]!=record[k] for k in ('kind','stage','binding'))):
            raise ValueError('Checkpoint code or approval changed')
        for name,value in value['receipts'].items():
            if Path(name).name!=name or sha(data/'state/artifacts'/terminal['id']/name)!=value:
                raise ValueError('Checkpoint receipt changed')
    native=[];sessions=sorted((data/'sessions').iterdir(),key=lambda s:load(s/'run-intent.json')['started_utc_epoch'])
    for session in sessions:
        intent=load(session/'run-intent.json')
        if (intent['manifest_sha256']!=expected or intent['approval_digest']!=terminal['digest']
                or intent.get('no_operator_rescue') is not True):
            raise ValueError('Session differs from its approved preparation')
        directory=session/'backends/qwen';closed=verify_closed_lease(directory)
        if (session/'outcome.json').is_file():
            if load(session/'outcome.json')['cleanup']['qwen']!=closed:raise ValueError('Session cleanup receipt changed')
        elif not (session/'runner-error.json').is_file():raise ValueError('Session has no settled outcome')
        for path in (directory/'requests').glob('*.json'):
            row=load(path);role=row['role']
            model=(bundle['backend']['model_manifest_sha256'] if role=='reviewer'
                   else bundle['whole_project_workers'][role]['model_digest'])
            if row['native_model_manifest_sha256']!=model:raise ValueError('Native model identity changed')
            if row['status']=='completed':
                if not row.get('done_event') or not row.get('generation_policy_verified'):
                    raise ValueError('Native completion or generation policy is unproven')
            elif terminal['status']=='completed':raise ValueError('Completed delivery contains an unsettled native request')
            native.append(row)
    delivered=0
    if terminal['status']=='completed':
        delivery=load(data/'delivery.json')
        if delivery['artifact_digest']!=digest(terminal['result']['output']['artifact']):
            raise ValueError('Delivered artifact differs from the accepted result')
        from orchestrator.repository import file_at
        for name,value in delivery['file_sha256'].items():
            if sha(file_at(data/'candidate',name))!=value:raise ValueError('Delivered bytes changed')
        delivered=len(delivery['file_sha256'])
    report={'status':terminal['status'],'run_id':terminal['id'],'workflow':'general-coding-v1',
        'elapsed_seconds':round(outcome['finished_utc_epoch']-load(sessions[-1]/'run-intent.json')['started_utc_epoch'],3),
        'frozen_source_files_verified':len(manifest['source_sha256']),'checkpoints_verified':len(terminal['checkpoints']),
        'sessions_verified':len(sessions),'native_completed':sum(r['status']=='completed' for r in native),
        'compactions':sum(r.get('auxiliary_request')=='opencode-compaction' for r in native),
        'delivered_files_verified':delivered,'owned_cleanup_verified':True,'no_operator_intervention':True,
        'evidence_sha256':{name:sha(data/name) for name in ('terminal.json','outcome.json')}}
    save(data/'operator-verification.json',report);print(json.dumps(report));return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--manifest-sha256',required=True);args=parser.parse_args();audit(args.data,args.manifest_sha256)
