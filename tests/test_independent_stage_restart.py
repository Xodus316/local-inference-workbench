from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from orchestrator.candidate_source_contract import KEY as SOURCE_KEY,POLICY as SOURCE_POLICY,evaluate,gate
from orchestrator.independent_stage_restart import POLICY,retained_counters
from orchestrator.project_build import ProjectArtifact
from orchestrator.repair_coordinator import (causal_patch_type,evidence_catalog,compose_tests,test_scope,
    executable_edits,metadata_projection,DIRECT_INSTRUCTION)
from orchestrator.reference_repair_coordinator import reference_evidence,repair
from orchestrator.store import digest
from orchestrator.test_time_contract import iso8601_milliseconds,observe_timestamp_update
from test_repair_coordinator import fixture,receipt


class IndependentStageRestartTests(unittest.TestCase):
    def test_per_edit_schema_has_no_cross_reference_and_every_edit_requires_evidence(self):
        flow,suite=fixture();items=reference_evidence(flow.bundle,suite,receipt(flow,suite))['failures']
        refs=list(evidence_catalog(items));model=causal_patch_type(items)
        raw={'summary':'Repair helper','edits':[{'path':'tests/test_math.py','before':'return 0','after':'return 1',
            'cause':'Both failed cases share this helper','evidence_refs':refs}]}
        self.assertNotIn('edit_indexes',json.dumps(model.model_json_schema()))
        self.assertNotIn('clusters',model.model_json_schema()['properties'])
        self.assertNotIn('edit_indexes',DIRECT_INSTRUCTION)
        candidate=compose_tests(flow,suite,items,test_scope(suite,items),raw)
        self.assertIn('return 1',candidate.files[0].content)
        for name in ['cause','evidence_refs']:
            bad=deepcopy(raw);bad['edits'][0].pop(name)
            with self.assertRaises(ValueError):model.model_validate(bad)
        for extra in [{'edit_indexes':[0]},{'evidence_refs':['Einvented']}]:
            bad=deepcopy(raw);bad['edits'][0].update(extra)
            with self.assertRaises(ValueError):model.model_validate(bad)
        normalized=model.model_validate({**raw,'summary':'s'*1400}).model_dump()
        self.assertEqual(executable_edits(raw),executable_edits(normalized));metadata_projection({**raw,'summary':'s'*1400},normalized)
        changed=deepcopy(raw);changed['edits'][0]['cause']='Different prose'
        self.assertEqual(digest(executable_edits(raw)),digest(executable_edits(changed)))

    def test_direct_broker_uses_same_original_role_and_all_existing_gates(self):
        flow,suite=fixture();flow.bundle['whole_project_stage_continuation']={'policy':POLICY}
        flow.counters['recovery_output_correction']=0
        def generate(stage,role,model_type,instruction,data,visible,**kw):
            if stage.endswith('_consult'):
                return {'diagnosis':'Repair the helper','evidence_ref':data['failure_evidence_id'],
                    'action':'correct_stage_output','requested_changes':['Return the required value']}
            self.assertNotIn('edit_indexes',instruction)
            return {'summary':'Repair helper','edits':[{'path':'tests/test_math.py','before':'return 0','after':'return 1',
                'cause':'Observed shared helper failure','evidence_refs':[x['evidence_id'] for x in data['failures']]}]}
        flow._generate=Mock(side_effect=generate);flow.control=Mock(side_effect=lambda candidate:receipt(flow,candidate,failed=()))
        with patch('orchestrator.reference_repair_coordinator.targeted',return_value=({'failed':[],'tests_run':2},'probe')):
            candidate=repair(flow,suite,receipt(flow,suite))
        flow.compile_test_suite.assert_called_once();flow.control.assert_called_once()
        self.assertTrue(all(c.args[1]=='test_author' for c in flow._generate.call_args_list))
        self.assertEqual(flow.counters['recovery_output_correction'],1);self.assertIn('return 1',candidate.files[0].content)

    def test_portable_timestamp_precision_and_invalid_values(self):
        expected=1577836800510
        for value in ['2020-01-01T00:00:00.51','2020-01-01T00:00:00.510Z',
                '2020-01-01T00:00:00.510123456+00:00','2020-01-01T02:00:00.51+02:00']:
            self.assertEqual(iso8601_milliseconds(value),expected)
        self.assertEqual(iso8601_milliseconds('2020-01-01T00:00:00Z'),1577836800000)
        for value in [1577836800510,None,'not-a-date']:
            with self.assertRaises(AssertionError):iso8601_milliseconds(value)
        before='2020-01-01T00:00:00.510Z'
        with patch('time.monotonic',side_effect=[0,1]):
            with self.assertRaisesRegex(AssertionError,'did not refresh'):observe_timestamp_update(before,lambda:before,0.5)
        with self.assertRaisesRegex(AssertionError,'backwards'):
            observe_timestamp_update(before,lambda:'2020-01-01T00:00:00.509Z')
        self.assertEqual(observe_timestamp_update(before,lambda:'2020-01-01T00:00:00.511Z'),'2020-01-01T00:00:00.511Z')

    def test_packaging_checks_are_mandatory_candidate_only_and_never_optional_tests(self):
        files={'README.md':'Develop with Docker Compose, Gradle and npm.',
            'api/src/main/resources/application.yml':'uri: ${MONGODB_URI:mongodb://localhost/test}\nsecret: ${JWT_SECRET:dev}\n',
            'frontend/package.json':json.dumps({'devDependencies':{'vite':'^5.4.0'}}),
            'docker-compose.yml':'services:\n  db:\n    image: mongo:7\n'}
        artifact=ProjectArtifact(summary='Candidate',files=[{'path':p,'content':s} for p,s in files.items()])
        self.assertEqual(evaluate(artifact)['status'],'passed')
        flow=SimpleNamespace(bundle={SOURCE_KEY:deepcopy(SOURCE_POLICY)},evidence={},stages={},state={'run_id':'fixture'},
            runtime=SimpleNamespace(store=Mock()),output={},counters={},limits={'max_repair_attempts':8})
        self.assertIs(gate(flow,artifact,None),artifact);self.assertIs(gate(flow,artifact,None),artifact)
        flow.runtime.store.save_artifact.assert_called_once()
        bad=ProjectArtifact(summary='No docs',files=[f for f in artifact.files if f.path!='README.md'])
        with patch('orchestrator.whole_project_gates.repair',return_value=artifact) as repairer:
            gate(flow,bad,None)
        self.assertIn('readme-development',repairer.call_args.args[3]['feedback']['candidate_source_contract']['failed_checks'])

    def test_only_application_compile_and_smoke_counters_are_retained_in_new_epoch(self):
        prior={'whole_compile_initial_1':2,'whole_compile_initial_2':1,'whole_self_repair':3,
            'whole_compile_1_1':2,'recovery_diagnosis':16,'recovery_output_correction':7,'repair':4,'build_tests_syntax':2}
        kept=retained_counters(prior)
        self.assertEqual(kept,{'whole_compile_initial_1':2,'whole_compile_initial_2':1,'whole_self_repair':3})
        self.assertEqual(prior['recovery_diagnosis'],16)

    def test_candidate_contract_is_bound_when_present_and_absent_legacy_binding_is_unchanged(self):
        from orchestrator.whole_project_checkpoint import binding_digest
        plain={'request':{'prompt':'Same task'}}
        enabled={**plain,SOURCE_KEY:deepcopy(SOURCE_POLICY)}
        self.assertNotEqual(binding_digest(plain),binding_digest(enabled))
        altered=deepcopy(enabled);altered[SOURCE_KEY]['checks']=[]
        self.assertNotEqual(binding_digest(enabled),binding_digest(altered))
