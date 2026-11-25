"""Metadata recovery must never alter source permissions, edits or spent inference."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock,patch

from orchestrator.agent_recovery import advice_type
from orchestrator.repair_coordinator import (patch_type,evidence_catalog,migrate_legacy_proposal,
    compose_tests,test_scope,metadata_projection)
from orchestrator.reference_repair_coordinator import reference_evidence,repair
from orchestrator.retained_repair_proposal import pack,validate
from orchestrator.stage_continuation import KEY,PENDING_POLICY,retained_suite
from orchestrator.store import digest
from orchestrator.wire import wire_schema
from orchestrator.repair_coordinator import CoordinatedPatch
from test_repair_coordinator import fixture,receipt,proposal


def retained_fixture():
    flow,suite=fixture();reference=reference_evidence(flow.bundle,suite,receipt(flow,suite))
    items=reference['failures'];raw=proposal(items);raw['summary']='s'*1235
    raw['clusters'][0]['evidence_quote']='Invented diagnostic suffix'
    stage='whole_recovery_9_output'
    data={'base_suite':suite.model_dump(),'base_suite_digest':digest(suite.model_dump()),
        'scope':test_scope(suite,items),'failures':items}
    request={'role':'test_author','operation':'generate','payload':{'schema':wire_schema(CoordinatedPatch),
        'messages':[{}, {'content':json.dumps(data)}]}}
    row={'calls':[{'stage':stage,'status':'terminal'}],'result':{'output':{'recovery':{'requests':[
        {'agent_consultation':{'index':9},'action':'correct_stage_output','status':'output_correction_rejected'}]}}}}
    pending=pack(row,{stage:request},{stage:{'value':raw}},suite,reference)
    return flow,suite,reference,pending


class EvidenceRecoveryTests(unittest.TestCase):
    def test_metadata_projection_preserves_edits_and_binds_real_evidence(self):
        flow,suite,ref,pending=retained_fixture();value=pending['proposal'];original=pending['raw_value']
        self.assertEqual(value['edits'],original['edits']);self.assertEqual(len(value['summary']),1200)
        self.assertFalse(pending['migration']['legacy_quotes_advisory'][0]['literal_quote_matched'])
        self.assertNotIn('evidence_quote',value['clusters'][0])
        self.assertEqual(set(value['clusters'][0]['evidence_refs']),set(evidence_catalog(ref['failures'])))
        with self.assertRaisesRegex(ValueError,'quote'):compose_tests(flow,suite,ref['failures'],pending['scope'],
            {**original,'summary':'Short'})
        candidate=compose_tests(flow,suite,ref['failures'],pending['scope'],value)
        self.assertIn('return 1',candidate.files[0].content)
        validate(pending,suite,ref)

    def test_dynamic_schema_rejects_invented_references_and_never_shortens_code(self):
        _,_,ref,pending=retained_fixture();value=deepcopy(pending['proposal']);value['summary']='x'*2000
        value['clusters'][0]['cause']='c'*2000
        parsed=patch_type(ref['failures']).model_validate(value).model_dump()
        proof=metadata_projection(value,parsed)
        self.assertEqual([r['path'] for r in proof['fields']],['summary','clusters.0.cause'])
        self.assertEqual(value['edits'],parsed['edits'])
        invalid=deepcopy(value);invalid['clusters'][0]['evidence_refs']=['Einvented']
        with self.assertRaises(ValueError):patch_type(ref['failures']).model_validate(invalid)
        invalid=deepcopy(value);invalid['edits'][0]['after']='c'*13000
        with self.assertRaises(ValueError):patch_type(ref['failures']).model_validate(invalid)
        changed=deepcopy(parsed);changed['edits'][0]['after']='return 2'
        with self.assertRaises(ValueError):metadata_projection(value,changed)

    def test_pending_validation_cannot_change_role_edits_source_or_evidence(self):
        _,suite,ref,pending=retained_fixture()
        for mutation in ['role','edits','source','evidence','redispatch','migration']:
            bad=deepcopy(pending)
            if mutation=='role':bad['role']='repairer'
            if mutation=='edits':bad['proposal']['edits'][0]['after']='return 2'
            if mutation=='source':bad['base_suite_digest']='f'*64
            if mutation=='evidence':bad['evidence'][0]['diagnostic']='different'
            if mutation=='redispatch':bad['model_redispatch_allowed']=True
            if mutation=='migration':bad['migration']['legacy_quotes_advisory'][0]['literal_quote_matched']=True
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate(bad,suite,ref)

    def test_completed_proposal_runs_normal_gates_with_no_model_or_budget_refund(self):
        flow,suite,ref,pending=retained_fixture()
        flow.bundle[KEY]={'policy':PENDING_POLICY,'pending_proposal':deepcopy(pending)}
        flow._pending_repair=deepcopy(pending);flow.counters['recovery_output_correction']=4
        flow.control=Mock(side_effect=lambda candidate:receipt(flow,candidate,failed=()))
        with patch('orchestrator.reference_repair_coordinator.targeted',return_value=({'failed':[],'tests_run':2},'probe')):
            candidate=repair(flow,suite,receipt(flow,suite))
        flow._generate.assert_not_called();flow.compile_test_suite.assert_called_once()
        flow.control.assert_called_once();self.assertEqual(flow.counters['recovery_output_correction'],4)
        self.assertEqual(flow.counters['build_tests_defect'],3);self.assertEqual(flow.counters['coordinated_patch'],1)
        record=flow.evidence['coordinated_repairs'][0];self.assertEqual(record['disposition'],'promoted')
        row={'bundle':{**flow.bundle,'build_contract':flow.contract},'calls':[],
            'result':{'output':{'verification':{'whole_project':{'coordinated_repairs':[record]}}}}}
        row['bundle'][KEY].update(independent_suite=suite.model_dump(),generation={'value':suite.model_dump()})
        self.assertEqual(retained_suite(row,{},{}),candidate.model_dump())

    def test_pending_semantic_failure_rolls_back_without_new_inference(self):
        flow,suite,ref,pending=retained_fixture();pending['proposal']['edits'][0]['after']='return missing()'
        # Deliberately corrupted approved fixture exercises normal static gates; real approvals also validate migration.
        flow.bundle[KEY]={'policy':PENDING_POLICY,'pending_proposal':deepcopy(pending)}
        flow._pending_repair=deepcopy(pending);flow.counters['recovery_output_correction']=4
        with patch('orchestrator.reference_repair_coordinator.targeted') as targeted:
            with self.assertRaisesRegex(ValueError,'static defects'):repair(flow,suite,receipt(flow,suite))
        targeted.assert_not_called();flow._generate.assert_not_called();flow.compile_test_suite.assert_not_called()
        self.assertEqual(flow.evidence['coordinated_repairs'][0]['disposition'],'rolled_back')
        self.assertEqual(flow.counters['recovery_output_correction'],4);self.assertNotIn('missing()',suite.files[0].content)

    def test_replay_reconstructs_only_host_retained_coverage_metadata(self):
        flow,suite,ref,pending=retained_fixture();value=pending['proposal'];items=pending['evidence']
        candidate=compose_tests(flow,suite,items,pending['scope'],value)
        base=suite.model_dump();coverage=base['coverage'];base['coverage']={
            'host_retained':True,'digest':digest(coverage),'entries':len(coverage),'editable':False}
        data={'base_suite':base,'base_suite_digest':digest(suite.model_dump()),'failures':items,'scope':pending['scope'],
            'base_suite_projection':{'original_digest':digest(suite.model_dump())}}
        stage='whole_recovery_10_output';record={'proposal_digest':digest(value),'disposition':'promoted',
            'candidate_suite_digest':digest(candidate.model_dump())}
        row={'bundle':{**flow.bundle,'build_contract':flow.contract,KEY:{'independent_suite':suite.model_dump(),
            'generation':{'value':suite.model_dump()}}},'calls':[{'stage':stage}],
            'result':{'output':{'verification':{'whole_project':{'coordinated_repairs':[record]}}}}}
        request={'role':'test_author','payload':{'schema':wire_schema(patch_type(items)),
            'messages':[{}, {'content':json.dumps(data)}]}}
        self.assertEqual(retained_suite(row,{stage:request},{stage:{'value':value}}),candidate.model_dump())
        data['base_suite']['coverage']['digest']='f'*64
        request['payload']['messages'][1]['content']=json.dumps(data)
        with self.assertRaisesRegex(ValueError,'coverage'):retained_suite(row,{stage:request},{stage:{'value':value}})

    def test_advice_binds_the_host_failure_and_normalizes_only_narrative(self):
        model=advice_type('F123');raw={'diagnosis':'d'*1700,'required_capability':'r'*700,
            'evidence_ref':'F123','action':'request_capability'}
        advice=model.model_validate(raw);self.assertEqual(len(advice.diagnosis),1600)
        self.assertEqual(len(advice.required_capability),600)
        with self.assertRaises(ValueError):model.model_validate({**raw,'evidence_ref':'Finvented'})
        with self.assertRaises(ValueError):model.model_validate({**raw,'action':'execute_shell'})

    def test_real_output_gate_keeps_raw_metadata_without_a_new_agent_call(self):
        from test_agent_recovery import fixture as agent_fixture
        from orchestrator.agent_recovery import INLINE_RECOVERY_POLICY
        from orchestrator.whole_project import WholeProjectBuildRun
        flow,_=agent_fixture();_,_,reference,pending=retained_fixture()
        value=deepcopy(pending['proposal']);value['summary']='s'*1235
        flow.bundle['generation']={'test_author':{'context':98304}}
        flow.model_inputs=lambda data:data;flow.recovery._consulting=True;flow.scoped_repairs=True
        def dispatch(stage,*a,**kw):
            flow.stages[stage]={}
            return {'value':value}
        flow._opencode=Mock(side_effect=dispatch)
        stage='whole_recovery_9_output'
        flow._agent_output_authority={'stage':stage,'role':'test_author',
            'schema_digest':digest(patch_type(reference['failures']).model_json_schema())}
        returned=WholeProjectBuildRun._generate(flow,stage,'test_author',patch_type(reference['failures']),
            'Repair',{}, {},tool_policy=INLINE_RECOVERY_POLICY)
        self.assertEqual(returned,value);flow._opencode.assert_called_once()
        self.assertEqual(flow.stages[stage]['metadata_projection']['fields'][0]['characters_before'],1235)
        self.assertEqual(flow.counters['recovery_output_correction'],0)
