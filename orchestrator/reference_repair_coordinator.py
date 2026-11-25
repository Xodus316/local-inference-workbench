"""Reference qualification repairs share agent authority and must prove nonregressing progress."""
from copy import deepcopy
from collections import Counter
import hashlib
import json
from pathlib import Path,PurePosixPath
import re


from .build_coverage import python_test_names
from .build_discovery import CASE_LINE
from .build_workflow import validate_baseline
from .project_build import execution_settled,matching_image_checks,sandbox_stage_results
from .repair_coordinator import INSTRUCTION,compose_tests,root_hints,test_scope,prior_attempts,patch_type,metadata_projection
from .store import digest


def inventory(suite):
    result={}
    for path,names in python_test_names({f.path:f.content for f in suite.files}).items():
        module='.'.join(PurePosixPath(path).with_suffix('').parts[1:])
        for name in names:result[module+'.'+name]={'id':path+'::'+name,'path':path,'test_name':name}
    return result


def reference_evidence(bundle,suite,receipt,*,profile=None,selected=None):
    """Only complete, source-bound test-side failures; no reference source or API logs enter model data."""
    rows=receipt.get('commands',[]);sandbox=receipt.get('sandbox',{})
    expected={f['path']:hashlib.sha256(f['content'].encode('utf8')).hexdigest()
        for f in [*bundle['project_reference']['files'],*[f.model_dump() for f in suite.files]]}
    image=bundle['sandbox']['image_id'];requirements=bundle['project_environment']['verification']
    if (not execution_settled(receipt) or len(rows)!=1 or rows[0].get('source_sha256')!=expected
            or rows[0].get('source_unchanged') is not True or sandbox.get('image_id')!=image
            or sandbox.get('network')!='disabled' or sandbox.get('read_only_project') is not True
            or sandbox.get('read_only_root') is not True):
        raise ValueError('Reference repair requires settled source-bound execution in the pinned sandbox')
    summary=sandbox_stage_results(rows[0])
    if not summary or not matching_image_checks(receipt,[summary],requirements):
        raise ValueError('Reference repair requires conclusive pinned verifier checks')
    if any((summary.get('checks',{}).get(name) or {}).get('status')!='passed'
            for name in requirements['required_checks'] if name!='python-unittest'):
        raise ValueError('Reference infrastructure/build failures cannot authorize test repair')
    cases=inventory(suite);wanted=set(cases) if selected is None else set(selected)
    if not wanted or not wanted<=cases.keys():raise ValueError('Reference repair selection exceeds actual test identities')
    if profile is not None:
        from .sandbox_profiles import verification_profile_identity
        identity=verification_profile_identity(profile,image)
        if (sandbox.get('effective_runtime')!=identity or rows[0].get('effective_runtime')!=identity
                or rows[0].get('profile_sources_unchanged') is not True
                or summary.get('repair_probe')!={'policy':'targeted-reference-diagnostic-v1',
                    'cases':sorted(wanted),'acceptance_credit':False}):
            raise ValueError('Targeted diagnostic does not bind the exact selected tests and pinned tool')
    elif not wanted==set(cases):raise ValueError('Full reference qualification must run every original test')
    failures=[f for f in summary.get('failures',[]) if f.get('stage')=='python-unittest']
    if re.search(r'(?m)^test_\w+ \([\w.]+\) .*\.\.\. skipped',rows[0].get('stderr','')):
        raise ValueError('Reference progress cannot be earned by skipping an original test')
    if receipt.get('status')=='passed':
        if failures or (selected is None and not validate_baseline(receipt,suite)):
            raise ValueError('Reference passing receipt lacks complete test execution evidence')
        # A diagnostic must prove each requested test actually ran; it never earns acceptance credit.
        observed=Counter();lines=rows[0].get('stderr','').splitlines()
        for index,line in enumerate(lines):
            if match:=CASE_LINE.fullmatch(line):
                name=match['qualname'];method=match['method']
                ok=match['status']==' ... ok' or (match['status'] is None and index+1<len(lines) and lines[index+1].endswith(' ... ok'))
                if ok:observed[name if name.endswith('.'+method) else name+'.'+method]+=1
        if set(observed)!=wanted or any(n!=1 for n in observed.values()):
            raise ValueError('Reference execution did not execute every selected case successfully')
        return {'failed':[],'tests_run':len(wanted),'receipt_digest':digest(receipt),'failures':[]}
    if receipt.get('status')!='failed' or len(failures)!=1:
        raise ValueError('Reference repair requires a completed test failure')
    failure=failures[0];items=failure.get('test_failures',[])
    if (failure.get('test_failures_complete') is not True or failure.get('omitted_test_failures')!=0
            or failure.get('test_failure_count')!=len(items) or failure.get('tests_run')!=len(wanted)):
        raise ValueError('Incomplete reference failure inventory cannot prove repair progress')
    observations=[];seen=set()
    for item in items:
        match=CASE_LINE.fullmatch(item.get('test',''))
        if not match:raise ValueError('Reference failure lacks an exact test identity')
        name=match['qualname'];method=match['method'];case=name if name.endswith('.'+method) else name+'.'+method
        if case not in wanted or case in seen:raise ValueError('Reference failure identity is missing or duplicated')
        seen.add(case);observations.append({**cases[case],'case':case,'diagnostic':item.get('assertion','')})
    return {'failed':sorted(seen),'tests_run':len(wanted),'receipt_digest':digest(receipt),'failures':observations}


