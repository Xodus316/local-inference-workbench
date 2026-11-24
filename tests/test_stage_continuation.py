from collections import Counter
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import json
import unittest
from unittest.mock import Mock

from orchestrator.project_build import ProjectTestSuite
from orchestrator.stage_continuation import KEY,pack,restore,validate
from orchestrator.stage_recovery import KEY as RECOVERY,DYNAMIC_POLICY,StageRecovery
from orchestrator.store import digest
from orchestrator.whole_project import WholeProjectBuildRun
from orchestrator.wire import wire_schema
from test_whole_project_flow import PLAN
from test_whole_project_sandbox import suite_value


def fixture():
    b={'backend':{'model':'pinned'},'whole_project_repair_policy':'verified-scoped-repair-v1',
        'build':{'max_repair_attempts':8},'build_contract':{'max_compile_repairs':2,'max_compile_checkpoints':2},
        RECOVERY:deepcopy(DYNAMIC_POLICY)}
    smoke=suite_value();draft={**smoke,'implementation_files':['invented.py']}
    stopped={'receipt':{'scratch_cleanup_confirmed':True,'events':[{'type':'step_finish','part':{'reason':'stop'}}]}}
    raw={'implement':{**deepcopy(stopped),'files':{'app.py':'value = 2\n'}},
         'build_tests':{**deepcopy(stopped),'value':draft},
         'build_tests_retry':{'worker_error':'OpenCode cancelled','backend_cleanup_confirmed':True}}
    requests={'implement':{'operation':'run','editable_paths':['app.py'],'visible_files':{}},
        'build_tests':{'operation':'generate','role':'test_author','visible_files':{},'payload':{
            'schema':wire_schema(ProjectTestSuite),'messages':[{'role':'system','content':'Return the suite'},
                {'role':'user','content':json.dumps({'implementation_files':['app.py']})}]}},
        'build_tests_retry':{'operation':'generate'}}
    row={'id':str(uuid4()),'bundle':b,'digest':digest(b),'approval':{'digest':digest(b)},'approved':True,
        'status':'cancelled','calls':[{'stage':stage,'status':'terminal'} for stage in raw],
        'result':{'output':{'plan':PLAN,'artifact':{'summary':'App','files':[{'path':'app.py','content':'value = 2\n'}]},
            'repair_attempts':0,'compile_repair_attempts':1,'verification':{'whole_project':{
                'self_suite':smoke,'self_suite_digest':digest(smoke),'self_checks':[{'status':'passed'}],
                'self_reference_validation':{'status':'passed'},
                'budget_counters':{'whole_self_repair':3,'whole_compile_initial_1':1,'recovery_request':12}}}},
            'stages':{'build_tests':{'validation_error':'Independent tests changed the approved implementation path inventory'}}}}
    return row,requests,raw,deepcopy(b)


class StageContinuationTests(unittest.TestCase):
    def test_saved_source_and_spent_budgets_restore_but_rejected_tests_get_no_acceptance(self):
        row,requests,raw,b=fixture();v=pack(row,requests,raw,b,closed_execution_audit='a'*64);b[KEY]=v
        flow=object.__new__(WholeProjectBuildRun);flow.bundle=b;flow.counters=Counter();flow.evidence={};flow.output={}
        plan,artifact=restore(flow)
        self.assertEqual(plan.model_dump(),PLAN)
        self.assertEqual(artifact.model_dump(),row['result']['output']['artifact'])
        self.assertEqual(dict(flow.counters),row['result']['output']['verification']['whole_project']['budget_counters'])
        self.assertEqual(flow.output['compile_repair_attempts'],1)
        self.assertFalse(flow.evidence['stage_continuation']['acceptance_credit'])
        # Continue the rejected output through shared correction, not another ordinary author call.
        flow.state={'run_id':str(uuid4())};flow.stages={};flow.output['plan']=PLAN;flow.stage='whole_self_verify_3'
        flow.scoped_repairs=True;flow.suite_type=ProjectTestSuite
        store=Mock();store.has_unknown_calls.return_value=False;store.unsettled_operations.return_value=[]
        flow.runtime=SimpleNamespace(store=store,_guard=Mock());flow.recovery=StageRecovery(flow)
        def generate(stage,*a,**kw):
            if stage.endswith('_consult'):
                return {'diagnosis':'The rejected response listed unapproved application paths',
                    'evidence_quote':'Independent tests changed the approved implementation path inventory',
                    'action':'correct_stage_output','requested_changes':['Copy the original application inventory exactly']}
            return suite_value()
        flow._generate=Mock(side_effect=generate)
        result=flow._author_independent_suite(['app.py'])
        self.assertEqual(result.implementation_files,['app.py'])
        self.assertEqual(flow.stage,'build_tests')
        self.assertEqual([c.args[0] for c in flow._generate.call_args_list],
            ['whole_recovery_1_consult','whole_recovery_1_output'])
        self.assertEqual(flow.counters['whole_self_repair'],3)
        self.assertEqual(flow.counters['whole_compile_initial_1'],1)
        self.assertEqual(flow.counters['recovery_request'],13)
        self.assertEqual(flow.counters['recovery_output_correction'],1)

    def test_unknown_execution_mutated_source_scope_contracts_or_output_are_rejected(self):
        for change in ('unknown','source','scope','budget','model','cancelled_output','schema','reference'):
            row,requests,raw,b=fixture()
            if change=='unknown':row['calls'][-1]['status']='unknown'
            if change=='source':row['result']['output']['artifact']['files'][0]['content']='tampered'
            if change=='scope':requests['implement']['editable_paths']=[]
            if change=='budget':b['build']['max_repair_attempts']=9
            if change=='model':b['backend']['model']='different'
            if change=='cancelled_output':raw['build_tests_retry']['value']={}
            if change=='schema':requests['build_tests']['payload']['schema']={}
            if change=='reference':row['result']['output']['verification']['whole_project']['self_reference_validation']['status']='failed'
            with self.subTest(change=change),self.assertRaises(ValueError):pack(row,requests,raw,b,closed_execution_audit='a'*64)

    def test_continuation_is_not_fresh_or_a_budget_refund(self):
        row,requests,raw,b=fixture();v=pack(row,requests,raw,b,closed_execution_audit='a'*64)
        b['fresh_project']={}
        with self.assertRaises(ValueError):validate(v,b)
        b.pop('fresh_project');v['counters']['whole_self_repair']=4
        with self.assertRaises(ValueError):validate(v,b)
