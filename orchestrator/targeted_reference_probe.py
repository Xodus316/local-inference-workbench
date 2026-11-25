"""CPU diagnostic only: execute selected original tests using the immutable image verifier."""
import json
import os
import re
import sys
sys.path.insert(0,'/opt/evaluation')
import quality_entrypoint as quality

cases=json.loads(os.environ['WORKBENCH_REPAIR_CASES'])
if (not isinstance(cases,list) or not cases or len(cases)!=len(set(cases))
        or any(not isinstance(x,str) or not re.fullmatch(r'(?:[A-Za-z_]\w*\.)+[A-Za-z_]\w*',x) for x in cases)):
    raise ValueError('Targeted reference cases must be distinct literal test identities')

class Probe(quality.Verification):
    def verify_api(self,name,command,environment,jar,**kwargs):
        if name=='python-unittest':
            code=('import json,sys,unittest; sys.path.insert(0,sys.argv[1]); '
                's=unittest.TestLoader().loadTestsFromNames(json.loads(sys.argv[2])); '
                'r=unittest.TextTestRunner(verbosity=2).run(s); sys.exit(not r.wasSuccessful())')
            command=['python3','-I','-c',code,str(quality.TESTS),json.dumps(cases)]
        return super().verify_api(name,command,environment,jar,**kwargs)

    def finish(self):
        self.result['repair_probe']={'policy':'targeted-reference-diagnostic-v1','cases':cases,'acceptance_credit':False}
        return super().finish()

quality.Verification=Probe
sys.argv=[__file__,'/bin/sh','-lc','python3 -m unittest discover -s tests -v']
raise SystemExit(quality.main())
