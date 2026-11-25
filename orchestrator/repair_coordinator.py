"""One bounded agent capability for grouped, anchored repairs at any workflow gate."""
import ast
from copy import deepcopy
from functools import lru_cache
from pathlib import PurePosixPath
from typing import ClassVar,Literal

from pydantic import Field, model_validator,create_model

from .build_coverage import parse_test_files,python_test_definitions
from .project_build import RepairTextEdit,_materialize_text_edits
from .schemas import Record
from .store import digest

LEGACY_POLICY='grouped-anchored-recovery-v1'
POLICY='grouped-anchored-recovery-v2'
SUPPORTED_POLICIES={LEGACY_POLICY,POLICY}


def project_prompt_contract(inputs):
    """Keep exact criteria and source once, leaving room for complete source and bounded advice feedback."""
    coordinated=inputs.get('coordinated_repair',{})
    if inputs.get('repair_policy') not in SUPPORTED_POLICIES and coordinated.get('source_complete') is not True:return inputs
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
        'implementation_files_location':('base_suite.implementation_files' if inputs.get('repair_policy') in SUPPORTED_POLICIES
            else 'coordinated_repair.immutable_suite_metadata.implementation_files'),
        'reason':'Exact criteria remain numbered and complete; the complete source context already declares the implementation inventory.'}
    if inputs.get('repair_policy')==POLICY and isinstance(inputs.get('base_suite'),dict):
        base=inputs['base_suite'];projected=deepcopy(base)
        if isinstance(base.get('coverage'),list):
            projected['coverage']={'host_retained':True,'digest':digest(base['coverage']),'entries':len(base['coverage']),'editable':False}
            value['base_suite']=projected
            value['base_suite_projection']={'original_digest':digest(base),'source_complete':True,
                'omitted_metadata':['coverage'],'reason':'Host retains the exact immutable coverage map; proposals contain no coverage edits.'}
    rejected=value.get('rejected_output')
    if inputs.get('repair_policy')==POLICY and isinstance(rejected,dict) and rejected.get('edits'):
        value['rejected_output']={'host_retained':True,'digest':digest(rejected),
            'executable_edits_digest':digest(rejected['edits']),
            'replacement_base':'Return a complete new proposal against base_suite; the rejected draft was never applied.'}
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


def project_narrative(raw,limits):
    """Only explicitly declared non-executable narrative fields may be shortened."""
    if not isinstance(raw,dict):return raw
    value=deepcopy(raw)
    for key,maximum in limits.items():
        if isinstance(value.get(key),str) and len(value[key])>maximum:value[key]=value[key][:maximum]
    return value


class EvidenceCauseGroup(Record):
    narrative_fields:ClassVar[dict]={'cause':1200}
    cause:str=Field(min_length=1,max_length=1200)
    evidence_refs:list[str]=Field(min_length=1,max_length=80)
    edit_indexes:list[int]=Field(min_length=1,max_length=32)

    @model_validator(mode='before')
    @classmethod
    def narrative(cls,value):return project_narrative(value,cls.narrative_fields)


class EvidencePatch(CoordinatedPatch):
    narrative_fields:ClassVar[dict]={'summary':1200}
    clusters:list[EvidenceCauseGroup]=Field(min_length=1,max_length=24)

    @model_validator(mode='before')
    @classmethod
    def narrative(cls,value):return project_narrative(value,cls.narrative_fields)


def evidence_catalog(items):
    catalog={}
    for item in items:
        key='E'+digest(item['id'])[:12]
        if key in catalog:raise ValueError('Failure evidence identifiers collide or repeat')
        catalog[key]={'failure_id':item['id'],'diagnostic_digest':digest(item['diagnostic'])}
    if not catalog:raise ValueError('An evidence-bound patch requires observed failures')
    return catalog


@lru_cache(maxsize=32)
def _patch_type(refs):
    group=create_model('BoundEvidenceCauseGroup',__base__=EvidenceCauseGroup,
        evidence_refs=(list[Literal[refs]],Field(min_length=1,max_length=80)))
    return create_model('BoundEvidencePatch',__base__=EvidencePatch,
        clusters=(list[group],Field(min_length=1,max_length=24)))


