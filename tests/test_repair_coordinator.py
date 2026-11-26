from collections import Counter
from copy import deepcopy
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from orchestrator.build_workflow import CodeBuild
from orchestrator.project_build import ProjectTestSuite,ProjectArtifact,ProjectProject
from orchestrator.repair_coordinator import compose_tests,test_scope
from orchestrator.reference_repair_coordinator import inventory,reference_evidence,progress,repair
from orchestrator.stage_recovery import KEY,DYNAMIC_POLICY,StageRecovery
from orchestrator.store import digest
from orchestrator.whole_project import WholeProjectBuildRun

SOURCE='''import unittest
def answer():
    return 0
class Checks(unittest.TestCase):
    def test_a(self):
        self.assertEqual(answer(),1)
    def test_b(self):
        self.assertEqual(answer(),1)
    def test_c(self):
        self.assertIn(answer(),[0,1])
'''

def fixture():
    suite=ProjectTestSuite.model_validate({'implementation_files':['app.py'],
        'commands':['python3 -m unittest discover -s tests -v'],'files':[{'path':'tests/test_math.py','content':SOURCE}],
        'coverage':[{'criterion':i,'test_file':'tests/test_math.py','test_name':'Checks.test_'+name,
            'scenario':'Check a sum','expected_result':'One','catches':'Wrong sum'} for i,name in enumerate('abc',1)],'assumptions':[]})
    flow=object.__new__(WholeProjectBuildRun);flow.bundle={KEY:deepcopy(DYNAMIC_POLICY),
        'whole_project_repair_policy':'verified-scoped-repair-v1','sandbox':{'image_id':'pinned-image'},
        'project_reference':{'files':[{'path':'app.py','content':'PRIVATE_REFERENCE_SOURCE'}]},
        'project_environment':{'verification':{'policy':'fixture','required_checks':['trusted-api','python-unittest']}}}
    flow.reference=flow.bundle['project_reference'];flow.contract={'test_bytes':65536};flow.limits={'max_test_defect_repairs':2}
    flow.state={'run_id':'fixture'};flow.stage='build_control_1';flow.stages={};flow.evidence={};flow.output={}
    flow.counters=Counter(recovery_output_correction=3,build_tests_defect=3,recovery_diagnosis=7)
    flow.scoped_repairs=True;flow.frontend_dom_test_brief='';flow.suite_type=ProjectTestSuite
    flow.artifact_type=ProjectArtifact;flow.project_type=ProjectProject;flow.compile_test_suite=Mock()
    store=Mock();store.has_unknown_calls.return_value=False;store.unsettled_operations.return_value=[]
    flow.runtime=SimpleNamespace(store=store,_guard=Mock());flow.recovery=StageRecovery(flow)
    def generate(stage,role,model_type,instruction,data,visible,**kw):
        if stage.endswith('_consult'):
            return {'diagnosis':'Two failed tests use the same helper',
                **({'evidence_ref':data['failure_evidence_id']} if 'failure_evidence_id' in data else {'evidence_quote':data['failure']}),
                'action':'correct_stage_output' if data['available_capabilities'] else 'stop',
                'requested_changes':['Correct the shared helper'] if data['available_capabilities'] else []}
        from orchestrator.repair_coordinator import migrate_legacy_proposal
        return migrate_legacy_proposal(proposal(data['failures']),data['failures'])[0]
    flow._generate=Mock(side_effect=generate)
    return flow,suite

def receipt(flow,suite,failed=('test_math.Checks.test_a','test_math.Checks.test_b'),selected=None):
    cases=inventory(suite);run=sorted(selected or cases)
    failed=set(failed);items=[];lines=[]
    for case in run:
        method=case.rsplit('.',1)[-1];owner=case.rsplit('.',1)[0]
        lines.append(f'{method} ({owner}) ... '+('FAIL' if case in failed else 'ok'))
        if case in failed:items.append({'test':f'{method} ({owner})','status':'failed','assertion':'AssertionError: 0 != 1'})
    lines+=['',f'Ran {len(run)} tests in 0.01s','FAILED (failures='+str(len(items))+')' if items else 'OK']
    summary={'policy':'fixture','cleanup_confirmed':True,'status':'failed' if items else 'passed','exit_code':int(bool(items)),
        'checks':{'trusted-api':{'status':'passed'},'python-unittest':{'status':'failed' if items else 'passed'}},
        'failures':[{'stage':'python-unittest','tests_run':len(run),'test_failures':items,'test_failure_count':len(items),
            'omitted_test_failures':0,'test_failures_complete':True,'api_context':['PRIVATE_API_LOG']}] if items else []}
    source=[*flow.reference['files'],*[f.model_dump() for f in suite.files]]
    return {'status':summary['status'],'sandbox':{'image_id':'pinned-image','network':'disabled','read_only_root':True,'read_only_project':True},
        'commands':[{'exit_code':summary['exit_code'],'cleanup_confirmed':True,'source_unchanged':True,
            'source_sha256':{f['path']:hashlib.sha256(f['content'].encode()).hexdigest() for f in source},
            'stderr':'\n'.join(lines)+'\nOFFLINE_PROJECT_RESULT '+__import__('json').dumps(summary),'stdout':''}]}

