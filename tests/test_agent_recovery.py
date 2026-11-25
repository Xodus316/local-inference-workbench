"""Unfamiliar failures reach an agent; only host capabilities can act on advice."""
from collections import Counter
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from pydantic import Field

from orchestrator.agent_recovery import INLINE_RECOVERY_POLICY,RecoveryAdvice,AgentOutputValidationError,excerpt
from orchestrator.schemas import Record
from orchestrator.stage_recovery import (KEY,POLICY,DYNAMIC_POLICY,StageRecovery,dynamic_enabled,
    enabled,validate_costs)
from orchestrator.store import STAGES,digest
from orchestrator.whole_project import WholeProjectBuildRun


class Output(Record):
    count:int=Field(ge=1)


def fixture(stage='whole_self_tests_retry',role='implementer',raw=None,corrected=None):
    flow=object.__new__(WholeProjectBuildRun)
    flow.bundle={KEY:deepcopy(DYNAMIC_POLICY),'whole_project_repair_policy':'verified-scoped-repair-v1'}
    flow.stage,flow.state=stage,{'run_id':'fixture'}
    flow.output,flow.evidence,flow.counters,flow.stages={}, {},Counter(),{}
    store=Mock();store.has_unknown_calls.return_value=False;store.unsettled_operations.return_value=[]
    flow.runtime=SimpleNamespace(store=store,_guard=Mock())
    flow.recovery=StageRecovery(flow)
    value={'count':'invalid'} if raw is None else raw
    flow._generation_context={'stage':stage,'role':role,'model_type':Output,'instruction':'Return a count',
        'data':{'original_contract':'Keep scope'},'value':deepcopy(value),'value_digest':digest(value)}
    def generate(stage,role,model_type,instruction,data,visible,**options):
        assert options['tool_policy']==INLINE_RECOVERY_POLICY and visible=={}
        if stage.endswith('_consult'):
            return {'diagnosis':'The output violates its retained validator','evidence_quote':data['failure'][:1000],
                'action':'correct_stage_output' if data['available_capabilities'] else 'request_capability',
                'requested_changes':['Correct only the count field'],
                'required_capability':'' if data['available_capabilities'] else 'A bounded adapter for the failed host operation'}
        return {'count':3} if corrected is None else corrected
    flow._generate=Mock(side_effect=generate)
    return flow,value