def patch_type(items):return _patch_type(tuple(sorted(evidence_catalog(items))))


def tagged_evidence(items):
    catalog=evidence_catalog(items);keys={row['failure_id']:key for key,row in catalog.items()}
    return [{**deepcopy(item),'evidence_id':keys[item['id']]} for item in items]


def metadata_projection(raw,normalized):
    changes=[]
    for path,before,after in [('summary',raw.get('summary'),normalized.get('summary')),*[
            (f'clusters.{i}.cause',a.get('cause'),b.get('cause')) for i,(a,b) in enumerate(zip(raw.get('clusters',[]),normalized.get('clusters',[])))]]:
        if before!=after:changes.append({'path':path,'before_digest':digest(before),'after_digest':digest(after),
            'characters_before':len(before),'characters_after':len(after)})
    if raw.get('edits')!=normalized.get('edits'):raise ValueError('Narrative normalization may not change executable edits')
    return {'policy':'non-executable-narrative-projection-v1','raw_digest':digest(raw),
        'normalized_digest':digest(normalized),'fields':changes,'executable_edits_digest':digest(raw.get('edits'))}


def migrate_legacy_proposal(raw,items):
    """Bind existing valid case selections to host evidence; never assert that an invalid legacy quote became true."""
    legacy=CoordinatedPatch.model_validate(project_narrative(raw,{'summary':1200})).model_dump()
    catalog=evidence_catalog(items);by_id={item['id']:item for item in items}
    refs={item['failure_id']:key for key,item in catalog.items()};groups=[];advisory=[]
    for index,group in enumerate(legacy['clusters']):
        if not set(group['failure_ids'])<=by_id.keys():raise ValueError('Retained proposal selected unknown evidence')
        evidence='\n'.join(by_id[key]['diagnostic'] for key in group['failure_ids'])
        advisory.append({'cluster':index,'quote_digest':digest(group['evidence_quote']),
            'literal_quote_matched':bool(group['evidence_quote'].strip()) and group['evidence_quote'] in evidence})
        groups.append({'cause':group['cause'],'evidence_refs':[refs[key] for key in group['failure_ids']],
            'edit_indexes':group['edit_indexes']})
    normalized=patch_type(items).model_validate({'summary':legacy['summary'],'clusters':groups,'edits':legacy['edits']}).model_dump()
    if normalized['edits']!=raw['edits']:raise ValueError('Proposal migration changed executable edits')
    proof={'policy':'legacy-case-selections-to-host-evidence-v1','raw_digest':digest(raw),
        'normalized_digest':digest(normalized),'executable_edits_digest':digest(raw['edits']),
        'evidence_catalog_digest':digest(catalog),'legacy_quotes_advisory':advisory,
        'summary_characters_before':len(raw['summary']),'summary_characters_after':len(normalized['summary'])}
    return normalized,proof


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


def prior_attempts(attempts,*,flow=None,suite=None):
    from .agent_recovery import excerpt
    result=[]
    for record in attempts[-4:]:
        value={k:record[k] for k in ('patch_digest','base_suite_digest','disposition','progress','rejection') if k in record}
        value['patch_excerpt']=excerpt(record.get('edits',[]),2500)
        details=record.get('validation_feedback')
        if details is None and flow is not None and suite is not None:
            from .repair_diagnostics import replay
            details=replay(flow,suite,record)
        if details is not None:value['composed_patch_rejection']=deepcopy(details)
        result.append(value)
    return result


