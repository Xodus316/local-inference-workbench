from copy import deepcopy
import unittest
from unittest.mock import Mock

from orchestrator.repair_allowance import POLICY,CONTINUATION,grant,output_limit
from orchestrator.stage_recovery import validate_costs
from orchestrator.repair_coordinator import compose_tests,migrate_legacy_proposal,test_scope,prior_attempts
from orchestrator.repair_diagnostics import PatchValidationError
from orchestrator.reference_repair_coordinator import reference_evidence,repair
from orchestrator.store import digest
from test_repair_coordinator import fixture,receipt,proposal


def renew(flow):
    flow.counters['recovery_output_correction']=4;base=dict(flow.counters)
    value={'policy':POLICY,'source_approval_digest':'a'*64,'counters':base,
        'repair_allowance':grant('a'*64,base,4)}
    flow.bundle[CONTINUATION]=value
    return value


class RepairFeedbackTests(unittest.TestCase):
    def test_partial_rename_reports_complete_method_and_dangling_later_branch(self):
        flow,suite=fixture();source=suite.model_dump()
        source['files'][0]['content']=source['files'][0]['content'].replace(
            'self.assertEqual(answer(),1)','tok=answer()\n        self.assertEqual(tok,1)\n        if tok:\n            self.assertEqual(tok,1)',1)
        suite=flow.suite_type.model_validate(source);items=reference_evidence(flow.bundle,suite,receipt(flow,suite))['failures']
        value=proposal(items);value['edits'][0].update(before='tok=answer()\n        self.assertEqual(tok,1)',
            after='reg=answer()\n        self.assertEqual(reg,1)')
        value=migrate_legacy_proposal(value,items)[0]
        with self.assertRaises(PatchValidationError) as caught:compose_tests(flow,suite,items,test_scope(suite,items),value)
        details=caught.exception.feedback;method=details['method_contexts'][0]
        self.assertEqual(details['base_suite_digest'],digest(suite.model_dump()))
        self.assertTrue(details['method_contexts_complete']);self.assertIn('tok',method['removed_bindings_still_used'])
        self.assertIn('if tok:',method['composed_method']);self.assertIn('tok=answer()',method['original_method'])
        self.assertFalse(details['executable_source_applied']);self.assertNotIn('reg=',suite.files[0].content)
        record={'patch_digest':digest(value['edits']),'base_suite_digest':digest(suite.model_dump()),
            'disposition':'rolled_back','edits':value['edits'],'rejection':str(caught.exception)}
        history=deepcopy(record);context=prior_attempts([record],flow=flow,suite=suite)[0]
        self.assertEqual(record,history);self.assertTrue(context['composed_patch_rejection']['host_replay_only'])
        self.assertEqual(context['composed_patch_rejection']['method_contexts'],details['method_contexts'])

    def test_fresh_allowance_adds_four_without_refunding_any_source_cost(self):
        flow,_=fixture();value=renew(flow)
        self.assertEqual(output_limit(flow.bundle),8)
        validate_costs(flow.bundle,dict(flow.counters))
        validate_costs(flow.bundle,{**flow.counters,'recovery_output_correction':8})
        for changes in [{'recovery_output_correction':3},{'build_tests_defect':2},{'recovery_output_correction':9}]:
            with self.assertRaises(ValueError):validate_costs(flow.bundle,{**flow.counters,**changes})
        for change in [True,3,5]:
            bad=deepcopy(flow.bundle);bad[CONTINUATION]['repair_allowance']['additional_output_corrections']=change
            with self.assertRaises(ValueError):output_limit(bad)
        bad=deepcopy(flow.bundle);bad[CONTINUATION]['counters']['recovery_output_correction']=0
        with self.assertRaises(ValueError):output_limit(bad)
        self.assertEqual(value['counters']['recovery_output_correction'],4)

    def test_new_grant_executes_original_role_through_same_validator_and_charges_fifth_cost(self):
        flow,suite=fixture();renew(flow);flow.control=Mock(side_effect=lambda candidate:receipt(flow,candidate,failed=()))
        from unittest.mock import patch
        with patch('orchestrator.reference_repair_coordinator.targeted',return_value=({'failed':[],'tests_run':2},'probe')):
            candidate=repair(flow,suite,receipt(flow,suite))
        self.assertEqual(flow.counters['recovery_output_correction'],5)
        self.assertTrue(all(c.args[1]=='test_author' for c in flow._generate.call_args_list))
        self.assertEqual(flow.counters['build_tests_defect'],3);self.assertIn('return 1',candidate.files[0].content)
        self.assertEqual(flow.evidence['coordinated_repairs'][-1]['disposition'],'promoted')

    def test_semantic_rejection_passes_structured_context_to_consultant_and_correction(self):
        from test_agent_recovery import fixture as agent_fixture,Output
        flow,raw=agent_fixture();feedback={'policy':'composed-test-rejection-v1','diagnostics':[{'code':'undefined_name'}],
            'method_contexts':[{'composed_method':'if later:\n    use(old_name)'}]}
        original=flow._generate.side_effect
        def generate(stage,*args,**kw):
            if stage.endswith('_output'):self.assertEqual(args[3]['composed_patch_rejection'],feedback)
            elif stage.endswith('_consult'):self.assertEqual(args[3]['composed_patch_rejection'],feedback)
            return original(stage,*args,**kw)
        flow._generate.side_effect=generate
        def checked(value):
            if value.get('count')!=3:raise PatchValidationError(feedback)
            return Output.model_validate(value)
        self.assertEqual(flow.validate_generated(raw,checked).count,3)
