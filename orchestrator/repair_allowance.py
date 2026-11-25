"""An explicit continuation grant adds repair capacity without resetting history."""
import re
from copy import deepcopy
from .store import digest

CONTINUATION='whole_project_stage_continuation'
POLICY='settled-renewed-reference-continuation-v6'
CARRY_POLICY='settled-carried-reference-continuation-v7'
GRANT='explicit-additional-repair-allowance-v1'


def grant(source_approval,counters,additional):
    if type(additional) is not int or additional!=4:
        raise ValueError('A fresh allowance grants exactly four additional output corrections')
    return {'policy':GRANT,'source_approval_digest':source_approval,
        'source_counters_digest':digest(counters),'additional_output_corrections':additional}


def carry(source,source_approval,counters):
    prior=source[CONTINUATION]
    limit=output_limit(source)
    if counters.get('recovery_output_correction',0)>=limit:
        raise ValueError('No approved repair capacity remains to carry')
    return {'policy':'carried-repair-allowance-v1','source_approval_digest':source_approval,
        'source_counters_digest':digest(counters),'inherited_limit':limit,
        'prior':{k:deepcopy(prior[k]) for k in ('policy','source_approval_digest','counters','repair_allowance')}}


def output_limit(bundle,_depth=0):
    value=bundle.get(CONTINUATION,{})
    from .checkpoint_repair_continuation import POLICY as EPOCH,approved_budgets
    if value.get('policy')==EPOCH:return approved_budgets(value['fresh_budgets'])['output_corrections']
    if value.get('policy') not in {POLICY,CARRY_POLICY}:return 4
    if _depth>16:raise ValueError('Repair grant ancestry exceeds the bounded workflow history')
    allowance=value.get('repair_allowance',{});base=value.get('counters',{})
    if value['policy']==CARRY_POLICY:
        if (allowance.keys()!={'policy','source_approval_digest','source_counters_digest','inherited_limit','prior'}
                or allowance.get('policy')!='carried-repair-allowance-v1'
                or allowance.get('source_approval_digest')!=value.get('source_approval_digest')
                or not re.fullmatch('[a-f0-9]{64}',allowance.get('source_approval_digest',''))
                or allowance.get('source_counters_digest')!=digest(base)
                or not isinstance(allowance.get('prior'),dict)
                or allowance['prior'].get('policy') not in {POLICY,CARRY_POLICY}):
            raise ValueError('Carried repair capacity differs from its settled source and grant')
        limit=output_limit({CONTINUATION:allowance['prior']},_depth+1)
        if (type(allowance['inherited_limit']) is not int or allowance['inherited_limit']!=limit
                or type(base.get('recovery_output_correction',0)) is not int
                or not allowance['prior']['counters'].get('recovery_output_correction',0)<=base.get('recovery_output_correction',0)<limit
                or any(base.get(k,0)<n for k,n in allowance['prior']['counters'].items())):
            raise ValueError('Carrying a grant cannot increase its ceiling or refund inherited costs')
        return limit
    if (allowance.keys()!={'policy','source_approval_digest','source_counters_digest','additional_output_corrections'}
            or allowance.get('policy')!=GRANT
            or allowance.get('source_approval_digest')!=value.get('source_approval_digest')
            or not re.fullmatch('[a-f0-9]{64}',allowance.get('source_approval_digest',''))
            or allowance.get('source_counters_digest')!=digest(base)
            or type(allowance.get('additional_output_corrections')) is not int
            or allowance['additional_output_corrections']!=4
            or type(base.get('recovery_output_correction',0)) is not int
            or not 0<=base.get('recovery_output_correction',0)<=12):
        raise ValueError('Fresh repair allowance is not bound to the retained approval and spent counters')
    return base.get('recovery_output_correction',0)+4


def validate_floors(bundle,counters):
    value=bundle.get(CONTINUATION,{})
    if value.get('policy') not in {POLICY,CARRY_POLICY}:return
    output_limit(bundle)
    if any(type(counters.get(key,0)) is not int or counters.get(key,0)<count
            for key,count in value['counters'].items()):
        raise ValueError('Fresh repair allowance cannot refund inherited costs')


def coordinated_limit(bundle):
    from .checkpoint_repair_continuation import POLICY as EPOCH,approved_budgets
    value=bundle.get(CONTINUATION,{})
    return approved_budgets(value['fresh_budgets'])['coordinated_validations'] if value.get('policy')==EPOCH else 6