def proposal(items):
    return {'summary':'Correct the common helper','clusters':[{'cause':'Both failed cases use answer()',
        'failure_ids':[x['id'] for x in items],'evidence_quote':items[0]['diagnostic'],'edit_indexes':[0]}],
        'edits':[{'path':'tests/test_math.py','before':'return 0','after':'return 1'}]}


class RepairCoordinatorTests(unittest.TestCase):
    def test_source_admission_projection_preserves_exact_criteria_and_avoids_duplicate_contract_text(self):
        from orchestrator.repair_coordinator import project_prompt_contract,POLICY
        from orchestrator.whole_project_test_author import numbered_criteria
        plan={'goal':'Build','acceptance_criteria':['Observable behavior'],'assumptions':[],'implementation_files':['app.py']}
        inputs={'repair_policy':POLICY,'plan':plan,'numbered_acceptance_criteria':numbered_criteria(plan['acceptance_criteria']),
            'base_suite':{'implementation_files':['app.py']}}
        projected=project_prompt_contract(inputs)
        self.assertEqual(projected['numbered_acceptance_criteria'],inputs['numbered_acceptance_criteria'])
        self.assertEqual(projected['base_suite'],inputs['base_suite']);self.assertNotIn('acceptance_criteria',projected['plan'])
        self.assertEqual(inputs['plan'],plan)
        with self.assertRaises(ValueError):project_prompt_contract({**inputs,'numbered_acceptance_criteria':[]})

    def test_reference_budget_falls_back_to_shared_pool_and_verifies_affected_then_full_suite(self):
        flow,suite=fixture();before=receipt(flow,suite);events=[]
        def targeted(f,candidate,cases,index):
            events.append('targeted');return {'failed':[],'tests_run':2},'probe'
        def control(candidate):events.append('full');return receipt(flow,candidate,failed=())
        flow.control=Mock(side_effect=control)
        with patch('orchestrator.reference_repair_coordinator.targeted',side_effect=targeted):
            corrected=repair(flow,suite,before)
        self.assertEqual(events,['targeted','full']);self.assertIn('return 1',corrected.files[0].content)
        self.assertEqual(flow.counters['build_tests_defect'],3);self.assertEqual(flow.counters['recovery_output_correction'],4)
        self.assertEqual(flow.evidence['coordinated_repairs'][0]['disposition'],'promoted')
        self.assertEqual(flow.evidence['coordinated_repairs'][0]['progress']['before_failed'],2)
        self.assertEqual(flow.evidence['coordinated_repairs'][0]['progress']['after_failed'],0)
        self.assertNotIn('PRIVATE_REFERENCE_SOURCE',str(flow._generate.call_args_list))
        self.assertNotIn('PRIVATE_API_LOG',str(flow._generate.call_args_list))
        projected=flow._generate.call_args_list[0].args[4]['coordinated_repair']['failure_observations']
        self.assertTrue(projected['complete']);self.assertIn('AssertionError: 0 != 1',projected['json_excerpt'])
        context=flow._generate.call_args_list[0].args[4]['coordinated_repair']
        self.assertTrue(context['source_complete']);self.assertEqual(context['editable_source'],suite.model_dump()['files'])
        self.assertEqual(context['immutable_suite_metadata']['commands'],suite.commands)
        self.assertEqual(context['immutable_suite_metadata']['coverage']['digest'],digest(suite.model_dump()['coverage']))
        self.assertFalse(context['immutable_suite_metadata']['coverage']['editable'])
        self.assertEqual(suite.files[0].content,SOURCE)
        completed=flow._coordinator_reference['receipt']
        self.assertIs(WholeProjectBuildRun.control(flow,corrected),completed)
        self.assertIsNone(flow._coordinator_reference)

    def test_no_progress_or_regression_rolls_back_without_refunding_the_model_attempt(self):
        for kind in ['no_progress','regression']:
            flow,suite=fixture();flow.control=Mock(side_effect=lambda candidate:receipt(flow,candidate,failed=('test_math.Checks.test_c',)))
            def target(f,candidate,cases,index):
                failures=('test_math.Checks.test_a','test_math.Checks.test_b') if kind=='no_progress' else ()
                return reference_evidence(f.bundle,candidate,receipt(f,candidate,failed=failures)),'probe'
            with patch('orchestrator.reference_repair_coordinator.targeted',side_effect=target):
                with self.assertRaises(ValueError):repair(flow,suite,receipt(flow,suite))
            self.assertEqual(flow.evidence['coordinated_repairs'][0]['disposition'],'rolled_back')
            self.assertEqual(flow.counters['recovery_output_correction'],4)
            self.assertFalse(hasattr(flow,'_coordinator_reference'))
            self.assertEqual(suite.files[0].content,SOURCE)
            if kind=='no_progress':flow.control.assert_not_called()

    def test_anchors_scope_passing_tests_and_causal_evidence_are_authoritative(self):
        for kind in ['ambiguous','outside','passing','evidence','unchanged','overlap','class_skip']:
            flow,suite=fixture();items=reference_evidence(flow.bundle,suite,receipt(flow,suite))['failures'];value=proposal(items)
            if kind=='ambiguous':value['edits'][0]['before']='self.assertEqual('
            if kind=='outside':value['edits'][0]['path']='app.py'
            if kind=='passing':value['edits'][0].update(before='self.assertIn(answer(),[0,1])',after='self.assertIn(answer(),[0,1,2])')
            if kind=='evidence':value['clusters'][0]['evidence_quote']='Invented evidence'
            if kind=='unchanged':value['edits'][0]['after']='return 0'
            if kind=='overlap':value['edits'].append(deepcopy(value['edits'][0]));value['clusters'][0]['edit_indexes']=[0,1]
            if kind=='class_skip':value['edits'][0].update(before='class Checks(unittest.TestCase):',after="@unittest.skip('skip')\nclass Checks(unittest.TestCase):")
            with self.subTest(kind=kind),self.assertRaises(ValueError):compose_tests(flow,suite,items,test_scope(suite,items),value)

    def test_unknown_or_incomplete_reference_execution_cannot_reach_an_agent(self):
        for kind in ['unknown','hash','incomplete','build']:
            flow,suite=fixture();value=receipt(flow,suite)
            if kind=='unknown':value['commands'][0]['cleanup_confirmed']=False
            if kind=='hash':value['commands'][0]['source_sha256']['app.py']='f'*64
            if kind in {'incomplete','build'}:
                import json
                prefix,body=value['commands'][0]['stderr'].split('OFFLINE_PROJECT_RESULT ');summary=json.loads(body)
                if kind=='incomplete':summary['failures'][0]['test_failures_complete']=False
                else:summary['checks']['trusted-api']['status']='failed'
                value['commands'][0]['stderr']=prefix+'OFFLINE_PROJECT_RESULT '+json.dumps(summary)
            with self.subTest(kind=kind),self.assertRaises(ValueError):repair(flow,suite,value)
            flow._generate.assert_not_called()

    def test_failure_reduction_preserves_case_population_and_rejects_new_failures(self):
        before={'failed':['a','b'],'tests_run':3}
        self.assertEqual(progress(before,{'failed':['b'],'tests_run':3})['resolved'],['a'])
        for after in [{'failed':['a','b'],'tests_run':3},{'failed':['c'],'tests_run':3},{'failed':[],'tests_run':2}]:
            with self.assertRaises(ValueError):progress(before,after)

    def test_broker_preserves_original_role_and_common_budget_across_workflow_phases(self):
        for stage,role in [('build_plan','planner'),('implement','implementer'),('whole_self_reference_1','implementer'),
                ('build_control_1','test_author'),('build_compile_repair_1_1','repairer'),
                ('whole_profile_0_0','repairer'),('build_code_review_0','reviewer')]:
            flow,suite=fixture();items=reference_evidence(flow.bundle,suite,receipt(flow,suite))['failures']
            def check(raw):
                if not raw.get('edits'):raise ValueError('A scoped patch is required')
                return raw
            flow.recovery.repair_patch(stage=stage,role=role,instruction='Apply the authorized source patch',
                data={'failures':items},validator=check)
            self.assertEqual(flow._generate.call_args.args[1],role)
            self.assertEqual(flow.counters['recovery_output_correction'],4)
            self.assertEqual(flow.counters['build_tests_defect'],3)

    def test_repeated_patch_cannot_evade_the_guard_by_changing_its_summary(self):
        flow,suite=fixture();items=reference_evidence(flow.bundle,suite,receipt(flow,suite))['failures'];value=proposal(items)
        flow.evidence['coordinated_repairs']=[{'patch_digest':digest(value['edits']),'base_suite_digest':digest(suite.model_dump()),
            'disposition':'rolled_back','edits':value['edits'],'rejection':'No progress'}]
        with patch('orchestrator.reference_repair_coordinator.targeted') as targeted:
            with self.assertRaisesRegex(ValueError,'already failed'):repair(flow,suite,receipt(flow,suite))
        targeted.assert_not_called();flow.compile_test_suite.assert_not_called()
        self.assertEqual(len(flow.evidence['coordinated_repairs']),1)

    def test_anchored_checkpoint_replay_requires_the_actual_verified_candidate_digest(self):
        import json
        from orchestrator.stage_continuation import KEY as CONTINUATION,retained_suite
        from orchestrator.repair_coordinator import CoordinatedPatch
        from orchestrator.wire import wire_schema
        flow,suite=fixture();items=reference_evidence(flow.bundle,suite,receipt(flow,suite))['failures'];value=proposal(items)
        changed=compose_tests(flow,suite,items,test_scope(suite,items),value)
        bundle={**flow.bundle,'build_contract':flow.contract,CONTINUATION:{'independent_suite':suite.model_dump(),'generation':{'value':suite.model_dump()}}}
        record={'proposal_digest':digest(value),'disposition':'promoted','candidate_suite_digest':digest(changed.model_dump())}
        row={'bundle':bundle,'calls':[{'stage':'whole_recovery_8_output'}],
            'result':{'output':{'verification':{'whole_project':{'coordinated_repairs':[record]}}}}}
        data={'base_suite':suite.model_dump(),'base_suite_digest':digest(suite.model_dump()),'failures':items,'scope':test_scope(suite,items)}
        request={'role':'test_author','payload':{'schema':wire_schema(CoordinatedPatch),'messages':[{}, {'content':json.dumps(data)}]}}
        self.assertEqual(retained_suite(row,{'whole_recovery_8_output':request},{'whole_recovery_8_output':{'value':value}}),changed.model_dump())
        record['candidate_suite_digest']='f'*64
        with self.assertRaises(ValueError):retained_suite(row,{'whole_recovery_8_output':request},{'whole_recovery_8_output':{'value':value}})

    def test_large_source_is_supplied_completely_instead_of_being_cut_off_by_the_contract_excerpt(self):
        flow,suite=fixture();value=suite.model_dump();value['files'][0]['content']+='\n# '+('large-source '*2500)+'TAIL_MARKER\n'
        from orchestrator.project_build import WholeProjectTestSuite
        suite=WholeProjectTestSuite.model_validate(value);items=reference_evidence(flow.bundle,suite,receipt(flow,suite))['failures']
        from orchestrator.repair_coordinator import CoordinatedPatch
        raw={'summary':'No model output','clusters':[],'edits':[]}
        data={'repair_policy':'grouped-anchored-recovery-v1','base_suite':suite.model_dump(),
            'base_suite_digest':digest(suite.model_dump()),'scope':['tests/test_math.py'],'failures':items}
        context={'stage':'build_control_recovery','role':'test_author','model_type':CoordinatedPatch,
            'instruction':'Apply exact source anchors','data':data,'value':raw,'value_digest':digest(raw)}
        flow.recovery.consult(context['stage'],ValueError('Reference repair required'),context)
        payload=flow._generate.call_args.args[4]
        self.assertIn('TAIL_MARKER',payload['coordinated_repair']['editable_source'][0]['content'])
        self.assertTrue(payload['original_stage_contract']['inputs']['complete'])
