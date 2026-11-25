"""Validate a complete scoped test repair before accepting its model response."""
from copy import deepcopy
import json

from .build_coverage import python_test_names
from .build_models import BuildTestSyntaxRepair
from .store import digest


def compose(flow, suite, diagnostics, raw):
    paths={d['path'] for d in diagnostics}
    replacements=BuildTestSyntaxRepair.model_validate(raw).files
    changed={f.path:f for f in replacements}
    if set(changed)!=paths or len(changed)!=len(replacements):
        raise ValueError('Test defect repair must return exactly the indicated test files')
    merged=suite.model_dump()
    merged['files']=[changed.get(f.path,f).model_dump() for f in suite.files]
    revised=flow.suite_type.model_validate(merged)
    if sum(len(f.content.encode('utf8')) for f in revised.files)>flow.contract['test_bytes']:
        raise ValueError('Corrected test suite exceeds the approved UTF-8 byte budget')
    before=python_test_names({f.path:f.content for f in suite.files if f.path in paths})
    after=python_test_names({f.path:f.content for f in revised.files if f.path in paths})
    if any(not names<=after.get(path,set()) for path,names in before.items()):
        raise ValueError('Test defect repair removed or renamed a test method')
    # Check the entire composed suite, including imports, before accepting this output.
    findings=flow.test_defects(revised)
    if findings:
        raise ValueError('Corrected test suite has static defects: '+json.dumps(findings,sort_keys=True))
    saved_stage=flow.stage
    try:flow.compile_test_suite(revised)  # Pinned Python preflight; never a passing acceptance credit.
    finally:flow.stage=saved_stage
    return revised


def rewrite(flow, suite, diagnostics, reason):
    from .repair_coordinator import INSTRUCTION,compose_tests,observations,root_hints,test_scope,prior_attempts
    items=observations(diagnostics,suite);scope=test_scope(suite,items)
    data={'base_suite':suite.model_dump(),'base_suite_digest':digest(suite.model_dump()),
        'scope':scope,'failures':items,'root_cause_hints':root_hints(items),
        'previous_attempts':prior_attempts(flow.evidence.get('coordinated_repairs',[]))}
    ordinary=None
    if flow.counters['build_tests_defect']<flow.limits['max_test_defect_repairs']:
        ordinary=flow.bounded('build_tests_defect','max_test_defect_repairs')
    def checked(raw):
        if not raw.get('edits'):raise ValueError('Generated tests require an anchored correction: '+str(diagnostics))
        if any(r['patch_digest']==digest(raw['edits']) and r['base_suite_digest']==digest(suite.model_dump())
                for r in flow.evidence.get('coordinated_repairs',[])):
            raise ValueError('This anchored patch already failed against the same source')
        if flow.counters['coordinated_patch']>=6:raise ValueError('Coordinated patch validation budget exhausted')
        flow.counters['coordinated_patch']+=1;flow.evidence['budget_counters']=dict(flow.counters)
        index=flow.counters['coordinated_patch'];saved=flow.stage
        record={'patch_digest':digest(raw['edits']),'proposal_digest':digest(raw),'edits':deepcopy(raw['edits']),
            'base_suite_digest':digest(suite.model_dump()),'disposition':'rolled_back'}
        flow.evidence.setdefault('coordinated_repairs',[]).append(record)
        try:
            revised=compose_tests(flow,suite,items,scope,raw)
            flow.compile_test_suite(revised)
            record.update(disposition='promoted',candidate_suite_digest=digest(revised.model_dump()),
                progress={'before_failed':len(items),'after_failed':0,'regressions':[]})
            flow.evidence['independent_draft']={'suite':revised.model_dump(),'acceptance_credit':False}
            return revised
        except Exception as error:
            from .build_workflow import public_error
            record['rejection']=public_error(error)
            raise
        finally:
            flow.stage=saved
            flow.runtime.store.save_artifact(flow.state['run_id'],f'whole_coordinated_{index}_evaluation','raw',deepcopy(record))
    return flow.recovery.repair_patch(stage='build_tests_defect_recovery',role='test_author',
        instruction=reason+INSTRUCTION+flow.frontend_dom_test_brief,data=data,validator=checked,ordinary=ordinary)
