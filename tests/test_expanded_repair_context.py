from copy import deepcopy
import hashlib
import json
import io
from contextlib import redirect_stderr
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from orchestrator.checkpoint_repair_continuation import POLICY, KEY, budgets, approved_budgets
from orchestrator.repair_allowance import output_limit, coordinated_limit
from orchestrator.stage_recovery import validate_costs
from orchestrator.reference_repair_coordinator import repair, inventory
from orchestrator.reference_context import profile_for, verified_observation
from orchestrator import reference_context_probe as probe
from orchestrator.project_build import sandbox_stage_results
from orchestrator.sandbox_profiles import verification_profile_identity
from orchestrator.store import STAGES
from orchestrator.whole_project_test_repair import METHOD_PATCH_STAGE, DIAGNOSIS_STAGE
from test_repair_coordinator import fixture, receipt


class ExpandedRepairContextTests(unittest.TestCase):
    def expanded(self):
        flow, suite = fixture()
        flow.bundle[KEY] = {'policy': POLICY, 'fresh_budgets': budgets(True)}
        flow.limits['max_test_defect_repairs'] = 16
        return flow, suite

    def test_explicit_profiles_keep_old_grants_replayable_and_reject_implicit_increases(self):
        flow, _ = self.expanded()
        self.assertEqual((output_limit(flow.bundle), coordinated_limit(flow.bundle)), (16, 16))
        flow.bundle[KEY]['fresh_budgets'] = budgets()
        self.assertEqual((output_limit(flow.bundle), coordinated_limit(flow.bundle)), (4, 6))
        for bad in ({**budgets(True), 'output_corrections': 17}, {**budgets(), 'diagnoses': 16.0}, {}):
            with self.assertRaises(ValueError):
                approved_budgets(bad)

    def test_cost_check_accepts_last_slot_and_rejects_overspend(self):
        flow, _ = self.expanded()
        counters = {'recovery_output_correction': 16, 'coordinated_patch': 16, 'recovery_context_observation': 2, 'build_tests_defect': 16}
        validate_costs(flow.bundle, counters)
        for key in counters:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_costs(flow.bundle, {**counters, key: counters[key]+1})

    def test_sixteenth_shared_patch_reaches_original_gates(self):
        flow, suite = self.expanded()
        flow.counters.clear()
        flow.counters.update(build_tests_defect=16, coordinated_patch=15, recovery_output_correction=15, recovery_diagnosis=15)
        from orchestrator.repair_coordinator import evidence_catalog
        def generate(stage,role,model_type,instruction,data,visible,**kw):
            if stage.endswith('_consult'):
                return {'diagnosis':'Correct helper','evidence_ref':data['failure_evidence_id'],
                    'action':'correct_stage_output','requested_changes':['Correct helper']}
            return {'summary':'Correct helper','edits':[{'path':'tests/test_math.py','before':'return 0','after':'return 1',
                'cause':'Shared wrong value','evidence_refs':list(evidence_catalog(data['failures']))}]}
        flow._generate=Mock(side_effect=generate)
        flow.control = Mock(side_effect=lambda candidate: receipt(flow, candidate, failed=()))
        with patch('orchestrator.reference_repair_coordinator.targeted', return_value=({'failed': []}, 'probe')):
            result = repair(flow, suite, receipt(flow, suite))
        self.assertIn('return 1', result.files[0].content)
        self.assertEqual(flow.counters['coordinated_patch'], 16)
        self.assertEqual(flow.counters['recovery_output_correction'], 16)
        self.assertEqual(flow.counters['build_tests_defect'], 16)
        self.assertEqual(flow._generate.call_args_list[0].args[0], 'whole_recovery_16_consult')
        flow.compile_test_suite.assert_called_once()
        flow.control.assert_called_once()

    def test_sixteenth_ordinary_patch_is_dispatched_and_uses_shared_validation_pool(self):
        flow, suite = self.expanded()
        flow.counters.clear()
        flow.counters.update(build_tests_defect=15, coordinated_patch=15, recovery_output_correction=0)
        from orchestrator.repair_coordinator import evidence_catalog
        from orchestrator.reference_repair_coordinator import reference_evidence
        items = reference_evidence(flow.bundle, suite, receipt(flow, suite))['failures']
        proposal = {'summary': 'Correct helper', 'edits': [{'path': 'tests/test_math.py', 'before': 'return 0',
            'after': 'return 1', 'cause': 'Shared wrong value', 'evidence_refs': list(evidence_catalog(items))}]}
        flow._generate = Mock(return_value=proposal)
        flow.control = Mock(side_effect=lambda candidate: receipt(flow, candidate, failed=()))
        with patch('orchestrator.reference_repair_coordinator.targeted', return_value=({'failed': []}, 'probe')):
            repair(flow, suite, receipt(flow, suite))
        self.assertEqual(flow._generate.call_args.args[0], 'build_tests_defect_16')
        self.assertEqual(flow.counters['build_tests_defect'], 16)
        self.assertEqual(flow.counters['coordinated_patch'], 16)
        self.assertEqual(flow.counters['recovery_output_correction'], 0)

    def test_stage_admission_covers_expanded_budget_boundaries(self):
        for name in ('build_tests_defect_16', 'build_tests_defect_16_file_15', 'build_tests_diagnose_16_file_15',
                     'whole_recovery_16_output', 'whole_coordinated_16_targeted', 'whole_coordinated_16_evaluation',
                     'build_control_63', 'build_test_preflight_63', 'whole_reference_context_2'):
            self.assertIn(name, STAGES)
        self.assertTrue(METHOD_PATCH_STAGE.fullmatch('build_tests_defect_16_file_15'))
        self.assertTrue(DIAGNOSIS_STAGE.fullmatch('build_tests_diagnose_16_file_15'))
        self.assertFalse(METHOD_PATCH_STAGE.fullmatch('build_tests_defect_17_file_1'))

    def context_receipt(self):
        flow, suite = fixture()
        flow.bundle['sandbox']['image_id'] = 'sha256:'+'a'*64
        cases = ['test_math.Checks.test_b']
        profile = profile_for(flow.bundle, cases)
        result = receipt(flow, suite)
        result['sandbox']['image_id'] = flow.bundle['sandbox']['image_id']
        identity = verification_profile_identity(profile, flow.bundle['sandbox']['image_id'])
        result['sandbox']['effective_runtime'] = identity
        result['commands'][0].update(effective_runtime=identity, profile_sources_unchanged=True)
        empty_sha=hashlib.sha256(b'').hexdigest()
        result['commands'][0]['stdout_capture']={'policy':'bounded-stdout-capture-v1','limit_bytes':131072,
            'total_bytes':0,'captured_bytes':0,'truncated':False,'complete':True,'utf8_valid':True,
            'sha256':empty_sha,'captured_sha256':empty_sha}
        summary = sandbox_stage_results(result['commands'][0])
        summary['reference_context_probe'] = {'policy': probe.POLICY, 'cases': cases,
            'sequence': sorted(inventory(suite)), 'acceptance_credit': False, 'observations': [{
                'case': cases[0], 'original_spec_sha256': 'a'*64, 'instrumented_spec_sha256': 'b'*64,
                'exit_code': 1, 'snapshots': [{'pathname': '/items', 'body_text': 'Items Page 1 of 2',
                    'row_count': 10, 'rows': ['Item'], 'navigation': [], 'buttons': [], 'headings': ['Items']}]}]}
        result['commands'][0]['stderr'] = result['commands'][0]['stderr'].split('OFFLINE_PROJECT_RESULT ')[0] + 'OFFLINE_PROJECT_RESULT '+json.dumps(summary)
        return flow, suite, result, profile, cases

    def test_context_requires_full_inventory_source_profile_and_no_acceptance_credit(self):
        flow, suite, result, profile, cases = self.context_receipt()
        value = verified_observation(flow.bundle, suite, result, profile, cases)
        self.assertFalse(value['acceptance_credit'])
        for kind in ('source', 'profile', 'sequence', 'credit', 'empty','truncated'):
            bad = deepcopy(result)
            summary = sandbox_stage_results(bad['commands'][0])
            witness = summary['reference_context_probe']
            if kind == 'source': bad['commands'][0]['source_unchanged'] = False
            if kind == 'profile': bad['commands'][0]['profile_sources_unchanged'] = False
            if kind == 'sequence': witness['sequence'].pop()
            if kind == 'credit': witness['acceptance_credit'] = True
            if kind == 'empty': witness['observations'][0]['snapshots'] = []
            if kind == 'truncated': bad['commands'][0]['stdout_capture']['truncated']=True
            bad['commands'][0]['stderr'] = bad['commands'][0]['stderr'].split('OFFLINE_PROJECT_RESULT ')[0]+'OFFLINE_PROJECT_RESULT '+json.dumps(summary)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                verified_observation(flow.bundle, suite, bad, profile, cases)

    def test_unavailable_settled_diagnostic_is_reported_but_unsettled_execution_blocks(self):
        from orchestrator.reference_context import observe
        from orchestrator.reference_repair_coordinator import reference_evidence
        flow,suite=self.expanded()
        suite.files[0].content += '\n# public test interface: #workbench-dom\n'
        result=receipt(flow,suite);before=reference_evidence(flow.bundle,suite,result)
        flow.runtime._build_execution=Mock(return_value=result)
        with patch('orchestrator.reference_context.verified_observation',side_effect=ValueError('No DOM snapshot')):
            value=observe(flow,suite,before)
            self.assertEqual(value['status'],'unavailable')
            self.assertFalse(value['acceptance_credit'])
            self.assertEqual(observe(flow,suite,before),value)
        flow.runtime._build_execution.assert_called_once()
        flow.evidence['reference_context_observations']=[]
        with patch('orchestrator.project_build.execution_settled',return_value=False),self.assertRaisesRegex(ValueError,'unresolved'):
            observe(flow,suite,before)

    def test_child_preserves_full_order_original_failure_and_mounted_source(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = '''import subprocess,unittest
class Checks(unittest.TestCase):
    def test_a(self):
        subprocess.run(['node','/opt/evaluation/run_vitest.mjs'],input='first',text=True)
    def test_b(self):
        subprocess.run(['node','/opt/evaluation/run_vitest.mjs'],input="original #workbench-dom spec",text=True)
        self.fail('original assertion')
'''
            path = directory/'test_context_child.py'
            path.write_text(source, encoding='utf8')
            seen = []
            def execute(command, **kwargs):
                spec = kwargs['input']; seen.append(spec)
                marker = __import__('re').search(r'WORKBENCH_PUBLIC_DOM_[a-f0-9]+ ', spec)
                out = marker[0]+json.dumps({'pathname': '/items', 'body_text': 'Public'}) if marker else ''
                return subprocess.CompletedProcess(command, 0, out)
            try:
                with patch.dict(os.environ, WORKBENCH_REPAIR_CASES=json.dumps(['test_context_child.Checks.test_b'])), \
                     patch.object(probe, 'OUTPUT', directory/'output.json'), patch.object(subprocess, 'run', side_effect=execute), redirect_stderr(io.StringIO()):
                    code = probe.run_suite(temp)
                value = json.loads((directory/'output.json').read_text())
            finally:
                sys.modules.pop('test_context_child', None)
                sys.path.remove(temp)
            self.assertEqual(code, 1)
            self.assertEqual(value['sequence'], ['test_context_child.Checks.test_a','test_context_child.Checks.test_b'])
            self.assertEqual(seen[0], 'first')
            self.assertTrue(seen[1].startswith('original #workbench-dom spec'))
            self.assertEqual(path.read_text(), source)
            self.assertEqual(value['observations'][0]['original_spec_sha256'], hashlib.sha256(b'original #workbench-dom spec').hexdigest())
            self.assertFalse(value['acceptance_credit'])
