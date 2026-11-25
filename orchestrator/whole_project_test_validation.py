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
    paths={d['path'] for d in diagnostics}
    selected=[f.model_dump() for f in suite.files if f.path in paths]
    if paths!={f['path'] for f in selected}:
        raise ValueError('Test repair diagnostics exceed the current test inventory')
    instruction=(reason+' Correct the diagnosed test defects and every import or helper needed by the correction. '
        'Return complete files for exactly the named paths. Preserve all test names, assertions, coverage, '
        'specified behavior and unaffected tests. Do not skip tests or replace checks with placeholders. '
        'The host composes the response with unchanged files and checks syntax, undefined names, assertions '
        'and the pinned Python preflight before accepting it.'+flow.frontend_dom_test_brief)
    data={'files':selected,'diagnostics':deepcopy(diagnostics),
        'read_only_support_files':[f.model_dump() for f in suite.files if f.path not in paths],
        'max_replacement_utf8_bytes':flow.contract['test_bytes']-sum(
            len(f.content.encode('utf8')) for f in suite.files if f.path not in paths),
        'required_tests':[c.test_name for c in suite.coverage if c.test_file in paths]}
    if flow.counters['build_tests_defect']<flow.limits['max_test_defect_repairs']:
        stage=flow.bounded('build_tests_defect','max_test_defect_repairs')
        raw=flow.ask(stage,'test_author',BuildTestSyntaxRepair,instruction,**data)
    else:
        # These are actual retained model-authored bytes, not a fabricated worker response.
        # No ordinary attempt is reserved or refunded. A correction requires shared agent authority.
        stage='build_tests_defect_recovery'
        flow.stage=stage
        raw={'files':deepcopy(selected)}
        flow.stages[stage]={'retained_suite_digest':digest(suite.model_dump()),
            'seed_kind':'current_model_authored_test_files','ordinary_dispatch':False,'acceptance_credit':False}
        flow._generation_context={'stage':stage,'role':'test_author','model_type':BuildTestSyntaxRepair,
            'instruction':instruction,'data':data,'value':deepcopy(raw),'value_digest':digest(raw)}
    return flow.validate_generated(raw,lambda value:compose(flow,suite,diagnostics,value))
