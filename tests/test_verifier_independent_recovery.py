"""The repair capability uses public verifier receipts, never a private image module."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

import test_repair_coordinator as fixtures
import test_stage_continuation as stages
from orchestrator.agent_recovery import validate_output
from orchestrator.reference_repair_coordinator import targeted,repair,reference_evidence
from orchestrator.repair_coordinator import patch_type
from orchestrator.store import digest
from orchestrator.whole_project_gates import VerificationUnavailable
from orchestrator.stage_continuation import retained_suite,KEY
from orchestrator.wire import wire_schema


class VerifierIndependentRecoveryTests(unittest.TestCase):
    def test_diagnostic_runs_complete_approved_gate_and_rejects_missing_evidence(self):
        flow,suite=fixtures.fixture();receipt=fixtures.receipt(flow,suite)
        flow.control=Mock(return_value=receipt);original=flow.stage
        result,_=targeted(flow,suite,['test_math.Checks.test_a'],1)
        self.assertEqual(result['tests_run'],3)
        flow.control.assert_called_once_with(suite)
        self.assertEqual(flow.stage,original)
        broken=deepcopy(receipt);broken['commands'][0]['stderr']='ModuleNotFoundError: verifier unavailable\n'
        flow.control.return_value=broken
        with self.assertRaises(VerificationUnavailable):targeted(flow,suite,['test_math.Checks.test_a'],2)
        flow._generate.assert_not_called()

    def test_verifier_failure_cannot_be_rewritten_as_a_worker_output_correction(self):
        flow,suite=fixtures.fixture();value={'summary':'original','edits':[]}
        context={'stage':'build_tests_defect_1','value_digest':digest(value),'value':value,
            'data':{},'role':'test_author','model_type':dict,'instruction':'Keep original gates'}
        def unavailable(_):raise VerificationUnavailable('Reference repair requires conclusive pinned verifier checks')
        costs=dict(flow.counters)
        with self.assertRaises(VerificationUnavailable):validate_output(flow.recovery,value,unavailable,context)
        flow._generate.assert_not_called()
        self.assertEqual(dict(flow.counters),costs)

    def test_unverified_old_patch_requires_fresh_full_verification_before_promotion(self):
        flow,suite=fixtures.fixture();items=reference_evidence(flow.bundle,suite,fixtures.receipt(flow,suite))['failures']
        proposal=fixtures.proposal(items)
        flow.evidence['coordinated_repairs']=[{'patch_digest':digest(proposal['edits']),
            'base_suite_digest':digest(suite.model_dump()),'edits':proposal['edits'],
            'disposition':'rolled_back','validation_phase':'targeted',
            'rejection':'Reference repair requires conclusive pinned verifier checks'}]
        revised=suite.model_copy(deep=True);revised.files[0].content=revised.files[0].content.replace('return 0','return 1')
        flow.control=Mock(return_value=fixtures.receipt(flow,revised,failed=()))
        self.assertEqual(repair(flow,suite,fixtures.receipt(flow,suite)),revised)
        self.assertEqual(flow.control.call_count,2)  # Full diagnostic, then independent full promotion gate.
        self.assertEqual(flow.evidence['coordinated_repairs'][-1]['disposition'],'promoted')
        self.assertEqual(flow.evidence['coordinated_repairs'][0]['disposition'],'rolled_back')

    def test_smoke_patch_is_not_replayed_onto_a_later_fresh_independent_draft(self):
        row,requests,raw,_=stages.fixture();flow,smoke=fixtures.fixture()
        row['bundle'][KEY]={'self_suite':smoke.model_dump()}
        # The independent author starts after a valid, promoted smoke patch.
        items=reference_evidence(flow.bundle,smoke,fixtures.receipt(flow,smoke))['failures']
        proposal=fixtures.proposal(items);name='build_tests_defect_1'
        from orchestrator.repair_coordinator import migrate_legacy_proposal
        proposal=migrate_legacy_proposal(proposal,items)[0]
        requests[name]={'operation':'generate','role':'test_author','payload':{
            'schema':wire_schema(patch_type(items)),'messages':[{'role':'system','content':'Smoke patch'},
                {'role':'user','content':json.dumps({'failures':items,'base_suite':smoke.model_dump(),
                    'base_suite_digest':digest(smoke.model_dump()),'scope':['tests/test_math.py']})}]}}
        raw[name]={'value':proposal}
        row['calls'].insert(0,{'stage':name,'status':'terminal'})
        row['result']['output']['verification']['whole_project']['coordinated_repairs']=[{
            'proposal_digest':digest(proposal),'disposition':'promoted','candidate_suite_digest':'a'*64}]
        expected=deepcopy(raw['build_tests']['value'])
        expected['implementation_files']=['app.py'];raw['build_tests']['value']=expected
        self.assertEqual(retained_suite(row,requests,raw)['files'],expected['files'])


if __name__=='__main__':unittest.main()
