"""Stop repeated tool-result cycles without treating compaction as progress."""
from collections import Counter
import hashlib
import json

POLICY = {'policy':'tool-result-progress-v1','max_identical_results':8,
          'max_results_without_progress':32,
          'novel_completed_results_are_progress':True}
_WRITES = {'apply_patch','edit','multiedit','write'}


def preview(value):
    data=json.dumps(value,sort_keys=True,ensure_ascii=False).encode('utf8')
    return {'sha256':hashlib.sha256(data).hexdigest(),'complete':len(data)<=1200,
            'json_excerpt':data[:1200].decode('utf8','ignore')}


class OpenCodeProgressError(ValueError):
    def __init__(self, proof):
        self.proof = proof
        super().__init__('OpenCode made no authorized source progress: ' + proof.get('reason','repeated tool result'))


def progress_guard(callback=None, *, revision=lambda:None, deny_tools=False):
    counts = Counter()
    seen_results = set()
    previous = revision()
    stalled = 0
    def observe(event):
        nonlocal previous,stalled
        if deny_tools and event.get('type')=='tool_use':
            part=event.get('part') or {};state=part.get('state') or {}
            raise OpenCodeProgressError({**POLICY,'reason':'tool call in a task-only structured stage',
                'tool':part.get('tool'),'tool_input':preview(state.get('input')),'source_progress':False})
        if callback is not None:callback(event)
        if event.get('type') != 'tool_use':return
        part = event.get('part') or {};state = part.get('state') or {};tool = part.get('tool')
        if state.get('status') not in {'completed','error'}:return
        stalled += 1
        if tool in _WRITES and state['status']=='completed':
            current = revision()
            if current != previous:
                previous = current;counts.clear();stalled=0
                return
        value = {'tool':tool,'status':state['status'],'input':state.get('input'),
                 'output':state.get('output'),'error':state.get('error')}
        result_key = hashlib.sha256(json.dumps({k:v for k,v in value.items() if k!='input'},
            sort_keys=True,ensure_ascii=False).encode('utf8')).hexdigest()
        if state['status']=='completed' and tool not in _WRITES and result_key not in seen_results:
            counts.clear()
            seen_results.add(result_key)
            stalled=0
        signature = hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode('utf8')).hexdigest()
        counts[signature] += 1
        if counts[signature] >= POLICY['max_identical_results'] or stalled >= POLICY['max_results_without_progress']:
            raise OpenCodeProgressError({**POLICY,'tool':tool,'status':state['status'],
                'result_digest':signature,'identical_results':counts[signature],
                'results_without_progress':stalled,
                'tool_input':preview(state.get('input')),
                'tool_result':preview(state.get('error') if state['status']=='error' else state.get('output')),
                'source_progress':False,'compaction_resets_guard':False})
    return observe