def progress(before,after):
    prior=set(before['failed']);current=set(after['failed'])
    if after['tests_run']!=before['tests_run'] or not current<prior:
        raise ValueError('Repair did not reduce failed cases without regressions; retain previous source')
    return {'resolved':sorted(prior-current),'remaining':sorted(current),'regressions':[],
        'before_failed':len(prior),'after_failed':len(current)}


def targeted(flow,suite,cases,index):
    path=Path(__file__).with_name('targeted_reference_probe.py')
    profile={'policy':'verification-profile-v1','name':'repair-targeted','phase':'candidate',
        'entrypoint':'/usr/bin/tini','environment':{'WORKBENCH_REPAIR_CASES':json.dumps(sorted(cases))},
        'readonly_files':[{'source':str(path),'target':'/opt/evaluation/repair_probe.py',
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}],
        'commands':['python3 -I /opt/evaluation/repair_probe.py'],
        'verification':{**flow.bundle['project_environment']['verification'],'marker':'OFFLINE_PROJECT_RESULT'}}
    reference=flow.artifact_type(summary='Trusted reference',files=flow.reference['files'])
    project=flow.project_type(summary=reference.summary,files=[*reference.files,*suite.files])
    stage=f'whole_coordinated_{index}_targeted';saved=flow.stage;flow.stage=stage
    try:receipt=flow.runtime._build_execution(flow.state,stage,9000+index,project,suite,verification_profile=profile)
    finally:flow.stage=saved
    return reference_evidence(flow.bundle,suite,receipt,profile=profile,selected=cases),stage


def repair(flow,suite,result):
    before=reference_evidence(flow.bundle,suite,result)
    if not before['failed']:raise ValueError('Reference repair requires observed failures')
    retained=getattr(flow,'_pending_repair',None)
    items=retained['evidence'] if retained is not None else before['failures']
    scope=retained['scope'] if retained is not None else test_scope(suite,items)
    attempts=flow.evidence.setdefault('coordinated_repairs',[])
    original=digest(suite.model_dump())
    if retained is not None and retained['base_suite_digest']!=original:
        raise ValueError('Retained repair proposal no longer matches the current test source')
    instruction=INSTRUCTION+' This is pre-freeze independent test qualification against the trusted reference. '+flow.frontend_dom_test_brief
    data={'base_suite':suite.model_dump(),'base_suite_digest':original,'scope':scope,'failures':items,
        'root_cause_hints':root_hints(items),'read_only_application_inventory':list(suite.implementation_files),
        'previous_attempts':prior_attempts(attempts,flow=flow,suite=suite),
        'verification_plan':['affected Python syntax/static checks','selected original reference cases',
            'all original reference cases with unchanged verifier','all downstream candidate/review/delivery gates']}

    def checked(raw):
        if not raw.get('edits'):raise ValueError('Reference qualification needs a causal anchored repair of '+str(len(items))+' observed failed cases')
        patch_digest=digest(raw['edits'])
        if any(r['patch_digest']==patch_digest and r['base_suite_digest']==original for r in attempts):
            raise ValueError('This patch already failed against the same source; consult with new evidence')
        record={'patch_digest':patch_digest,'proposal_digest':digest(raw),'edits':deepcopy(raw['edits']),
            'base_suite_digest':original,'disposition':'rolled_back'}
        parsed=patch_type(items).model_validate(raw).model_dump()
        record['metadata_projection']=metadata_projection(raw,parsed)
        if retained is not None:record['retained_model_stage']=retained['source_stage'];record['migration']=retained['migration']
        if flow.counters['coordinated_patch']>=6:raise ValueError('Coordinated patch validation budget exhausted')
        flow.counters['coordinated_patch']+=1
        flow.evidence['budget_counters']=dict(flow.counters)
        attempts.append(record);saved=flow.stage;index=flow.counters['coordinated_patch']
        try:
            candidate=compose_tests(flow,suite,items,scope,raw)
            flow.compile_test_suite(candidate)
            affected_cases=sorted(set(before['failed'])|{x['case'] for x in items})
            affected,_=targeted(flow,candidate,affected_cases,index)
            if not set(affected['failed'])<set(before['failed']):
                raise ValueError('Affected checks show no progress; retain previous source')
            full=flow.control(candidate)
            after=reference_evidence(flow.bundle,candidate,full)
            record.update(progress=progress(before,after),disposition='promoted',
                candidate_suite_digest=digest(candidate.model_dump()),reference_receipt_digest=digest(full))
            # Reuse only this actual completed full-gate receipt at the next normal check_suite iteration.
            flow._coordinator_reference={'suite_digest':digest(candidate.model_dump()),'receipt':full,'stage':flow.stage}
            flow.evidence['independent_draft']={'suite':candidate.model_dump(),'acceptance_credit':False}
            return candidate
        except Exception as error:
            from .build_workflow import public_error
            record['rejection']=public_error(error)
            if hasattr(error,'feedback'):record['validation_feedback']=deepcopy(error.feedback)
            raise
        finally:
            flow.stage=saved
            flow.runtime.store.save_artifact(flow.state['run_id'],f'whole_coordinated_{index}_evaluation','raw',deepcopy(record))
    ordinary=None
    if retained is None and flow.counters['build_tests_defect']<flow.limits['max_test_defect_repairs']:
        ordinary=flow.bounded('build_tests_defect','max_test_defect_repairs')
    if retained is not None:flow._pending_repair=None
    return flow.recovery.repair_patch(stage='build_control_recovery',role='test_author',instruction=instruction,
        data=data,validator=checked,ordinary=ordinary,retained=retained)