def compose_tests(flow,suite,items,scope,raw):
    referenced=any('evidence_refs' in group for group in raw.get('clusters',[]))
    patch=(patch_type(items) if referenced else CoordinatedPatch).model_validate(raw)
    catalog=evidence_catalog(items)
    by_id={item['id']:item for item in items};claimed=set();edit_claims=[]
    for cluster in patch.clusters:
        failure_ids=([catalog[key]['failure_id'] for key in cluster.evidence_refs] if referenced else cluster.failure_ids)
        if (len(set(failure_ids))!=len(failure_ids) or not set(failure_ids)<=by_id.keys()
                or claimed.intersection(failure_ids)):
            raise ValueError('Root-cause groups must cite distinct observed failed cases')
        evidence='\n'.join(by_id[key]['diagnostic'] for key in failure_ids)
        if not referenced and (not cluster.evidence_quote.strip() or cluster.evidence_quote not in evidence):
            raise ValueError('Root-cause diagnosis must quote its actual failure evidence')
        if any(type(i) is not int or not 0<=i<len(patch.edits) for i in cluster.edit_indexes):
            raise ValueError('Root-cause group cites an absent patch edit')
        claimed.update(failure_ids);edit_claims.extend(cluster.edit_indexes)
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
    if findings:=flow.test_defects(revised):
        from .repair_diagnostics import PatchValidationError,feedback
        raise PatchValidationError(feedback(suite,revised,findings))
    return revised


def repair(recovery, *,stage,role,instruction,data,validator,ordinary=None,retained=None):
    """Advertise a concrete original-schema capability, with one common pool and settlement barrier."""
    flow=recovery.flow
    from .agent_recovery import barrier
    if blocked:=barrier(flow,''):
        raise ValueError(blocked)
    material={**deepcopy(data),'repair_policy':POLICY,'proposal_kind':'anchored_edits',
        'ordinary_attempts_are_not_refunded':True}
    flow.stage=stage
    model_type=patch_type(material['failures'])
    material['failures']=tagged_evidence(material['failures'])
    material['evidence_catalog_digest']=digest(evidence_catalog(material['failures']))
    if retained is not None:
        from .stage_continuation import KEY,PENDING_POLICY
        approval=flow.bundle.get(KEY,{})
        if (approval.get('policy')!=PENDING_POLICY or approval.get('pending_proposal')!=retained
                or retained['role']!=role or digest(data['base_suite'])!=retained['base_suite_digest']):
            raise ValueError('Retained proposal is not bound to this continuation approval and source')
        flow.stages[stage]={'retained_proposal_digest':digest(retained['proposal']),
            'model_redispatched':False,'acceptance_credit':False}
        return validator(deepcopy(retained['proposal']))
    if ordinary is not None:
        raw=flow.ask(ordinary,role,model_type,instruction,**material)
    else:
        raw={'summary':'Host repair request; no model patch has been dispatched','clusters':[],'edits':[]}
        flow.stages[stage]={'repair_policy':POLICY,'original_output_available':False,'acceptance_credit':False}
        flow._generation_context={'stage':stage,'role':role,'model_type':model_type,
            'instruction':instruction,'data':material,'value':deepcopy(raw),'value_digest':digest(raw)}
    def checked(value):
        if value.get('edits'):model_type.model_validate(value)
        return validator(value)
    return flow.validate_generated(raw,checked)


INSTRUCTION=(
    'Repair the supplied observed failures with small anchored before/after edits. Group cases by actual root '
    'cause; for each cluster select its exact host-provided evidence_id values in evidence_refs and identify zero-based edit_indexes. '
    'The host binds those references to literal captured diagnostics; do not reproduce or complete truncated quotes. '
    'A shared helper fix may address several failures. Inspect its actual definition and return values; do not guess '
    'signatures. A binding rename must update every use in the complete enclosing method, including later branches. '
    'Inspect composed_patch_rejection and previous_attempts; never repeat a rejected partial rename. '
    'Copy each before anchor exactly and uniquely from current source; edits are simultaneous and may '
    'not overlap. Return only changed fragments, never whole files. Preserve every test/class name, all coverage '
    'and commands, passing test methods and the specification. Correct test expectations only from the public '
    'contract. Do not skip tests, make operations optional, weaken assertions to vacuous checks, or tailor tests '
    'to private reference implementation details. Other source is read-only. The host checks affected source '
    'first, then reruns reference qualification. A patch is promoted only with fewer failed cases and no regressions. '
    'Account for previous_attempts; do not repeat an ineffective patch. No budgets, models or gates may change.')
