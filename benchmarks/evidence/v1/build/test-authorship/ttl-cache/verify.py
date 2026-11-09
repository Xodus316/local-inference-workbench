"""Validate controls with a time-bucket oracle and known mutation witnesses."""
import copy
import json
import random
import subprocess
import sys
from pathlib import Path
from sweep import audit as mutable
from segments import audit as history
ROOT = Path(__file__).resolve().parent


def oracle(data):
    # Small integer-time oracle: walk every time, expire timers, process input-order bucket.
    state, reads = {}, []
    events = data['events']
    for t in range(max((e['time'] for e in events), default=0)+1):
        state = {k:(v,end) for k,(v,end) in state.items() if end is None or end > t}
        for e in events:
            if e['time'] != t: continue
            k = e['key']
            if e['op'] == 'put':
                state.pop(k, None)
                if e['ttl'] != 0:
                    state[k] = (e['value'], None if e['ttl'] is None else t+e['ttl'])
            elif e['op'] == 'delete': state.pop(k, None)
            else: reads.append([t,k,state[k][0] if k in state else None])
    return dict(reads=reads, live=[[k,v[0]] for k,v in sorted(state.items())])


def outcome(fn, data):
    try:return fn(copy.deepcopy(data))
    except (ValueError,TypeError,KeyError):return None


def main():
    held=json.loads((ROOT/'heldout.json').read_text())
    for data, expected in held['cases'].values():
        assert outcome(mutable,data)==outcome(history,data)==expected
    rand=random.Random(901)
    for _ in range(2500):
        events=[]
        for _ in range(rand.randrange(9)):
            e=dict(time=rand.randrange(6),key=rand.choice(['a','b']),op=rand.choice(['put','get','delete']))
            if e['op']=='put': e.update(value=rand.choice([-2,0,7]),ttl=rand.choice([None,0,1,3]))
            events.append(e)
        data={'events':events}
        assert mutable(data)==history(data)==oracle(data), data
    invalid=[None, [], {}, {'events':None},{'events':[None]}]
    base={'time':0,'key':'a','op':'put','value':0,'ttl':None}
    for field, values in {'time':[True,0.0,-1,'0'], 'key':['','A','é',1], 'op':['PUT','unknown',None,{}], 'value':[True,1.0,None], 'ttl':[True,1.0,-1,'1']}.items():
        for v in values: invalid.append({'events':[dict(base,**{field:v})]})
    for field in base:
        e=dict(base);del e[field];invalid.append({'events':[e]})
    for data in invalid:assert outcome(mutable,data) is None and outcome(history,data) is None
    for path in (ROOT/'sweep.py',ROOT/'segments.py'):
        for data,expected in list(held['cases'].values())+[(d,None) for d in invalid]:
            p=subprocess.run([sys.executable,str(path)],input=json.dumps(data),capture_output=True,text=True,timeout=3)
            assert (p.returncode!=0 and p.stdout=='') if expected is None else (p.returncode==0 and json.loads(p.stdout)==expected)
        p=subprocess.run([sys.executable,str(path)],input='{broken',capture_output=True,text=True,timeout=3)
        assert p.returncode!=0 and p.stdout==''
    code=(ROOT/'sweep.py').read_text()
    for m in held['mutations']:
        assert code.count(m['original'])==1
        env={'__name__':'control'}
        exec(compile(code.replace(m['original'],m['replacement']),m['id'],'exec'),env)
        data,expected=held['cases'][m['witness']]
        assert outcome(env['audit'],data)!=expected, m['id']
    print(json.dumps({'random_inputs_cross_checked':2500,'controls':2,'manual_cases':len(held['cases']),'invalid_inputs':len(invalid),'witnessed_mutations':len(held['mutations'])}))


if __name__=='__main__':main()
