"""Rejected executable diagnostics and settled malformed patches remain recoverable."""
import json
import unittest
from unittest.mock import Mock

import test_agent_recovery as agent
import test_repair_coordinator as fixtures
from orchestrator.agent_recovery import AgentOutputValidationError
from orchestrator.project_build import sandbox_stage_results
from orchestrator.reference_repair_coordinator import repair
from orchestrator.reference_repair_feedback import ReferencePatchError
from orchestrator.repair_coordinator import prior_attempts
from orchestrator.store import digest


class SettledPatchFeedbackTests(unittest.TestCase):
    def test_settled_malformed_correction_uses_same_original_validator_and_shared_budget(self):
        flow,raw=agent.fixture(raw={'count':0});original=flow._generate.side_effect
        invalid={'count':'not an integer'};outputs=0
        def generate(stage,*args,**kwargs):
            nonlocal outputs
            if stage.endswith('_output'):
                outputs+=1
                if outputs==1:raise AgentOutputValidationError(invalid,ValueError('count must be an integer'))
            return original(stage,*args,**kwargs)
        flow._generate.side_effect=generate
        self.assertEqual(flow.validate_generated(raw,agent.Output.model_validate).count,3)
        self.assertEqual(flow.counters['recovery_output_correction'],2)
        self.assertEqual(flow.counters['recovery_diagnosis'],2)
        rejected=[r for r in flow.recovery.requests if r.get('settled_invalid_output_digest')]
        self.assertEqual(rejected[0]['settled_invalid_output_digest'],digest(invalid))
        self.assertEqual(rejected[0]['status'],'output_correction_rejected')
        self.assertEqual(flow._generate.call_args.args[2],agent.Output)
        self.assertEqual(flow._generation_context['stage'],'whole_self_tests_retry')

    def test_unknown_correction_execution_still_stops_without_a_second_dispatch(self):
        flow,raw=agent.fixture();original=flow._generate.side_effect
        def generate(stage,*args,**kwargs):
            if stage.endswith('_output'):raise OSError('native_completion_unknown')
            return original(stage,*args,**kwargs)
        flow._generate.side_effect=generate
        with self.assertRaisesRegex(OSError,'native_completion_unknown'):
            flow.validate_generated(raw,agent.Output.model_validate)
        self.assertEqual(flow._generate.call_count,2)
        self.assertEqual(flow.counters['recovery_output_correction'],1)

    def test_same_failed_ids_with_new_diagnostics_are_exposed_without_promoting_source(self):
        flow,suite=fixtures.fixture();original=suite.model_dump()
        def control(candidate):
            value=fixtures.receipt(flow,candidate)
            report=sandbox_stage_results(value['commands'][0])
            for failed in report['failures'][0]['test_failures']:
                failed['assertion']='AssertionError: new failure after the first fixture defect was fixed'
            prefix=value['commands'][0]['stderr'].split('OFFLINE_PROJECT_RESULT ')[0]
            value['commands'][0]['stderr']=prefix+'OFFLINE_PROJECT_RESULT '+json.dumps(report)
            return value
        flow.control=Mock(side_effect=control)
        with self.assertRaises(ReferencePatchError):repair(flow,suite,fixtures.receipt(flow,suite))
        record=flow.evidence['coordinated_repairs'][-1]
        details=record['validation_feedback']
        self.assertEqual(details['before_failed'],details['after_failed'])
        self.assertFalse(details['acceptance_credit'])
        self.assertNotEqual(details['proposed_suite_digest'],details['base_suite_digest'])
        projected=prior_attempts([record],flow=flow,suite=suite)[0]
        self.assertIn('new failure after the first fixture defect',projected['post_patch_diagnostics']['observations']['json_excerpt'])
        self.assertEqual(flow.control.call_count,1)
        self.assertEqual(suite.model_dump(),original)
        self.assertFalse(hasattr(flow,'_coordinator_reference'))


if __name__=='__main__':unittest.main()
