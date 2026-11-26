"""Prepare, run or resume the General coding workflow with project configuration only."""
import argparse
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import subprocess
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(Path(__file__).resolve().parent)]
from orchestrator.config import Config
from orchestrator.runtime import Runtime
from orchestrator.store import Store,digest
from orchestrator.general_coding import validate
from orchestrator.whole_project_setup import validate_fresh_run
from orchestrator.repository import file_at
import native_adapter as native
from role_routes import validate_opencode_routes


def load(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8',newline='\n')


def source_files():
    return sorted([*ROOT.glob('orchestrator/**/*.py'),*Path(__file__).parent.glob('*.py')])


def verify_closed_lease(directory):
    directory=Path(directory)
    if not (directory/'cleanup.json').is_file() or not (directory/'owner.json').is_file():
        raise ValueError('Previous backend lease is unresolved; reconcile exact owned resources')
    closed=load(directory/'cleanup.json');owner=load(directory/'owner.json')
    if not all(closed.get(k) is True for k in ('stopped','transport_drained','bridge_closed')):
        raise ValueError('Previous backend transport or cleanup is unresolved')
    observed=json.loads(subprocess.check_output(['docker','inspect',owner['container_id']],text=True))[0]
    if (observed['Id']!=owner['container_id'] or observed['Image']!=owner['image_id']
            or observed['Name']!='/'+owner['name']
            or observed['Config']['Labels'].get(owner['ownership_label'])!=owner['ownership_nonce']
            or observed['State']['Running'] or observed['State']['Pid']!=0):
        raise ValueError('Exact owned backend is not physically closed')
    return closed


class IdentityOnly:
    def __init__(self,value):self.value=deepcopy(value)
    def identity(self):return deepcopy(self.value)
    def __getattr__(self,name):raise RuntimeError('Preparation cannot dispatch '+name)


class GeneralRuntime(Runtime):
    def before_whole_project_worker(self,role,cancel,deadline):
        self.native_backend.activate(cancel,math.inf if deadline is None else deadline)


def install(runtime,bindings,*,backend=None,pin=None,executable=None):
    limits=runtime.config.build.general_coding
    if limits is None:raise ValueError('Enable build.general_coding explicitly')
    runtime.whole_project_workers={role:(IdentityOnly(value) if backend is None else native.Worker(
        executable,backend.bridge,pin,role=role,source_bytes=limits.source_bytes,test_bytes=limits.test_bytes))
        for role,value in bindings['whole_project_workers'].items()}
    runtime.whole_project_prompt_tokenizers={role:(IdentityOnly(value) if backend is None else native.PromptTokenizer(pin,role=role))
        for role,value in bindings['whole_project_prompt_tokenizers'].items()}
    if backend is not None:runtime.native_backend=backend


def prepare(data,config_path,request_path,pin_path,executable):
    data=Path(data).resolve()
    if data.exists():raise ValueError('Use a new data directory for a fresh General workflow')
    paths=[Path(p).resolve(strict=True) for p in (config_path,request_path,pin_path,executable)]
    config_path,request_path,pin_path,executable=paths
    config=Config.model_validate(load(config_path));pin=load(pin_path)
    if config.build.general_coding is None:raise ValueError('Enable build.general_coding explicitly')
    bindings=native.profile(pin,executable,source_bytes=config.build.general_coding.source_bytes)
    validate_opencode_routes(bindings)
    with closing(GeneralRuntime(data/'state',config,provider=IdentityOnly(bindings['backend']))) as runtime:
        install(runtime,bindings)
        # This is the ordinary Build API, not a project-specific whole_project override.
        row=runtime.prepare(load(request_path))
        validate(row['bundle']);validate_fresh_run(row['bundle'])
        if row['calls'] or row['approved'] or row['status']!='prepared':raise ValueError('Preparation dispatched work')
        save(data/'prepared-bundle.json',row['bundle'])
    manifest={'policy':'general-coding-launch-v1','runtime_root':str(ROOT),'run_id':row['id'],
        'approval_digest':row['digest'],'config':config.model_dump(),'pin':pin,'profile':bindings,
        'opencode':str(executable),'input_sha256':{str(p):sha(p) for p in paths},
        'source_sha256':{str(p):sha(p) for p in source_files()},
        'bundle_sha256':sha(data/'prepared-bundle.json'),'initial_state':'fresh_no_seeds_no_costs'}
    save(data/'general-manifest.json',manifest)
    result={'status':'prepared_not_started','run_id':row['id'],'approval_digest':row['digest'],
        'manifest_sha256':sha(data/'general-manifest.json'),'data':str(data),'model_calls':0}
    save(data/'preparation.json',result);print(json.dumps(result),flush=True)
    return result


def validate_launch(data,expected):
    data=Path(data).resolve();path=data/'general-manifest.json'
    if not expected or sha(path)!=expected:raise ValueError('Supply the exact prepared manifest SHA')
    manifest=load(path)
    if manifest['policy']!='general-coding-launch-v1' or manifest['runtime_root']!=str(ROOT):
        raise ValueError('General workflow runtime differs from its preparation')
    if manifest['source_sha256']!={str(p):sha(p) for p in source_files()}:
        raise ValueError('Prepared runtime source changed; prepare a new explicit continuation')
    if any(sha(p)!=value for p,value in manifest['input_sha256'].items()):raise ValueError('Prepared inputs changed')
    if sha(data/'prepared-bundle.json')!=manifest['bundle_sha256']:raise ValueError('Prepared bundle changed')
    bundle=load(data/'prepared-bundle.json')
    if digest(bundle)!=manifest['approval_digest']:raise ValueError('Prepared approval changed')
    validate(bundle);validate_fresh_run(bundle);validate_opencode_routes(manifest['profile'])
    return manifest,bundle


def run(data,expected,*,resume=False,execute=False):
    if not execute:raise ValueError('Starting inference requires explicit --execute')
    data=Path(data).resolve();manifest,bundle=validate_launch(data,expected)
    config=Config.model_validate(manifest['config']);pin=manifest['pin'];executable=Path(manifest['opencode'])
    with closing(Store(data/'state',config.storage_bytes)) as store:
        row=store.get(manifest['run_id'])
        if row['digest']!=manifest['approval_digest'] or row['status'] not in ({'paused','interrupted'} if resume else {'prepared'}):
            raise ValueError('Run state or approval changed')
        if store.has_unknown_calls() or store.unsettled_operations(row['id']):
            raise ValueError('Resolve unknown native/sandbox execution before creating another backend lease')
    for prior in (data/'sessions').iterdir() if (data/'sessions').exists() else ():
        verify_closed_lease(prior/'backends/qwen')
    session=data/'sessions'/uuid4().hex;session.mkdir(parents=True)
    save(session/'run-intent.json',{'policy':manifest['policy'],'manifest_sha256':expected,
        'approval_digest':manifest['approval_digest'],'resume':resume,'no_operator_rescue':True,
        'started_utc_epoch':time.time()})
    terminal=None
    try:
        with native.Backend(session/'backends/qwen',pin) as backend:
            provider=native.ReviewerProvider(backend,pin,executable,source_bytes=config.build.general_coding.source_bytes)
            with closing(GeneralRuntime(data/'state',config,provider)) as runtime:
                install(runtime,manifest['profile'],backend=backend,pin=pin,executable=executable)
                if (provider.identity()!=bundle['backend']
                        or {r:w.identity() for r,w in runtime.whole_project_workers.items()}!=bundle['whole_project_workers']
                        or {r:w.identity() for r,w in runtime.whole_project_prompt_tokenizers.items()}!=bundle['whole_project_prompt_tokenizers']):
                    raise ValueError('Live deployment differs from its approved roles')
                runtime.approve(row['id'],manifest['approval_digest'],resume=resume)
                previous=None
                while True:
                    terminal=runtime.store.get(row['id']);calls=terminal['calls']
                    progress={'run_id':row['id'],'status':terminal['status'],'journal_calls':len(calls),
                        'stage':calls[-1]['stage'] if calls else 'queued','checkpoints':len(terminal['checkpoints'])}
                    save(data/'progress.json',progress)
                    if progress!=previous:print(json.dumps(progress),flush=True);previous=progress
                    if terminal['status'] not in {'prepared','queued','running','pausing','cancelling'}:break
                    time.sleep(5)
                save(session/'terminal.json',terminal);save(data/'terminal.json',terminal)
        # Cleanup and source identity must settle before any delivery is exported.
        validate_launch(data,expected)
        cleanup=verify_closed_lease(session/'backends/qwen')
        closed=all(cleanup.get(k) is True for k in ('stopped','transport_drained','bridge_closed'))
        if not closed:raise ValueError('Owned backend cleanup is incomplete; no validated delivery')
        if terminal['status']=='completed':
            artifact=terminal['result']['output']['artifact'];hashes={}
            for item in artifact['files']:
                target=file_at(data/'candidate',item['path']);target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(item['content'],encoding='utf8',newline='');hashes[item['path']]=sha(target)
            save(data/'delivery.json',{'artifact_digest':digest(artifact),'file_sha256':hashes})
        outcome={'status':terminal['status'],'no_operator_intervention':True,'runtime_source_unchanged':True,
            'cleanup':{'qwen':cleanup},'owned_backend_closed':closed,
            'error':(terminal.get('result') or {}).get('output',{}).get('error'),
            'finished_utc_epoch':time.time()}
        save(session/'outcome.json',outcome);save(data/'outcome.json',outcome)
        print(json.dumps(outcome),flush=True);return outcome
    except BaseException as error:
        from orchestrator.stage_recovery import external_fix_request
        save(data/'recovery-request.json',external_fix_request('infrastructure',error))
        save(session/'runner-error.json',{'type':type(error).__name__,'message':str(error),'automatic_retry':False})
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','start','resume','validate'])
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--config',type=Path);parser.add_argument('--request',type=Path)
    parser.add_argument('--pin',type=Path);parser.add_argument('--opencode',type=Path)
    parser.add_argument('--manifest-sha256');parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    if args.action=='prepare':
        if any(v is None for v in (args.config,args.request,args.pin,args.opencode)):
            parser.error('prepare requires --config, --request, --pin and --opencode')
        prepare(args.data,args.config,args.request,args.pin,args.opencode)
    elif args.action=='validate':validate_launch(args.data,args.manifest_sha256);print('Prepared General workflow valid; no inference')
    else:run(args.data,args.manifest_sha256,resume=args.action=='resume',execute=args.execute)


if __name__=='__main__':main()
