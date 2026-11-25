"""One bounded agent capability for grouped, anchored repairs at any workflow gate."""
import ast
from copy import deepcopy
from pathlib import PurePosixPath

from pydantic import Field, model_validator

from .build_coverage import parse_test_files,python_test_definitions
from .project_build import RepairTextEdit,_materialize_text_edits
from .schemas import Record
from .store import digest

POLICY='grouped-anchored-recovery-v1'


def project_prompt_contract(inputs):
    """Keep exact criteria and source once, leaving room for complete source and bounded advice feedback."""
    coordinated=inputs.get('coordinated_repair',{})
    if inputs.get('repair_policy')!=POLICY and coordinated.get('source_complete') is not True:return inputs
    plan=inputs.get('plan')
    if not isinstance(plan,dict):return inputs
    from .whole_project_test_author import numbered_criteria
    if inputs.get('numbered_acceptance_criteria')!=numbered_criteria(plan['acceptance_criteria']):
        raise ValueError('Coordinated repair must retain every exact acceptance criterion')
    value=dict(inputs);value['plan']={k:v for k,v in plan.items() if k in {'goal','assumptions'}}
    previous=inputs.get('test_author_plan_projection',{})
    value['test_author_plan_projection']={**previous,
        'omitted_fields':sorted(set(previous.get('omitted_fields',[]))|{'acceptance_criteria','implementation_files'}),
        'criteria_location':'numbered_acceptance_criteria',
        'implementation_files_location':('base_suite.implementation_files' if inputs.get('repair_policy')==POLICY
            else 'coordinated_repair.immutable_suite_metadata.implementation_files'),
        'reason':'Exact criteria remain numbered and complete; the complete source context already declares the implementation inventory.'}
    return value


class CauseGroup(Record):
    cause:str=Field(min_length=1,max_length=1200)
    failure_ids:list[str]=Field(min_length=1,max_length=80)
    evidence_quote:str=Field(min_length=1,max_length=1200)
    edit_indexes:list[int]=Field(min_length=1,max_length=32)


class CoordinatedPatch(Record):
    summary:str=Field(min_length=1,max_length=1200)
    clusters:list[CauseGroup]=Field(min_length=1,max_length=24)
    edits:list[RepairTextEdit]=Field(min_length=1,max_length=32)

    @model_validator(mode='after')
    def bounded(self):
        text=[s for edit in self.edits for s in (edit.before,edit.after)]
        if any('\0' in s for s in text) or sum(len(s.encode('utf8')) for s in text)>32768:
            raise ValueError('Anchored patch exceeds the 32768-byte edit budget or contains a null byte')
        return self


def test_scope(suite, failures):
    """Failed test modules plus their actual local import dependencies; passing methods stay protected."""
    files={f.path:f.content for f in suite.files}
    trees=parse_test_files(files)
    modules={'.'.join(PurePosixPath(p).with_suffix('').parts[1:]):p for p in files}
    scope={item['path'] for item in failures}
    pending=list(scope)
    while pending:
        path=pending.pop()
        if path not in trees:raise ValueError('Repair failure is outside the current Python test inventory')
        imports=[]
        for node in ast.walk(trees[path]):
            if isinstance(node,ast.ImportFrom) and node.module:imports.append(node.module)
            elif isinstance(node,ast.Import):imports.extend(a.name for a in node.names)
        for name in imports:
            target=modules.get(name)
            if target is not None and target not in scope:scope.add(target);pending.append(target)
    return sorted(scope)


def observations(diagnostics,suite=None):
    result=[]
    definitions=({p:python_test_definitions(t) for p,t in parse_test_files({f.path:f.content for f in suite.files}).items()}
        if suite is not None else None)
    for item in diagnostics:
        for name in item['names']:
            if definitions is not None and name not in definitions[item['path']]:
                matches=[key for key,node in definitions[item['path']].items()
                    if key.rsplit('.',1)[-1]==name and (len([k for k in definitions[item['path']] if k.rsplit('.',1)[-1]==name])==1
                        or node.lineno==item.get('line'))]
                if len(matches)!=1:raise ValueError('Static failure requires an unambiguous existing test identity')
                name=matches[0]
            details=[f.get('excerpt','') for f in item.get('failures',[]) if f.get('test')==name.rsplit('.',1)[-1]]
            result.append({'id':item['path']+'::'+name,'path':item['path'],'test_name':name,
                'diagnostic':'\n'.join(details) or item.get('message') or str(item.get('code','Test failed'))})
    if len({r['id'] for r in result})!=len(result):raise ValueError('Repair evidence repeats a failed test identity')
    return result


def root_hints(items):
    groups={}
    for item in items:
        headline=item['diagnostic'].splitlines()[-1] if item['diagnostic'] else 'unspecified failure'
        # Only a hint: the agent must explain causality with literal evidence and source.
        key=headline.split(':',1)[0]
        groups.setdefault(key,[]).append(item['id'])
    return [{'headline_family':key,'failure_ids':ids,'causal_diagnosis_required':True} for key,ids in sorted(groups.items())]


