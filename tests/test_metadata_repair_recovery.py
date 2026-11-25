from copy import deepcopy
import unittest
from unittest.mock import Mock,patch

from orchestrator.repair_coordinator import check_repeat,evidence_catalog,executable_edits
from orchestrator.reference_repair_coordinator import repair,reference_evidence
from orchestrator.checkpoint_repair_continuation import POLICY,retained_counters,budgets
from orchestrator.store import digest
from test_repair_coordinator import fixture,receipt


class MetadataRepairRecoveryTests(unittest.TestCase):
    def context(self):
        flow,suite=fixture();items=reference_evidence(flow.bundle,suite,receipt(flow,suite))['failures']
        refs=list(evidence_catalog(items))
        good={'summary':'Correct shared helper','edits':[{'path':'tests/test_math.py','before':'return 0','after':'return 1',
            'cause':'The failed checks share this helper','evidence_refs':refs}]}
        bad=deepcopy(good);bad['edits'][0]['evidence_refs'].append(refs[0])
        history=[{'patch_digest':digest(executable_edits(bad)),'proposal_digest':digest(bad),
            'base_suite_digest':digest(suite.model_dump()),'edits':bad['edits'],'disposition':'rolled_back',
            'rejection':'An edit repeats an evidence reference'}]
        return flow,suite,items,good,bad,history

    def test_legacy_metadata_only_rejection_allows_valid_correction_with_identical_code(self):
        flow,suite,items,good,bad,history=self.context()
        before=deepcopy(history)
        check_repeat(history,digest(suite.model_dump()),good,items)
        self.assertEqual(history,before)
        self.assertEqual(executable_edits(bad),executable_edits(good))

    def test_prose_changes_do_not_allow_repeated_invalid_metadata(self):
        _,suite,items,_,bad,history=self.context();bad['summary']='Different prose'
        with self.assertRaisesRegex(ValueError,'repeats an evidence'):
            check_repeat(history,digest(suite.model_dump()),bad,items)

    def test_composition_execution_and_unknown_legacy_failures_still_block_identical_code(self):
        _,suite,items,good,_,history=self.context()
        for phase in ['composition','preflight','targeted','full_reference',None]:
            attempt=deepcopy(history[0]);attempt['rejection']='No progress'
            if phase:attempt['validation_phase']=phase
            with self.subTest(phase=phase),self.assertRaisesRegex(ValueError,'already failed'):
                check_repeat([attempt],digest(suite.model_dump()),good,items)

    def test_corrected_metadata_reaches_unchanged_preflight_targeted_and_full_gates(self):
        flow,suite,items,good,_,history=self.context()
        flow.bundle['whole_project_stage_continuation']={'policy':POLICY,'fresh_budgets':budgets()}
        flow.counters['recovery_output_correction']=0;flow.evidence['coordinated_repairs']=history
        def generate(stage,role,model_type,instruction,data,visible,**kw):
            if stage.endswith('_consult'):
                return {'diagnosis':'Correct invalid evidence metadata','evidence_ref':data['failure_evidence_id'],
                    'action':'correct_stage_output','requested_changes':['Remove duplicate references']}
            return deepcopy(good)
        flow._generate=Mock(side_effect=generate)
        flow.control=Mock(side_effect=lambda candidate:receipt(flow,candidate,failed=()))
        with patch('orchestrator.reference_repair_coordinator.targeted',return_value=({'failed':[],'tests_run':2},'probe')) as targeted:
            candidate=repair(flow,suite,receipt(flow,suite))
        self.assertIn('return 1',candidate.files[0].content)
        flow.compile_test_suite.assert_called_once();targeted.assert_called_once();flow.control.assert_called_once()
        self.assertEqual(history[-1]['validation_phase'],'full_reference')
        self.assertEqual(history[-1]['disposition'],'promoted')
        self.assertNotIn('validation_phase',history[0])

    def test_new_epoch_keeps_ordinary_compile_and_general_costs_while_archiving_shared_costs(self):
        prior={'whole_compile_initial_1':2,'whole_compile_initial_2':1,'whole_self_repair':3,
            'build_tests_defect':2,'repair':1,'recovery_diagnosis':6,'recovery_output_correction':4,
            'recovery_request':9,'recovery_issue_abc':2,'coordinated_patch':5}
        kept=retained_counters(prior)
        self.assertEqual(kept,{'whole_compile_initial_1':2,'whole_compile_initial_2':1,
            'whole_self_repair':3,'build_tests_defect':2,'repair':1})
        self.assertEqual(prior['recovery_output_correction'],4)
