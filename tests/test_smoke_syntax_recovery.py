"""Smoke parser failures repair retained source, never regenerate its manifest."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from orchestrator.build_coverage import TestSyntaxError
from orchestrator.project_build import ProjectArtifact,ProjectProject,SelectedTextRepair
from orchestrator.whole_project import WholeProjectBuildRun
from orchestrator.whole_project_sandbox import SelfTestSyntaxError,validate_self_suite
from orchestrator.whole_project_smoke import INLINE_SMOKE_POLICY
from test_whole_project_sandbox import suite_value


BAD=('import unittest\n\nclass SelfCheck(unittest.TestCase):\n'
     '    @classmethod\n    def setUpClass(cls):\n        cls.pass = 2\n'
     + '\n'*20 + '    def test_value(self):\n        self.assertEqual(self.pass, 2)\n')


def workflow():
    value=object.__new__(WholeProjectBuildRun)
    value.scoped_repairs=True
    value.artifact_type,value.project_type=ProjectArtifact,ProjectProject
    value.bundle={};value.output={'plan':{'acceptance_criteria':['Compute the sum']}}
    value.contract={'test_files':15};value.stages={'whole_self_tests':{}}
    value.validate_suite=Mock();value.fixture_problems=Mock(return_value=[])
    return value


def fixture():
    raw=suite_value();raw['implementation_files']=['app.py','dto_one.py','dto_two.py']
    raw['files'][0]['content']=BAD;raw['assumptions']=['Retain the original assumption']
    candidate=ProjectArtifact(summary='source',files=[{'path':p,'content':'source'} for p in raw['implementation_files']])
    edits=[{'path':raw['files'][0]['path'],'before':line,'after':line.replace('.pass','.password')}
           for line in BAD.splitlines() if '.pass' in line]
    return raw,candidate,{'summary':'Correct the invalid fixture identifier and its uses','edits':edits}


class SmokeSyntaxRecoveryTests(unittest.TestCase):
    def test_syntax_error_retains_structurally_checked_suite_and_parser_locations(self):
        raw,candidate,_=fixture();value=workflow()
        with self.assertRaises(SelfTestSyntaxError) as caught:validate_self_suite(value,raw,candidate)
        error=caught.exception
        self.assertEqual(error.suite.model_dump(),raw)
        self.assertEqual(error.diagnostics[0]['path'],'tests/test_self.py')
        self.assertEqual(error.diagnostics[0]['line'],6)
        self.assertGreater(error.diagnostics[0]['column'],0)
        self.assertEqual(error.diagnostics[0]['message'],'invalid syntax')
        value.scoped_repairs=False
        with self.assertRaises(TestSyntaxError) as legacy:validate_self_suite(value,raw,candidate)
        self.assertNotIsInstance(legacy.exception,SelfTestSyntaxError)

    def test_one_exact_edit_correction_preserves_inventory_and_all_manifest_fields(self):
        raw,candidate,patch=fixture();original=deepcopy(raw);value=workflow()
        value._generate=Mock(side_effect=[raw,patch])
        result=value._author_self_suite(candidate,raw['implementation_files'])
        self.assertEqual(value._generate.call_count,2)
        retry=value._generate.call_args
        self.assertEqual(retry.args[0],'whole_self_tests_retry')
        self.assertIs(retry.args[2],SelectedTextRepair)
        self.assertEqual(set(retry.args[2].model_json_schema()['properties']),{'summary','edits'})
        self.assertEqual(retry.kwargs,{'tool_policy':INLINE_SMOKE_POLICY})
        data=retry.args[4]
        self.assertEqual(data['diagnostics'][0]['line'],6)
        self.assertEqual(data['snippets'][0]['source'],BAD)
        # The remote use beyond the parser's first error must be repairable
        # within the same correction, without a second generation allowance.
        self.assertIn('self.pass',data['snippets'][0]['source'])
        self.assertEqual(result.implementation_files,raw['implementation_files'])
        self.assertEqual(result.commands,raw['commands'])
        self.assertEqual(result.assumptions,raw['assumptions'])
        self.assertEqual(result.coverage[0].test_name,'SelfCheck.test_value')
        self.assertEqual(result.coverage[0].scenario,raw['coverage'][0]['scenario'])
        self.assertEqual(result.files[0].content,BAD.replace('.pass','.password'))
        self.assertEqual(raw,original)
        self.assertEqual(value.stages['whole_self_tests']['syntax_correction']['retained_manifest_digest'],
                         value.stages['whole_self_tests_retry']['syntax_correction']['retained_manifest_digest'])

    def test_replacement_suite_or_changed_manifest_is_rejected_by_the_patch_schema(self):
        raw,candidate,patch=fixture()
        for response in [{**patch,'implementation_files':['app.py']},raw,
                         {**patch,'commands':['python3 -c pass']}, {**patch,'coverage':[]}]:
            value=workflow();value._generate=Mock(side_effect=[raw,response])
            with self.subTest(fields=list(response)),self.assertRaises(ValueError):
                value._author_self_suite(candidate,raw['implementation_files'])
            self.assertEqual(value._generate.call_count,2)

    def test_undetected_file_changes_and_renamed_tests_cannot_enter_the_retained_suite(self):
        raw,candidate,patch=fixture()
        for edit in [
            {'path':'tests/test_other.py','before':'pass','after':'return'},
            {'path':'tests/test_self.py','before':'def test_value','after':'def test_other'}]:
            value=workflow();value._generate=Mock(side_effect=[raw,{'summary':'bad change','edits':[edit]}])
            with self.subTest(edit=edit),self.assertRaisesRegex(ValueError,'outside|declaration'):
                value._author_self_suite(candidate,raw['implementation_files'])
            self.assertEqual(value._generate.call_count,2)

    def test_remaining_syntax_error_stops_after_existing_single_correction(self):
        raw,candidate,patch=fixture();patch['edits']=patch['edits'][:1]
        value=workflow();value._generate=Mock(side_effect=[raw,patch])
        with self.assertRaises(SelfTestSyntaxError):value._author_self_suite(candidate,raw['implementation_files'])
        self.assertEqual(value._generate.call_count,2)

    def test_pinned_image_syntax_diagnostics_are_retained_after_host_parsing_succeeds(self):
        raw,candidate,_=fixture();raw['files'][0]['content']=BAD.replace('.pass','.password')
        value=workflow()
        diagnostic={'path':'tests/test_self.py','line':6,'column':1,'message':'pinned Python syntax error','python_version':'3.10'}
        value.fixture_problems.side_effect=TestSyntaxError([diagnostic])
        with self.assertRaises(SelfTestSyntaxError) as caught:validate_self_suite(value,raw,candidate)
        self.assertEqual(caught.exception.diagnostics,[diagnostic])
        self.assertEqual(caught.exception.suite.implementation_files,raw['implementation_files'])


if __name__=='__main__':unittest.main()
