"""Additional candidate-only packaging checks; behavioral reference fixtures need not contain docs."""
import json
import re

from .store import digest

KEY='whole_project_candidate_source_contract'
POLICY={'policy':'commerce-candidate-source-contract-v1',
    'checks':['readme-development','mongo-environment','jwt-environment','vite-major','database-only-compose']}


def validate(bundle):
    value=bundle.get(KEY)
    if value is not None and value!=POLICY:raise ValueError('Invalid candidate source-contract policy')
    return value is not None


def evaluate(artifact):
    files={f.path:f.content for f in artifact.files};checks={}
    readme=files.get('README.md','').lower()
    checks['readme-development']=all(word in readme for word in ('gradle','npm','docker'))
    yml=re.sub(r'(?m)^\s*#.*$','',files.get('api/src/main/resources/application.yml',''))
    checks['mongo-environment']=bool(re.search(r'\$\{(?:SPRING_DATA_MONGODB_URI|MONGODB_URI)(?=[:}])',yml))
    checks['jwt-environment']=bool(re.search(r'\$\{JWT_SECRET(?=[:}])',yml))
    try:
        package=json.loads(files.get('frontend/package.json','{}'))
        version=package.get('devDependencies',{}).get('vite') or package.get('dependencies',{}).get('vite','')
        checks['vite-major']=bool(re.fullmatch(r'[\^~]?(?:5\.\d+(?:\.\d+)?(?:[-+][\w.-]+)?)',version))
    except (ValueError,TypeError,AttributeError):checks['vite-major']=False
    compose=re.sub(r'(?m)^\s*#.*$','',files.get('docker-compose.yml',''))
    match=re.search(r'(?m)^services:\s*\n((?:[ \t]+[^\n]*\n|\s*\n)*)',compose+'\n')
    block=match[1] if match else ''
    entries=re.findall(r'(?m)^( +)([\w-]+):\s*$',block)
    indent=min((len(a) for a,_ in entries),default=0)
    services=[b for a,b in entries if len(a)==indent]
    checks['database-only-compose']=len(services)==1 and bool(re.search(r'(?m)^\s+image:\s*[\'"]?mongo:7(?:[.\d-]*)(?:[\'"]?\s*)$',block))
    return {'policy':POLICY['policy'],'artifact_digest':digest(artifact.model_dump()),'checks':{
        name:{'status':'passed' if okay else 'failed'} for name,okay in checks.items()},
        'status':'passed' if all(checks.values()) else 'failed','execution_settled':True,
        'feedback':{'candidate_source_contract':{'failed_checks':[k for k,v in checks.items() if not v],
            'requirements':{'readme-development':'README documents Gradle, npm and Docker development steps',
                'mongo-environment':'application.yml reads SPRING_DATA_MONGODB_URI or MONGODB_URI',
                'jwt-environment':'application.yml reads JWT_SECRET',
                'vite-major':'package.json declares Vite major 5',
                'database-only-compose':'docker-compose.yml defines one MongoDB 7 database service'}}},
        'source_unchanged':True,'reference_acceptance_credit':False}


def gate(flow,artifact,suite):
    if not validate(flow.bundle):return artifact
    from .whole_project_gates import repair,snapshot,stop
    history=flow.evidence.setdefault('candidate_source_contract_checks',[])
    if history and history[-1]['artifact_digest']==digest(artifact.model_dump()) and history[-1]['status']=='passed':
        return artifact  # A pure source check is reusable only for identical immutable artifact bytes.
    if len(history)>flow.limits['max_repair_attempts']:stop(flow,'Candidate source-contract checks exhausted their repair bound')
    flow.stage=f'whole_source_contract_{len(history)}'
    result=evaluate(artifact);result['run_id']=flow.state['run_id'];history.append(result)
    flow.stages[flow.stage]={'status':result['status'],'receipt_digest':digest(result)}
    flow.runtime.store.save_artifact(flow.state['run_id'],flow.stage,'raw',result);snapshot(flow)
    return artifact if result['status']=='passed' else repair(flow,artifact,suite,result,'candidate_source_contract')
