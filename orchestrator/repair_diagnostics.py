"""Focused composed-source feedback; diagnostics never become executable changes."""
import ast
from copy import deepcopy
from types import SimpleNamespace

from .build_coverage import python_test_definitions
from .project_build import RepairTextEdit,_materialize_text_edits
from .store import digest,encoded


class PatchValidationError(ValueError):
    def __init__(self,feedback):
        self.feedback=deepcopy(feedback)
        super().__init__('Anchored repair has static defects: '+str(feedback['diagnostics']))


def _definitions(source):
    try:return python_test_definitions(ast.parse(source))
    except SyntaxError:return {}


def feedback(base,proposed,findings):
    original={f.path:f.content for f in base.files};draft={f.path:f.content for f in proposed.files};contexts=[]
    for finding in findings:
        path=finding['path'];old=_definitions(original[path]);new=_definitions(draft[path])
        names=finding.get('names',[])
        for name in sorted(set(old)|set(new)):
            if name not in names and name.rsplit('.',1)[-1] not in names:continue
            prior=old.get(name);after=new.get(name)
            def segment(source,node):return ast.get_source_segment(source,node) if node is not None else None
            before_bindings={n.id for n in ast.walk(prior) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store)} if prior else set()
            after_bindings={n.id for n in ast.walk(after) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store)} if after else set()
            dangling={key:sorted(n.lineno for n in ast.walk(after) if isinstance(n,ast.Name)
                and isinstance(n.ctx,ast.Load) and n.id==key) for key in before_bindings-after_bindings} if after else {}
            contexts.append({'path':path,'test_name':name,'original_method':segment(original[path],prior),
                'composed_method':segment(draft[path],after),
                'removed_bindings_still_used':{k:v for k,v in dangling.items() if v}})
    # The complete raw proposal and source remain retained independently. Do not silently truncate a method.
    complete=len(encoded(contexts).encode('utf8'))<=16384
    return {'policy':'composed-test-rejection-v1','base_suite_digest':digest(base.model_dump()),
        'proposed_suite_digest':digest(proposed.model_dump()),'diagnostics':deepcopy(findings),
        'method_contexts':contexts if complete else [],'method_contexts_complete':complete,
        'method_contexts_digest':digest(contexts),'executable_source_applied':False,
        'correction_base':'Return replacement anchors against the original base_suite, not this rejected draft. '
            'When renaming a binding, inspect every use in the complete method, including later branches.'}


def replay(flow,suite,record):
    """Recover precise feedback for an old rolled-back patch without accepting or executing it."""
    if record.get('base_suite_digest')!=digest(suite.model_dump()) or record.get('disposition')!='rolled_back':return None
    try:
        from .repair_coordinator import executable_edits
        edits=[RepairTextEdit.model_validate(e) for e in executable_edits(record)]
        changed={f.path:f for f in _materialize_text_edits(suite.files,sorted({e.path for e in edits}),SimpleNamespace(edits=edits))}
        proposed=flow.suite_type.model_validate({**suite.model_dump(),
            'files':[changed.get(f.path,f).model_dump() for f in suite.files]})
        findings=flow.test_defects(proposed)
        if not findings:return None
        return {**feedback(suite,proposed,findings),'historical_record_digest':digest(record),
            'historical_patch_digest':digest(record['edits']),'host_replay_only':True}
    except (ValueError,KeyError,SyntaxError):return None
