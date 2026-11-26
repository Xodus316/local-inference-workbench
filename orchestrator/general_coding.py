"""One explicitly configured coding workflow; project tooling is approval-bound data."""
from copy import deepcopy
from typing import Literal

from pydantic import Field
from .schemas import Record
from .store import digest
from .whole_project_limits import DEFAULT_SOURCE_BYTES,WHOLE_PROJECT_TEST_BYTES

KEY='general_coding_workflow'
POLICY='general-coding-v1'


class GeneralCodingConfig(Record):
    policy:Literal['general-coding-v1']=POLICY
    source_bytes:int=Field(default=DEFAULT_SOURCE_BYTES,ge=1,le=DEFAULT_SOURCE_BYTES)
    test_bytes:Literal[65536]=WHOLE_PROJECT_TEST_BYTES
    recovery:Literal['expanded','legacy']='expanded'
    require_opencode:bool=True
    dependency_reference:dict|None=None
    supplemental_suite:dict|None=None
    java_api_probe:dict|None=None
    frontend_diagnostics:dict|None=None
    candidate_source_contract:dict|None=None

    def options(self):
        value={'source_bytes':self.source_bytes,'test_bytes':self.test_bytes}
        for name in ('dependency_reference','supplemental_suite','java_api_probe','frontend_diagnostics','candidate_source_contract'):
            if getattr(self,name) is not None:value[name]=deepcopy(getattr(self,name))
        if self.recovery=='expanded':
            from .recovery_limits import EXPANDED
            value['recovery_limits']=deepcopy(EXPANDED)
        return value


def normalize_request(raw,config):
    """Opt-in configuration makes the ordinary Build API use the shared engine."""
    if config is None or not isinstance(raw,dict) or raw.get('mode')!='build':return raw
    from .project_build import REVIEWED_POLICY
    if raw.get('build_profile') not in (None,REVIEWED_POLICY):
        raise ValueError('General coding requires the reviewed project profile')
    return {**raw,'build_profile':REVIEWED_POLICY}


def configure(runtime,bundle):
    config=runtime.config.build.general_coding
    from .whole_project_setup import configure_fresh_bundle
    value=configure_fresh_bundle(runtime,bundle,**config.options())
    value[KEY]={'policy':POLICY,'options_digest':digest(config.model_dump())}
    validate(value)
    return value


def validate(bundle):
    declared=bundle.get('build',{}).get('general_coding')
    if declared is None:
        if KEY in bundle:raise ValueError('General workflow requires its configured approval')
        return False
    config=GeneralCodingConfig.model_validate(declared)
    from .whole_project import POLICY as ENGINE_POLICY
    if (bundle.get(KEY)!={'policy':POLICY,'options_digest':digest(config.model_dump())}
            or bundle.get('whole_project_workflow')!=ENGINE_POLICY
            or bundle.get('whole_project_source_bytes')!=config.source_bytes
            or bundle.get('test_suite_bytes')!=config.test_bytes):
        raise ValueError('General coding differs from its shared approved configuration')
    from .recovery_limits import KEY as LIMITS,EXPANDED
    if (bundle.get(LIMITS)!=(EXPANDED if config.recovery=='expanded' else None)):
        raise ValueError('General coding recovery capacity differs from its approval')
    from .stage_recovery import KEY as RECOVERY,DYNAMIC_POLICY
    if bundle.get(RECOVERY)!=DYNAMIC_POLICY:
        raise ValueError('General coding requires shared agent-consulted stage recovery')
    for field,key in (('dependency_reference','project_dependency_reference'),
            ('supplemental_suite','whole_project_regression_suite'),('java_api_probe','whole_project_java_api_probe'),
            ('frontend_diagnostics','whole_project_frontend_diagnostics'),('candidate_source_contract','whole_project_candidate_source_contract')):
        if (getattr(config,field) is None)!=(key not in bundle):
            raise ValueError('General coding capability differs from its declared configuration: '+field)
    if config.require_opencode:
        workers=bundle.get('whole_project_workers',{})
        review=bundle.get('backend',{}).get('review_worker',{})
        if (set(workers)!={'planner','implementer','repairer','test_author'}
                or any(w.get('name')!='opencode' or w.get('auto_compaction') is not True
                       for w in [*workers.values(),review])
                or bundle['backend'].get('agent_transport')!='opencode-cli'
                or bundle['backend'].get('auto_compaction') is not True
                or review.get('role')!='reviewer'):
            raise ValueError('General coding requires compacting OpenCode for all five roles')
    return True