def prior_attempts(attempts):
    from .agent_recovery import excerpt
    return [{**{k:r[k] for k in ('patch_digest','base_suite_digest','disposition','progress','rejection') if k in r},
        'patch_excerpt':excerpt(r.get('edits',[]),6000)} for r in attempts[-4:]]


def compose_tests(flow,suite,items,scope,raw):
    patch=CoordinatedPatch.model_validate(raw)
    by_id={item['id']:item for item in items};claimed=set();edit_claims=[]
    for cluster in patch.clusters:
        if (len(set(cluster.failure_ids))!=len(cluster.failure_ids) or not set(cluster.failure_ids)<=by_id.keys()
                or claimed.intersection(cluster.failure_ids)):
            raise ValueError('Root-cause groups must cite distinct observed failed cases')
        evidence='\n'.join(by_id[key]['diagnostic'] for key in cluster.failure_ids)
        if not cluster.evidence_quote.strip() or cluster.evidence_quote not in evidence:
            raise ValueError('Root-cause diagnosis must quote its actual failure evidence')
        if any(type(i) is not int or not 0<=i<len(patch.edits) for i in cluster.edit_indexes):
            raise ValueError('Root-cause group cites an absent patch edit')
        claimed.update(cluster.failure_ids);edit_claims.extend(cluster.edit_indexes)
    if set(edit_claims)!=set(range(len(patch.edits))):raise ValueError('Every patch edit requires a causal failure group')
    replacements=_materialize_text_edits(suite.files,scope,patch)
    changed={f.path:f for f in replacements}
    revised=flow.suite_type.model_validate({**suite.model_dump(),'files':[changed.get(f.path,f).model_dump() for f in suite.files]})
    if sum(len(f.content.encode('utf8')) for f in revised.files)>flow.contract['test_bytes']:
        raise ValueError('Patched test suite exceeds its source budget')
    old_trees=parse_test_files({f.path:f.content for f in suite.files})
    new_trees=parse_test_files({f.path:f.content for f in revised.files})
    old={p:python_test_definitions(tree) for p,tree in old_trees.items()}
    new={p:python_test_definitions(tree) for p,tree in new_trees.items()}
    failed={(x['path'],x['test_name']) for x in items}
    for path,definitions in old.items():
        def headers(tree):
            return {n.name:[ast.dump(x,include_attributes=False) for x in [*n.bases,*n.keywords,*n.decorator_list]]
                for n in tree.body if isinstance(n,ast.ClassDef) and any(k.startswith(n.name+'.') for k in definitions)}
        if headers(old_trees[path])!=headers(new_trees[path]):raise ValueError('Anchored repair changed a test class base or decorator')
        if definitions.keys()!=new[path].keys():raise ValueError('Anchored repair changed test identities')
        for name,node in definitions.items():
            if (path,name) not in failed and ast.dump(node,include_attributes=False)!=ast.dump(new[path][name],include_attributes=False):
                raise ValueError('Anchored repair changed a previously passing test: '+path+'::'+name)
    if findings:=flow.test_defects(revised):raise ValueError('Anchored repair has static defects: '+str(findings))
    return revised


def repair(recovery, *,stage,role,instruction,data,validator,ordinary=None):
    """Advertise a concrete original-schema capability, with one common pool and settlement barrier."""
    flow=recovery.flow
    from .agent_recovery import barrier
    if blocked:=barrier(flow,''):
        raise ValueError(blocked)
    material={**deepcopy(data),'repair_policy':POLICY,'proposal_kind':'anchored_edits',
        'ordinary_attempts_are_not_refunded':True}
    flow.stage=stage
    if ordinary is not None:
        raw=flow.ask(ordinary,role,CoordinatedPatch,instruction,**material)
    else:
        raw={'summary':'Host repair request; no model patch has been dispatched','clusters':[],'edits':[]}
        flow.stages[stage]={'repair_policy':POLICY,'original_output_available':False,'acceptance_credit':False}
        flow._generation_context={'stage':stage,'role':role,'model_type':CoordinatedPatch,
            'instruction':instruction,'data':material,'value':deepcopy(raw),'value_digest':digest(raw)}
    return flow.validate_generated(raw,validator)


INSTRUCTION=(
    'Repair the supplied observed failures with small anchored before/after edits. Group cases by actual root '
    'cause; for each cluster cite failure_ids, quote literal diagnostic evidence and identify zero-based edit_indexes. '
    'A shared helper fix may address several failures. Inspect its actual definition and return values; do not guess '
    'signatures. Copy each before anchor exactly and uniquely from current source; edits are simultaneous and may '
    'not overlap. Return only changed fragments, never whole files. Preserve every test/class name, all coverage '
    'and commands, passing test methods and the specification. Correct test expectations only from the public '
    'contract. Do not skip tests, make operations optional, weaken assertions to vacuous checks, or tailor tests '
    'to private reference implementation details. Other source is read-only. The host checks affected source '
    'first, then reruns reference qualification. A patch is promoted only with fewer failed cases and no regressions. '
    'Account for previous_attempts; do not repeat an ineffective patch. No budgets, models or gates may change.')
