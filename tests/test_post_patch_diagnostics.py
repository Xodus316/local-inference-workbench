from copy import deepcopy
import unittest

from orchestrator.reference_repair_feedback import feedback,validate
from orchestrator.reference_repair_coordinator import reference_evidence
from orchestrator.repair_coordinator import prior_attempts
from orchestrator.store import digest
from test_repair_coordinator import fixture,receipt


class PostPatchDiagnosticTests(unittest.TestCase):
    def example(self):
        flow,suite=fixture();before=reference_evidence(flow.bundle,suite,receipt(flow,suite))
        after=deepcopy(before);after['receipt_digest']='e'*64
        for item in after['failures']:item['diagnostic']='AssertionError: expected fixture missing from the current page'
        details=feedback(before,after,suite,suite)
        record={'patch_digest':digest([]),'proposal_digest':digest({}),'disposition':'rolled_back',
            'base_suite_digest':digest(suite.model_dump()),'edits':[],'validation_feedback':deepcopy(details)}
        binding={'request_digest':'a'*64,'raw_digest':'b'*64}
        item={'patch_digest':record['patch_digest'],'proposal_digest':record['proposal_digest'],
            'model_stage':'whole_recovery_1_output',**binding,'reference_stage':'build_control_2',
            'reference_raw_sha256':'c'*64,'feedback':details}
        value={'coordinated_history':[record],'source_receipts':{item['model_stage']:binding},'retained_reference':before}
        return flow,suite,before,after,record,item,value

    def test_changed_diagnostics_are_retained_when_the_same_cases_still_fail(self):
        _,suite,before,after,_,item,value=self.example();details=item['feedback']
        self.assertEqual(details['before_failed'],details['after_failed'])
        self.assertEqual(details['new_failure_observations'],[])
        self.assertEqual(details['post_patch_failure_observations'],after['failures'])
        self.assertNotEqual(after['failures'],before['failures'])
        self.assertFalse(details['acceptance_credit']);self.assertFalse(details['executable_source_applied'])
        validate([item],value,suite)

    def test_legacy_feedback_is_replayable_and_changed_post_patch_evidence_is_rejected(self):
        _,suite,_,_,_,item,value=self.example()
        legacy=deepcopy(item);legacy['feedback']['policy']='reference-progress-rejection-v1'
        legacy['feedback'].pop('post_patch_failure_observations');legacy['feedback'].pop('post_patch_failure_observations_digest')
        validate([legacy],value,suite)
        for key,new in [('case','invented.case'),('diagnostic','Invented different diagnosis')]:
            bad=deepcopy(item);bad['feedback']['post_patch_failure_observations'][0][key]=new
            with self.subTest(key=key),self.assertRaises(ValueError):validate([bad],value,suite)

    def test_verified_carried_diagnostics_override_old_lossy_projection_without_rewriting_it(self):
        flow,suite,_,_,record,item,_=self.example()
        record['validation_feedback']={'policy':'reference-progress-rejection-v1','new_failure_observations':[]}
        original=deepcopy(record)
        flow.evidence['carried_verification_rejections']=[item]
        view=prior_attempts([record],flow=flow,suite=suite)
        self.assertEqual(view[0]['composed_patch_rejection'],item['feedback'])
        self.assertEqual(record,original)

    def test_all_recent_post_patch_diagnostics_remain_visible_with_bounded_previews(self):
        flow,suite,_,_,record,item,_=self.example()
        records=[];carried=[]
        for index in range(4):
            r=deepcopy(record);r['patch_digest']=digest([index]);records.append(r)
            f=deepcopy(item);f['patch_digest']=r['patch_digest'];carried.append(f)
        before=deepcopy(records);flow.evidence['carried_verification_rejections']=carried
        view=prior_attempts(records,flow=flow,suite=suite)
        for entry in view:
            proof=entry['post_patch_diagnostics']
            self.assertIn('AssertionError',proof['observations']['json_excerpt'])
            self.assertLessEqual(len(proof['observations']['json_excerpt'].encode()),1200)
            self.assertFalse(proof['acceptance_credit'])
        self.assertNotIn('composed_patch_rejection',view[0])
        self.assertEqual(records,before)
