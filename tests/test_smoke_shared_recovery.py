"""Smoke recovery retains nested causes and uses the approved shared capacity."""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

import test_whole_project_smoke_reference as fixtures
from test_whole_project_smoke_reference import suite,summary,receipt,SOURCE
from orchestrator.whole_project_smoke_reference import detailed_test_failures,validate_smoke_reference
from orchestrator.stage_recovery import KEY,DYNAMIC_POLICY
from orchestrator.recovery_limits import KEY as LIMITS,EXPANDED


class SharedSmokeRecoveryTests(unittest.TestCase):
    def test_closed_smoke_checkpoint_continuation_preserves_costs_and_rejects_tampering(self):
        import test_stage_continuation as stages
        import test_repair_coordinator as coordinated
        from orchestrator.smoke_continuation import pack,validate,restore
        from orchestrator.stage_continuation import KEY as CONTINUATION
        from orchestrator.store import digest
        from types import SimpleNamespace
        from collections import Counter
        row,requests,raw,_=stages.fixture();flow,smoke=coordinated.fixture()
        source=row['bundle'];source.update({LIMITS:deepcopy(EXPANDED),
            'sandbox':flow.bundle['sandbox'],'project_reference':flow.bundle['project_reference'],
            'project_environment':flow.bundle['project_environment'],'whole_project_engine_digest':{'source.py':'a'*64}})
        row['status']='review_required';row['digest']=row['approval']['digest']=digest(source)
        row['calls']=[{'stage':'implement','status':'terminal'}]
        out=row['result']['output'];out.update(suite=None)
        evidence=out['verification']['whole_project'];evidence.update(self_suite=smoke.model_dump(),
            self_suite_digest=digest(smoke.model_dump()),budget_counters={'whole_compile_initial_1':1,'whole_self_reference_repair':2})
        reference=json.dumps(coordinated.receipt(flow,smoke)).encode()
        record={'kind':'graph','stage':'code_build','binding':'b'*64,'created':1}
        checkpoint={'run_id':row['id'],'approval_digest':row['digest'],'engine_digest':digest(source['whole_project_engine_digest']),
            **record,'state':{'output':deepcopy(out)},'receipts':{'build_control_2.raw.json':hashlib.sha256(reference).hexdigest()}}
        data=json.dumps(checkpoint).encode();record['content_sha256']=hashlib.sha256(data).hexdigest();row['checkpoints']=[record]
        target=deepcopy(source)
        value=pack(row,requests,raw,target,closed_execution_audit='c'*64,checkpoint_bytes=data,reference_bytes=reference)
        target[CONTINUATION]=value
        resumed=SimpleNamespace(bundle=target,counters=Counter(),output={},evidence={})
        _,artifact=restore(resumed)
        self.assertEqual(artifact.model_dump(),out['artifact'])
        self.assertEqual(value['self_suite'],smoke.model_dump())
        self.assertEqual(dict(resumed.counters),evidence['budget_counters'])
        self.assertFalse(resumed.evidence['stage_continuation']['acceptance_credit'])
        for field in ('artifact','self_suite','counters'):
            bad=deepcopy(value)
            if field=='counters':bad[field]['recovery_diagnosis']=99
            else:bad[field]['files'][0]['content']+='tamper'
            with self.subTest(field=field),self.assertRaises(ValueError):validate(bad,{**target,CONTINUATION:bad})
        with self.assertRaises(ValueError):
            pack(row,requests,raw,target,closed_execution_audit='c'*64,checkpoint_bytes=data+b' ',reference_bytes=reference)

    def test_shared_smoke_patch_is_promoted_only_after_full_reference_progress(self):
        import test_repair_coordinator as coordinated
        from orchestrator.reference_repair_coordinator import repair
        from orchestrator.project_build import sandbox_stage_results
        from unittest.mock import Mock
        flow,base=coordinated.fixture()
        flow.counters['whole_self_reference_repair']=2
        flow.counters['build_tests_defect']=0
        revised=base.model_copy(deep=True)
        revised.files[0].content=revised.files[0].content.replace('return 0','return 1')
        before=coordinated.receipt(flow,base)
        report=sandbox_stage_results(before['commands'][0])
        report['failures'][0]['status']='failed'
        before['commands'][0]['stderr']=before['commands'][0]['stderr'].split('OFFLINE_PROJECT_RESULT ')[0]+'OFFLINE_PROJECT_RESULT '+json.dumps(report)
        flow.control=Mock(return_value=coordinated.receipt(flow,revised,failed=()))
        with patch('orchestrator.reference_repair_coordinator.targeted') as targeted:
            self.assertEqual(repair(flow,base,before,smoke=True),revised)
        targeted.assert_not_called()  # The full original smoke suite is the browser-independent gate.
        flow.compile_test_suite.assert_called_once_with(revised)
        flow.control.assert_called_once_with(revised)
        self.assertEqual(flow.counters['coordinated_patch'],1)
        self.assertEqual(flow.counters['build_tests_defect'],1)
        self.assertEqual(flow._generate.call_args.args[0:2],('build_tests_defect_1','test_author'))
        self.assertEqual(flow.evidence['self_suite'],revised.model_dump())
        self.assertEqual(flow.evidence['coordinated_repairs'][0]['disposition'],'promoted')
        self.assertNotIn('independent_draft',flow.evidence)

    def test_nested_browser_assertion_requires_matching_complete_test_log_and_case_inventory(self):
        log=('test_product (test_smoke.Smoke) ... FAIL\n\n'+'='*70+
             '\nFAIL: test_product (test_smoke.Smoke)\n'+'-'*70+
             '\nTraceback (most recent call last):\nAssertionError: browser check failed\n'
             'PUBLIC_BROWSER_STATE {"text":"public page"}\n'
             'AssertionError [ERR_ASSERTION]: persisted best score\n\nnull !== \'10\'\n\n'+
             '-'*70+'\nRan 2 tests in 0.01s\n\nFAILED (failures=1)\n')
        report=summary();failure=report['failures'][0]
        report['stage_receipts']=[{'stage':'python-unittest','exit_code':1,'log_complete':True,
            'stream_relay_complete':True,'log_bytes':len(log.encode()),'log_sha256':hashlib.sha256(log.encode()).hexdigest()}]
        raw={'commands':[{'stdout':'','stderr':log+'OFFLINE_PROJECT_RESULT '+json.dumps(report)}]}
        enriched=detailed_test_failures(report,raw,failure)
        self.assertIn("null !== '10'",enriched['test_failures'][0]['assertion'])
        self.assertNotIn('PUBLIC_BROWSER_STATE',enriched['test_failures'][0]['assertion'])
        self.assertNotIn('browser check failed',enriched['test_failures'][0]['assertion'])
        altered=deepcopy(raw);altered['commands'][0]['stderr']='unbound\n'+altered['commands'][0]['stderr']
        self.assertIs(detailed_test_failures(report,altered,failure),failure)
        wrong=deepcopy(failure);wrong['test_failures'][0]['test']='test_clicks (test_smoke.Smoke)'
        self.assertIs(detailed_test_failures(report,raw,wrong),wrong)

    def test_explicit_shared_grant_recovers_after_legacy_smoke_ceiling_without_crediting_independent_tests(self):
        flow,candidate=fixtures.SmokeReferenceTests().workflow([receipt(),receipt(())])
        flow.bundle.update({KEY:deepcopy(DYNAMIC_POLICY),LIMITS:deepcopy(EXPANDED),
                            'whole_project_repair_policy':'verified-scoped-repair-v1'})
        flow.counters['whole_self_reference_repair']=2
        fixed=suite(SOURCE.replace('status = request()',"status = request(token='fixture')"))
        with patch('orchestrator.reference_repair_coordinator.repair',return_value=fixed) as shared:
            self.assertEqual(validate_smoke_reference(flow,candidate,suite()),fixed)
        shared.assert_called_once_with(flow,suite(),unittest.mock.ANY,smoke=True)
        self.assertEqual(flow.counters['whole_self_reference_repair'],2)
        self.assertEqual(flow.evidence['self_reference_validation']['status'],'passed')
        self.assertNotIn('independent_draft',flow.evidence)


if __name__=='__main__':unittest.main()
