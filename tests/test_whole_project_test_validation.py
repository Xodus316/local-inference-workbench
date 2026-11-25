from collections import Counter
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from orchestrator.project_build import ProjectTestSuite
from orchestrator.stage_recovery import KEY,DYNAMIC_POLICY,StageRecovery
from orchestrator.store import digest
from orchestrator.whole_project import WholeProjectBuildRun
from orchestrator.whole_project_test_validation import compose
from test_whole_project_sandbox import suite_value


def fixture(*,ordinary=3,shared=1):
    flow=object.__new__(WholeProjectBuildRun)
    flow.bundle={KEY:deepcopy(DYNAMIC_POLICY),'whole_project_repair_policy':'verified-scoped-repair-v1'}
    flow.state={'run_id':'fixture'};flow.stage='build_test_preflight_2';flow.stages={}
    flow.scoped_repairs=True;flow.output={};flow.evidence={}
    flow.counters=Counter(build_tests_defect=ordinary,recovery_output_correction=shared)
    flow.limits={'max_test_defect_repairs':2};flow.contract={'test_bytes':65536}
    flow.frontend_dom_test_brief='';flow.suite_type=ProjectTestSuite;flow.compile_test_suite=Mock()
    store=Mock();store.has_unknown_calls.return_value=False;store.unsettled_operations.return_value=[]
    flow.runtime=SimpleNamespace(store=store,_guard=Mock());flow.recovery=StageRecovery(flow)
    good=suite_value();bad=deepcopy(good)
    bad['files'][0]['content']=bad['files'][0]['content'].replace('1 + 1','missing()')
    original=ProjectTestSuite.model_validate(bad)
    def generate(stage,role,model_type,instruction,data,visible,**kw):
        if stage.endswith('_consult'):
            return {'diagnosis':'Correct the static defect in retained model-authored tests','evidence_quote':data['failure'][:1000],
                'action':'correct_stage_output' if data['available_capabilities'] else 'stop',
                'requested_changes':['Bind the helper used by the preserved assertion']}
        value={'files':deepcopy(good['files'])}
        if not stage.endswith('_output'):
            value={'files':deepcopy(bad['files'])}
            flow.stages[stage]={};flow.stage=stage
            flow._generation_context={'stage':stage,'role':role,'model_type':model_type,'instruction':instruction,
                'data':deepcopy(data),'value':deepcopy(value),'value_digest':digest(value)}
        return value
    flow._generate=Mock(side_effect=generate)
    return flow,original,ProjectTestSuite.model_validate(good)


class WholeProjectTestValidationTests(unittest.TestCase):
    def test_exhausted_ordinary_attempt_uses_remaining_shared_budget_without_refund(self):
        flow,bad,good=fixture();before=bad.model_dump();diagnostics=flow.test_defects(bad)
        corrected=flow.repair_test_defects(bad,diagnostics)
        self.assertEqual(corrected.model_dump(),good.model_dump());self.assertEqual(bad.model_dump(),before)
        self.assertEqual(flow.counters['build_tests_defect'],3)
        self.assertEqual(flow.counters['recovery_output_correction'],2)
        self.assertEqual([c.args[0] for c in flow._generate.call_args_list],
            ['whole_recovery_1_consult','whole_recovery_1_output'])
        self.assertTrue(all(c.args[1]=='test_author' for c in flow._generate.call_args_list))
        flow.compile_test_suite.assert_called_once()
        self.assertFalse(flow.stages['build_tests_defect_recovery']['acceptance_credit'])

    def test_new_repair_response_is_semantically_checked_before_spending_another_ordinary_attempt(self):
        flow,bad,good=fixture(ordinary=0,shared=0)
        corrected=flow.repair_test_defects(bad,flow.test_defects(bad))
        self.assertEqual(corrected.model_dump(),good.model_dump())
        self.assertEqual(flow.counters['build_tests_defect'],1)
        self.assertEqual(flow.counters['recovery_output_correction'],1)
        self.assertEqual([c.args[0] for c in flow._generate.call_args_list],
            ['build_tests_defect_1','whole_recovery_1_consult','whole_recovery_1_output'])

    def test_unknown_completion_or_shared_exhaustion_cannot_dispatch_a_correction(self):
        for blocked in ['unknown','budget']:
            flow,bad,_=fixture(shared=4 if blocked=='budget' else 1)
            if blocked=='unknown':flow.runtime.store.has_unknown_calls.return_value=True
            with self.assertRaisesRegex(ValueError,'static defects'):
                flow.repair_test_defects(bad,flow.test_defects(bad))
            self.assertFalse(any(c.args[0].endswith('_output') for c in flow._generate.call_args_list))
            self.assertEqual(flow.counters['build_tests_defect'],3)
            flow.compile_test_suite.assert_not_called()

    def test_invalid_scope_removed_tests_syntax_size_and_assertions_cannot_pass(self):
        for issue in ['scope','names','syntax','size','assertion']:
            flow,bad,good=fixture();raw={'files':[f.model_dump() for f in good.files]}
            source=raw['files'][0]
            if issue=='scope':source['path']='tests/other.py'
            if issue=='names':source['content']=source['content'].replace('test_value','test_renamed')
            if issue=='syntax':source['content']='def test_value(:\n'
            if issue=='size':flow.contract['test_bytes']=1
            if issue=='assertion':source['content']=source['content'].replace('self.assertEqual(2, 1 + 1)','pass')
            with self.subTest(issue=issue),self.assertRaises(ValueError):compose(flow,bad,flow.test_defects(bad),raw)
            flow.compile_test_suite.assert_not_called()
