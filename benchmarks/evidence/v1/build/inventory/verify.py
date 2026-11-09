"""Evaluator-only case check. Run generated implementations only inside Docker."""
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def check(path):
    cases=json.loads((ROOT/'heldout-cases.json').read_text())['cases']
    results=[]
    for case in cases:
        text=case.get('raw_input',json.dumps(case.get('input')))
        try:
            p=subprocess.run([sys.executable,str(path)],input=text,text=True,capture_output=True,timeout=3)
        except subprocess.TimeoutExpired:
            results.append({'case':case['id'],'passed':False,'error':'timeout'})
            continue
        if case['expected'] is None:
            passed=p.returncode!=0 and p.stdout==''
        else:
            try:
                # Preserve JSON number/boolean distinctions, ignoring only object key order.
                passed=p.returncode==0 and json.dumps(json.loads(p.stdout),sort_keys=True)==json.dumps(case['expected'],sort_keys=True)
            except ValueError:passed=False
        results.append({'case':case['id'],'passed':passed})
    print(json.dumps({'cases':len(results),'passed':all(r['passed'] for r in results),'results':results}))
    return all(r['passed'] for r in results)


if __name__=='__main__':
    # An explicit path is intended only for an isolated evaluator container.
    sys.exit(0 if check(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'reference.py') else 1)
