from copy import deepcopy
import unittest
from unittest.mock import Mock,patch

from orchestrator.agent_recovery import advice_retry_rejection,advice_type
from orchestrator.repair_allowance import CONTINUATION,POLICY,CARRY_POLICY,carry,output_limit
from orchestrator.reference_repair_feedback import ReferencePatchError
from orchestrator.reference_repair_coordinator import repair
from orchestrator.repair_coordinator import prior_attempts
from orchestrator.stage_recovery import validate_costs
from orchestrator.store import digest
from test_repair_coordinator import fixture,receipt
from test_repair_feedback import renew


class ReferenceRepairFeedbackTests(unittest.TestCase):
    def test_full_reference_regression_survives_rollback_and_reaches_next_advisor(self):
        flow,suite=fixture();renew(flow);flow.counters['recovery_output_correction']=7
        flow.control=Mock(side_effect=lambda candidate:receipt(flow,candidate,failed=('test_math.Checks.test_c',)))
        with patch('orchestrator.reference_repair_coordinator.targeted',return_value=({'failed':[],'tests_run':2},'probe')):
            with self.assertRaises(ReferencePatchError) as error:repair(flow,suite,receipt(flow,suite))
        details=error.exception.feedback
        self.assertEqual(details['regressions'],['test_math.Checks.test_c'])
        self.assertEqual(details['resolved'],['test_math.Checks.test_a','test_math.Checks.test_b'])
        self.assertEqual(details['new_failure_observations'][0]['case'],'test_math.Checks.test_c')
        self.assertFalse(details['acceptance_credit']);self.assertFalse(details['executable_source_applied'])
        record=flow.evidence['coordinated_repairs'][-1]
        self.assertEqual(record['validation_feedback'],details);self.assertEqual(record['disposition'],'rolled_back')
        view=prior_attempts(flow.evidence['coordinated_repairs'],flow=flow,suite=suite)
        self.assertEqual(view[-1]['composed_patch_rejection'],details)

    def test_carried_grant_keeps_original_ceiling_and_every_new_source_cost(self):
        flow,_=fixture();old=renew(flow);base=deepcopy(old['counters']);base['recovery_output_correction']=5
        base['recovery_diagnosis']+=2;value={'policy':CARRY_POLICY,'source_approval_digest':'b'*64,'counters':base,
            'repair_allowance':carry(flow.bundle,'b'*64,base)}
        bundle={**flow.bundle,CONTINUATION:value}
        self.assertEqual(output_limit(bundle),8);validate_costs(bundle,base)
        self.assertEqual(output_limit(bundle)-base['recovery_output_correction'],3)
        for change in [4,9]:
            with self.assertRaises(ValueError):validate_costs(bundle,{**base,'recovery_output_correction':change})
        bad=deepcopy(bundle);bad[CONTINUATION]['repair_allowance']['inherited_limit']=9
        with self.assertRaises(ValueError):output_limit(bad)
        with self.assertRaises(ValueError):carry(flow.bundle,'b'*64,{**base,'recovery_output_correction':8})

    def test_format_retry_projects_only_advice_and_keeps_original_digest(self):
        raw={'action':'correct_stage_output','diagnosis':'d'*5500,'evidence_ref':'F123','required_capability':'c'*800}
        original=deepcopy(raw);result=advice_retry_rejection(advice_type('F123'),raw)
        self.assertEqual(result['digest'],digest(raw));self.assertNotEqual(result['projected_digest'],digest(raw))
        self.assertLessEqual(len(result['json_excerpt'].encode()),2400)
        self.assertEqual(raw,original);self.assertTrue(result['raw_response_host_retained'])

    def test_only_latest_complete_feedback_is_repeated_without_mutating_history(self):
        flow,suite=fixture();base=digest(suite.model_dump())
        records=[{'patch_digest':digest([i]),'base_suite_digest':base,'disposition':'rolled_back','edits':[],
            'validation_feedback':{'large':'x'*10000,'index':i},'rejection':'Old rejection'} for i in range(3)]
        before=deepcopy(records);result=prior_attempts(records,flow=flow,suite=suite)
        self.assertNotIn('composed_patch_rejection',result[0]);self.assertNotIn('patch_excerpt',result[1])
        self.assertEqual(result[-1]['composed_patch_rejection']['index'],2);self.assertEqual(records,before)

    def test_consultation_identity_tracks_real_patch_and_evidence_changes_not_summary(self):
        from test_agent_recovery import fixture as agent_fixture
        flow,_=agent_fixture();context=deepcopy(flow._generation_context)
        context['data'].update(repair_policy='grouped-anchored-recovery-v2',failures=[],base_suite_digest='a'*64)
        context['value']={'summary':'one','edits':[{'path':'tests/a.py','before':'x','after':'y'}]}
        first=flow.recovery.consult(flow.stage,'Same verification rejection',context)
        context['value']['summary']='two'
        self.assertEqual(flow.recovery.consult(flow.stage,'Same verification rejection',context),first)
        self.assertEqual(flow.counters['recovery_diagnosis'],1)
        context['value']['edits'][0]['after']='z'
        second=flow.recovery.consult(flow.stage,'Same verification rejection',context)
        self.assertNotEqual(first['request_id'],second['request_id'])
        self.assertEqual(flow.counters['recovery_diagnosis'],2)