class AgentRecoveryTests(unittest.TestCase):
    def test_semantically_incomplete_advice_gets_the_same_single_validation_feedback_retry(self):
        flow,raw=fixture()
        original=flow._generate.side_effect;seen=[]
        def generate(stage,*args,**kw):
            if stage=='whole_recovery_1_consult':
                data=args[3]
                return {'diagnosis':'The failure needs an output correction','evidence_quote':data['failure'],
                    'action':'correct_stage_output','requested_changes':[]}
            if stage=='whole_recovery_1_retry_consult':
                seen.append(args[3]['previous_decision_rejection'])
            return original(stage,*args,**kw)
        flow._generate.side_effect=generate
        self.assertEqual(flow.validate_generated(raw,Output.model_validate).count,3)
        self.assertIn('nonempty requested_changes',seen[0]['failure'])
        self.assertEqual([c.args[0] for c in flow._generate.call_args_list],
            ['whole_recovery_1_consult','whole_recovery_1_retry_consult','whole_recovery_1_output'])
        self.assertEqual(flow.counters['recovery_diagnosis'],1)
        self.assertEqual(flow.counters['recovery_output_correction'],1)

    def test_syntax_consultation_includes_exact_parser_diagnostics(self):
        from orchestrator.build_coverage import TestSyntaxError
        flow,_=fixture('build_tests_defect_recovery','test_author')
        diagnostics=[{'path':'tests/test_ui.py','line':91,'message':'unterminated string literal'}]
        flow.recovery.consult(flow.stage,TestSyntaxError(diagnostics),flow._generation_context)
        self.assertEqual(flow._generate.call_args.args[4]['parser_diagnostics'],diagnostics)

    def test_confirmed_pre_dispatch_failure_can_consult_for_original_output_without_retrying_the_bad_stage(self):
        for proved in (True,False):
            flow,_=fixture();flow.bundle['generation']={r:{'context':98304} for r in ['implementer','repairer']}
            flow.model_inputs=lambda data:data
            error=ValueError('Unsupported future adapter stage')
            if proved:error.not_dispatched=True
            def dispatch(stage,role,operation,visible,**kw):
                flow.stages[stage]={}
                if stage=='whole_self_tests_retry':raise error
                if stage.endswith('_consult'):
                    return {'value':{'diagnosis':'No original inference was dispatched; return the approved output through recovery',
                        'evidence_quote':str(error),'action':'correct_stage_output',
                        'requested_changes':['Produce the original schema-bound output']}}
                return {'value':{'count':3}}
            flow._opencode=Mock(side_effect=dispatch)
            flow._generate=lambda *a,**kw:WholeProjectBuildRun._generate(flow,*a,**kw)
            if proved:
                self.assertEqual(flow._generate(flow.stage,'implementer',Output,'Return count',{},{}),{'count':3})
                self.assertEqual(flow._opencode.call_count,3)
                self.assertEqual(flow.counters['recovery_output_correction'],1)
            else:
                with self.assertRaises(ValueError):flow._generate(flow.stage,'implementer',Output,'Return count',{}, {})
                self.assertEqual(flow._opencode.call_count,1)

    def test_settled_worker_loop_consults_and_corrects_under_original_owner_without_redispatch(self):
        from orchestrator.worker_progress import OpenCodeProgressError
        for cleanup,unknown in [(True,False),(False,False),(True,True)]:
            flow,_=fixture();flow.bundle['generation']={r:{'context':98304} for r in ['implementer','repairer']}
            flow.model_inputs=lambda data:data
            error=OpenCodeProgressError({'tool':'glob','identical_results':8,'source_progress':False})
            error.backend_cleanup_confirmed=cleanup
            flow.runtime.store.has_unknown_calls.return_value=unknown
            def dispatch(stage,role,operation,visible,**kw):
                flow.stages[stage]={}
                if stage=='whole_self_tests_retry':raise error
                if stage.endswith('_consult'):
                    return {'value':{'diagnosis':'A settled worker repeated exploration instead of returning output',
                        'evidence_quote':str(error),'action':'correct_stage_output',
                        'requested_changes':['Return the original response directly from supplied evidence']}}
                return {'value':{'count':3}}
            flow._opencode=Mock(side_effect=dispatch)
            flow._generate=lambda *a,**kw:WholeProjectBuildRun._generate(flow,*a,**kw)
            if cleanup and not unknown:
                self.assertEqual(flow._generate(flow.stage,'implementer',Output,'Return a count',{},{}),{'count':3})
                self.assertEqual(flow._opencode.call_count,3)
                self.assertEqual(flow._opencode.call_args.args[:2],('whole_recovery_1_output','implementer'))
                self.assertEqual(flow.counters['recovery_output_correction'],1)
            else:
                with self.assertRaises(OpenCodeProgressError):flow._generate(flow.stage,'implementer',Output,'Return a count',{}, {})
                self.assertEqual(flow._opencode.call_count,1)

    def test_actual_workflow_exception_and_review_stop_boundaries_consult_before_return(self):
        flow,_=fixture('future_stage_not_in_any_handler')
        flow._execute=Mock(side_effect=KeyError('unfamiliar exception Z916'))
        with self.assertRaises(KeyError):flow.execute()
        flow._generate.assert_called_once()
        self.assertEqual(flow._generate.call_args.args[4]['error_type'],'KeyError')
        flow,_=fixture('whole_profile_0_1');flow._execute=Mock(return_value='review_required')
        flow.output['error']={'stage':flow.stage,'message':'Unrecognized gate failure Z917'}
        self.assertEqual(flow.execute(),'review_required')
        flow._generate.assert_called_once()

    def test_each_unhandled_phase_consults_an_agent_including_unrecognized_stages(self):
        for stage in ['build_plan_retry','implement','build_compile_checkpoint_1_0','whole_self_tests_retry',
                'whole_self_reference_repair_1','whole_self_verify_0','build_tests_retry','build_tests_defect_1_file_1',
                'build_verify_0','whole_regression_verify_1','whole_profile_0_1','build_code_review_0_chunk_1',
                'whole_self_verify_final_1','delivery_export','future_stage_not_in_any_handler']:
            flow,_=fixture(stage)
            flow.output['private_reference']='PRIVATE_REFERENCE_SOURCE_AND_LOGS'
            request=flow.recovery.terminal(stage,'Unfamiliar exception code Z914')
            with self.subTest(stage=stage):
                flow._generate.assert_called_once()
                call=flow._generate.call_args
                self.assertEqual(call.args[0],'whole_recovery_1_consult')
                self.assertIs(call.args[2],RecoveryAdvice)
                self.assertNotIn('PRIVATE_REFERENCE',str(call.args[4]))
                self.assertEqual(request['agent_consultation']['advice']['action'],'request_capability')
                self.assertIn(call.args[0],STAGES)
                self.assertEqual(flow.stage,stage)

    def test_schema_and_arbitrary_semantic_errors_use_original_schema_owner_and_validator(self):
        for role,stage in [('planner','build_plan_retry'),('implementer','whole_self_tests_retry'),
                           ('test_author','build_tests_retry'),('repairer','repair_1_select')]:
            flow,raw=fixture(stage,role)
            result=flow.validate_generated(raw,Output.model_validate)
            self.assertEqual(result.count,3)
            self.assertEqual(flow._generate.call_count,2)
            correction=flow._generate.call_args
            self.assertEqual(correction.args[:3],('whole_recovery_1_output',role,Output))
            self.assertEqual(correction.args[4]['original_contract'],'Keep scope')
            self.assertEqual(flow.counters['recovery_diagnosis'],1)
            self.assertEqual(flow.counters['recovery_output_correction'],1)
            self.assertEqual(flow.recovery.requests[-1]['status'],'output_revalidated')
            self.assertFalse(flow.recovery.requests[-1]['acceptance_credit'])
            if role=='test_author':self.assertEqual(flow._generate.call_args_list[0].args[1],'test_author')
        flow,raw=fixture(raw={'count':1})
        def semantic(value):
            if value['count']<3:raise KeyError('Unexpected dynamic constraint Z915')
            return value
        self.assertEqual(flow.validate_generated(raw,semantic),{'count':3})

    def test_same_validator_rejects_bad_agent_correction_without_an_infinite_retry(self):
        flow,raw=fixture(corrected={'count':0})
        with self.assertRaises(ValueError):flow.validate_generated(raw,Output.model_validate)
        self.assertEqual(flow._generate.call_count,4)
        self.assertEqual(flow.recovery.requests[-1]['status'],'output_correction_rejected')
        self.assertEqual(flow.counters['recovery_output_correction'],2)

    def test_distinct_rejections_can_use_remaining_shared_budget_but_never_exceed_it(self):
        for shared,success in [(1,True),(3,False)]:
            flow,raw=fixture(raw={'count':0});flow.counters['recovery_output_correction']=shared
            corrected=iter([{'count':1},{'count':2}])
            original=flow._generate.side_effect
            def generate(stage,*a,**kw):
                if stage.endswith('_consult'):return original(stage,*a,**kw)
                return next(corrected)
            flow._generate.side_effect=generate
            def validator(value):
                if value['count']!=2:raise ValueError('Remaining defect at value '+str(value['count']))
                return value
            if success:self.assertEqual(flow.validate_generated(raw,validator),{'count':2})
            else:
                with self.assertRaises(ValueError):flow.validate_generated(raw,validator)
            self.assertLessEqual(flow.counters['recovery_output_correction'],4)
            self.assertEqual(sum(c.args[0].endswith('_output') for c in flow._generate.call_args_list),2 if success else 1)

    def test_consultant_receives_bounded_original_contract_and_rejected_output(self):
        flow,raw=fixture()
        flow._generation_context['data']['previous_suite']={'files':[{'content':'retained source'}],
            'coverage':['Retained.test_identity']}
        flow.output['private_reference']='PRIVATE_REFERENCE_SOURCE_AND_LOGS'
        flow.validate_generated(raw,Output.model_validate)
        data=flow._generate.call_args_list[0].args[4]
        contract=data['original_stage_contract']
        self.assertEqual((contract['stage'],contract['role']),(flow.stage,'implementer'))
        self.assertIn('Return a count',contract['instruction']['json_excerpt'])
        self.assertIn('Retained.test_identity',contract['inputs']['json_excerpt'])
        self.assertEqual(data['rejected_output']['digest'],digest(raw))
        self.assertNotIn('PRIVATE_REFERENCE',str(data))
        flow,raw=fixture()
        flow._generation_context['data']={'large':'x'*50000}
        flow.validate_generated(raw,Output.model_validate)
        projection=flow._generate.call_args_list[0].args[4]['original_stage_contract']['inputs']
        self.assertFalse(projection['complete'])
        self.assertLessEqual(len(projection['json_excerpt'].encode('utf8')),24000)

    def test_unknown_execution_and_unsettled_sandbox_block_new_agent_dispatch(self):
        for change in ['unknown_native','sandbox','message']:
            flow,_=fixture()
            if change=='unknown_native':flow.runtime.store.has_unknown_calls.return_value=True
            if change=='sandbox':flow.runtime.store.unsettled_operations.return_value=['owned sandbox']
            message='native_completion_unknown' if change=='message' else 'Unexpected error'
            request=flow.recovery.terminal(flow.stage,message)
            self.assertEqual(request['action'],'resolve_execution')
            flow._generate.assert_not_called()
            self.assertEqual(flow.counters['recovery_diagnosis'],0)

    def test_budget_cancellation_and_duplicate_terminal_failures_do_not_redispatch(self):
        flow,_=fixture();flow.counters['recovery_diagnosis']=16
        flow.recovery.terminal(flow.stage,'failure');flow._generate.assert_not_called()
        flow,_=fixture();flow.runtime._guard.side_effect=TimeoutError('Deadline expired')
        flow.recovery.terminal(flow.stage,'failure');flow._generate.assert_not_called()
        flow,_=fixture();flow.recovery.terminal(flow.stage,'failure');flow.recovery.terminal(flow.stage,'failure')
        flow._generate.assert_called_once()

    def test_consultation_uses_the_shared_single_settled_output_limit_restart(self):
        from orchestrator.project_opencode_whole import OpenCodeSettledResponseError
        for stage,role in [('whole_self_tests_retry','repairer'),('build_tests_retry','test_author')]:
            flow,_=fixture(stage)
            error=OpenCodeSettledResponseError('structured-output-limit',{'completed_requests':1,
                'scratch_cleanup_confirmed':True,'native_receipts_digest':'a'*64,'session_id':'settled'})
            flow._generate.side_effect=[error,{'diagnosis':'A host adapter is needed','evidence_quote':'failure',
                'action':'request_capability','required_capability':'A bounded log-reader capability'}]
            result=flow.recovery.terminal(stage,'failure')
            self.assertEqual(flow._generate.call_count,2)
            self.assertEqual(flow._generate.call_args.args[:2],('whole_recovery_1_retry_consult',role))
            self.assertEqual(result['agent_consultation']['index'],1)
            self.assertEqual(flow.counters['recovery_diagnosis'],1)

    def test_malformed_advice_uses_same_single_retry_and_cannot_chain_with_output_limit_retry(self):
        from orchestrator.project_opencode_whole import OpenCodeSettledResponseError
        malformed={'diagnosis':'Retained output is invalid','evidence_quote':'failure',
            'action':'correct_stage_output','required_capability':'x'*601}
        try:RecoveryAdvice.model_validate(malformed)
        except ValueError as error:bad=AgentOutputValidationError(malformed,error)
        limit=OpenCodeSettledResponseError('structured-output-limit',{'completed_requests':1,
            'scratch_cleanup_confirmed':True,'native_receipts_digest':'a'*64,'session_id':'settled'})
        good={'diagnosis':'The host needs an adapter','evidence_quote':'failure','action':'request_capability',
            'required_capability':'A bounded host adapter'}
        for first,second in [(bad,good),(bad,bad),(bad,limit),(limit,bad)]:
            flow,_=fixture();flow._generate.side_effect=[first,second]
            result=flow.recovery.terminal(flow.stage,'failure')
            self.assertEqual(flow._generate.call_count,2)
            self.assertEqual(flow._generate.call_args.args[0],'whole_recovery_1_retry_consult')
            self.assertEqual(flow.counters['recovery_diagnosis'],1)
            if second is good:
                self.assertIn('agent_consultation',result)
                feedback=flow._generate.call_args.args[4]['previous_decision_rejection']
                self.assertIn('600',feedback['failure'])
                self.assertEqual(feedback['rejected_output']['digest'],digest(malformed))
            else:self.assertEqual(flow.recovery.requests[-2]['action'],'agent_consultation_failed')

    def test_real_generation_retains_settled_invalid_agent_output_for_bounded_feedback(self):
        flow,_=fixture()
        flow.bundle['generation']={'repairer':{'context':98304}}
        flow.model_inputs=lambda data:data
        flow.recovery._consulting=True
        value={'diagnosis':'d','evidence_quote':'failure','action':'stop','required_capability':'x'*601}
        def dispatch(stage,*a,**kw):
            flow.stages[stage]={}
            return {'value':value}
        flow._opencode=Mock(side_effect=dispatch)
        with self.assertRaises(AgentOutputValidationError) as raised:
            WholeProjectBuildRun._generate(flow,'whole_recovery_1_consult','repairer',RecoveryAdvice,
                'Diagnose',{}, {},tool_policy=INLINE_RECOVERY_POLICY)
        self.assertEqual(raised.exception.value,value)
        self.assertEqual(flow._opencode.call_count,1)

    def test_advice_cannot_invent_evidence_or_unsupported_actions(self):
        for advice in [
                {'diagnosis':'invented','evidence_quote':'never reported','action':'stop'},
                {'diagnosis':'bad','evidence_quote':'failure','action':'execute_shell','command':'invented'},
                {'diagnosis':'bad','evidence_quote':'failure','action':'correct_stage_output','requested_changes':['rewrite']},
                {'diagnosis':'bad','evidence_quote':'failure','action':'request_capability'}]:
            flow,_=fixture();flow._generate.return_value=advice;flow._generate.side_effect=None
            result=flow.recovery.consult(flow.stage,'failure')
            self.assertIsNone(result)
            self.assertEqual(flow.recovery.requests[-1]['action'],'agent_consultation_failed')

    def test_legacy_approval_has_no_consultation_and_new_costs_are_bound(self):
        flow,raw=fixture();flow.bundle[KEY]=deepcopy(POLICY)
        self.assertTrue(enabled(flow.bundle));self.assertFalse(dynamic_enabled(flow.bundle))
        flow.recovery.terminal(flow.stage,'failure');flow._generate.assert_not_called()
        with self.assertRaises(ValueError):validate_costs(flow.bundle,{'recovery_output_correction':1})
        flow.bundle[KEY]=deepcopy(DYNAMIC_POLICY)
        validate_costs(flow.bundle,{'recovery_output_correction':4})
        with self.assertRaises(ValueError):validate_costs(flow.bundle,{'recovery_output_correction':5})
        flow.bundle[KEY]['max_output_corrections']=True
        with self.assertRaises(ValueError):enabled(flow.bundle)

    def test_unmatched_output_cannot_borrow_another_generation_context(self):
        flow,_=fixture()
        with self.assertRaises(ValueError):flow.validate_generated({'count':0},Output.model_validate)
        flow._generate.assert_not_called()
        projection=excerpt({'large':'x'*100},12)
        self.assertFalse(projection['complete']);self.assertGreater(projection['omitted_utf8_bytes'],0)

    def test_correction_dispatch_requires_the_captured_role_and_schema_authority(self):
        for authority in [None,{'stage':'whole_recovery_1_output','role':'repairer','schema_digest':'wrong'}]:
            flow,_=fixture();flow._opencode=Mock();flow._agent_output_authority=authority
            # Invoke the real generation entry point, not this fixture's agent.
            with self.assertRaisesRegex(ValueError,'original schema'):
                WholeProjectBuildRun._generate(flow,'whole_recovery_1_output','implementer',Output,'task',{}, {},
                    tool_policy=INLINE_RECOVERY_POLICY)
            flow._opencode.assert_not_called()


if __name__=='__main__':unittest.main()
