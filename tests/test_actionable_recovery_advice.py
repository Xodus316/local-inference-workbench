import unittest

from orchestrator.agent_recovery import advice_type,EvidenceRecoveryAdvice
from orchestrator.wire import wire_schema


class ActionableRecoveryAdviceTests(unittest.TestCase):
    def test_correction_requires_executable_changes_in_its_field_and_no_missing_capability(self):
        model=advice_type('Factual')
        base={'diagnosis':'Fixture is beyond the first page','evidence_ref':'Factual','action':'correct_stage_output'}
        for changes in [None,[],['   ']]:
            value=dict(base)
            if changes is not None:value['requested_changes']=changes
            with self.subTest(changes=changes),self.assertRaisesRegex(ValueError,'nonempty requested_changes'):
                model.model_validate(value)
        good={**base,'requested_changes':['Navigate the paginated table to the fixture and preserve assertions']}
        self.assertEqual(model.model_validate(good).requested_changes,good['requested_changes'])
        with self.assertRaisesRegex(ValueError,'empty required_capability'):
            model.model_validate({**good,'required_capability':'none; use the existing capability'})
        self.assertEqual(model.model_validate({**base,'action':'stop'}).requested_changes,[])
        with self.assertRaisesRegex(ValueError,'missing capability'):
            model.model_validate({**base,'action':'request_capability'})

    def test_native_schema_enforces_action_requirements_without_changing_legacy_wire_schemas(self):
        schema=wire_schema(advice_type('Factual'))
        for branch in schema['oneOf']:
            self.assertEqual(branch['properties']['evidence_ref']['enum'],['Factual'])
            self.assertTrue({'diagnosis','evidence_ref','action'}<=set(branch['required']))
            self.assertIs(branch['additionalProperties'],False)
        branches={x['properties']['action']['enum'][0]:x for x in schema['oneOf']}
        correct=branches['correct_stage_output']
        self.assertIn('requested_changes',correct['required'])
        self.assertEqual(correct['properties']['requested_changes']['minItems'],1)
        self.assertEqual(correct['properties']['required_capability']['maxLength'],0)
        self.assertEqual(correct['properties']['required_capability']['enum'],[''])
        self.assertIn('required_capability',branches['request_capability']['required'])
        self.assertNotIn('requested_changes',branches['stop']['required'])
        self.assertNotIn('oneOf',wire_schema(EvidenceRecoveryAdvice))

    def test_branch_only_decoder_still_must_produce_a_complete_host_valid_response(self):
        model=advice_type('Factual')
        for branch in wire_schema(model)['oneOf']:
            action=branch['properties']['action']['enum'][0]
            values={'diagnosis':'Observed retained failure','evidence_ref':'Factual','action':action,
                'requested_changes':['Correct the failing fixture without changing assertions'],
                'required_capability':'A bounded host adapter'}
            response={key:values[key] for key in branch['required']}
            with self.subTest(action=action):
                self.assertEqual(model.model_validate(response).action,action)
