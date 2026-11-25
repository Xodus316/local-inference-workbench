"""An explicit continuation grant adds repair capacity without resetting history."""
import re
from .store import digest

CONTINUATION='whole_project_stage_continuation'
POLICY='settled-renewed-reference-continuation-v6'
GRANT='explicit-additional-repair-allowance-v1'


def grant(source_approval,counters,additional):
    if type(additional) is not int or additional!=4:
        raise ValueError('A fresh allowance grants exactly four additional output corrections')
    return {'policy':GRANT,'source_approval_digest':source_approval,
        'source_counters_digest':digest(counters),'additional_output_corrections':additional}


def output_limit(bundle):
    value=bundle.get(CONTINUATION,{})
    if value.get('policy')!=POLICY:return 4
    allowance=value.get('repair_allowance',{});base=value.get('counters',{})
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
    if value.get('policy')!=POLICY:return
    output_limit(bundle)
    if any(type(counters.get(key,0)) is not int or counters.get(key,0)<count
            for key,count in value['counters'].items()):
        raise ValueError('Fresh repair allowance cannot refund inherited costs')
