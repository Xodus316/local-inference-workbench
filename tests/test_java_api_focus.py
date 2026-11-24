"""Focused platform/library facts and conservative bounded Java edit corrections."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import zipfile

from orchestrator.compiler_api import query,validate_policy,POLICY_V2,dependency_recovery_proof
from orchestrator.java_api_probe import inspect,public_constructors
from orchestrator.java_source_evidence import patch_findings
from orchestrator.project_build import ProjectArtifact
from orchestrator.whole_project import WholeProjectBuildRun

POLICY={'policy':POLICY_V2,'classpath_roots':['/opt/cache'],'include_jdk':True}


def artifact(text):return ProjectArtifact(summary='Java source',files=[{'path':'api/A.java','content':text}])
def feedback(message):return {'failed_execution':{'commands':[{'exit_code':1,'sandbox_stage_results':{
    'status':'failed','cleanup_confirmed':True,'failures':[{'stage':'gradle-compile','status':'failed',
    'diagnostic_excerpt':message}]}}]}}


class JavaApiFocusTests(unittest.TestCase):
    def test_constructor_metadata_distinguishes_public_protected_and_generic_varargs(self):
        text=('public class lib.Foo {\n'
              '  public lib.Foo(java.util.Map<java.lang.String, java.lang.Integer>, java.lang.String...);\n'
              '  protected lib.Foo(boolean, java.lang.String);\n}')
        public=public_constructors(text,'lib.Foo')
        self.assertEqual(len(public),1)
        self.assertEqual(public[0]['parameter_count'],2)
        self.assertTrue(public[0]['varargs'])
        protected=public_constructors(text,'lib.Foo','protected')
        self.assertEqual(protected[0]['parameter_count'],2)
        self.assertFalse(protected[0]['varargs'])

    def test_plain_constructor_arity_uses_complete_pinned_facts_and_balanced_arguments(self):
        before=artifact('import lib.Foo;\nclass A {}')
        evidence={'documents':[{'name':'lib.Foo','public_constructors_complete':True,
            'public_constructors':[{'signature':'public lib.Foo();','parameter_count':0,'varargs':False}]}]}
        for expression,count in [('new Foo("a,b")',1),("new Foo('x')",1),
                ('new Foo(call(1,2), new int[]{1,2})',2),('new Foo<Map<String,Integer>>(value)',1)]:
            after=artifact('import lib.Foo;\nclass A { Object x = '+expression+'; }')
            with self.subTest(expression=expression):
                found=patch_findings(before.files,after.files,evidence)
                self.assertEqual(found[0]['kind'],'unsupported-public-constructor-arity')
                self.assertEqual(found[0]['argument_count'],count)
        for expression in ['new Foo()','new Foo(/* comment */)','new Foo(value < other)',
                           'new Foo(x -> x)','new Foo(1) { }']:
            after=artifact('import lib.Foo;\nclass A { Object x = '+expression+'; }')
            self.assertEqual(patch_findings(before.files,after.files,evidence),[])
        # Protected/package-local/nested access and unknown versions are not
        # established by a plain public-constructor inventory.
        same_package=artifact('package lib;\nclass A { Object x = new lib.Foo(1); }')
        self.assertEqual(patch_findings(before.files,same_package.files,evidence),[])
        evidence['documents'][0]['public_constructors_complete']=False
        self.assertEqual(patch_findings(before.files,artifact('import lib.Foo;\nclass A { Object x = new Foo(1); }').files,evidence),[])

    def test_no_public_constructor_and_varargs_have_different_admission(self):
        before=artifact('import lib.Foo;\nclass A {}')
        after=artifact('import lib.Foo;\nclass A { Object x = new Foo(); }')
        doc={'name':'lib.Foo','public_constructors_complete':True,'public_constructors':[]}
        self.assertEqual(patch_findings(before.files,after.files,{'documents':[doc]})[0]['argument_count'],0)
        doc['public_constructors']=[{'parameter_count':1,'varargs':True,'signature':'public lib.Foo(String...);'}]
        self.assertEqual(patch_findings(before.files,after.files,{'documents':[doc]}),[])
        doc['name']='lib.Outer$Inner'
        nested=artifact('import lib.Outer$Inner;\nclass A { Object x = new Outer$Inner(1); }')
        self.assertEqual(patch_findings(before.files,nested.files,{'documents':[doc]}),[])
    def test_query_binds_jdk_opt_in_and_derives_receiver_owner_at_typed_error(self):
        text='import java.time.Instant;\nimport java.time.temporal.ChronoField;\nclass A { void f(Instant value) { value.truncatedTo(ChronoField.MILLIS); } }'
        value=query(artifact(text).files,feedback('api/A.java:3: error: incompatible types'),POLICY)
        self.assertEqual(value['policy'],POLICY_V2)
        self.assertIn('java.time.Instant',value['focus_types'])
        self.assertIn('java.time.temporal.ChronoField',value['focus_types'])
        self.assertIn('truncatedTo',value['members'])
        self.assertTrue(value['include_jdk'])
        self.assertEqual(value['source_anchors'][0]['line'],3)
        for changed in ({**POLICY,'include_jdk':False},{k:v for k,v in POLICY.items() if k!='include_jdk'}):
            with self.assertRaises(ValueError):validate_policy(changed)

    def test_probe_projects_late_members_and_discovers_required_interface_enum_without_guesses(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'bin').mkdir();(root/'jmods').mkdir();cache=root/'cache';cache.mkdir()
            with zipfile.ZipFile(cache/'api.jar','w') as archive:archive.writestr('lib/Context.class',b'never executed')
            with zipfile.ZipFile(root/'jmods/java.base.jmod','w') as archive:
                for name in ('Value','Field','Unit','DurationUnit'):
                    archive.writestr('classes/java/example/'+name+'.class',b'never executed')
            docs={
                'java.example.Value':'public class java.example.Value {\n public java.example.Value adjust(java.example.DurationUnit);\n}',
                'java.example.Field':'public final class java.example.Field extends java.lang.Enum<java.example.Field> {\n public static final java.example.Field SUBFIELD;\n}',
                'java.example.Unit':'public final class java.example.Unit extends java.lang.Enum<java.example.Unit> implements java.example.DurationUnit {\n public static final java.example.Unit MILLIS;\n}',
                'java.example.DurationUnit':'public interface java.example.DurationUnit {\n}',
                'lib.Context':'public class lib.Context {\n'+''.join(' public void unrelated'+str(i)+'(java.lang.String);\n' for i in range(200))+' public <T> T error(java.lang.Class<T>, java.lang.String);\n}',
            }
            def javap(args,**kwargs):return SimpleNamespace(returncode=0,stdout=docs[args[-1]].encode())
            request={**POLICY,'classpath_roots':[str(cache)],'imports':['lib.Context','java.example.Field','java.example.Value'],
                'focus_types':['java.example.Value','java.example.Field','lib.Context'],'symbols':['BAD'],
                'members':['adjust','error'],'source_anchors':[]}
            with patch('orchestrator.java_api_probe.shutil.which',return_value=str(root/'bin/javap')), \
                    patch('orchestrator.java_api_probe.subprocess.run',side_effect=javap):
                result=inspect(request)
            self.assertTrue(result['complete_jdk_index'])
            by={doc['name']:doc for doc in result['documents']}
            self.assertIn('java.example.Unit',by)
            self.assertEqual(by['java.example.Unit']['declared_fields'],['MILLIS'])
            self.assertTrue(by['java.example.Field']['enum_fields_complete'])
            self.assertIn(' T error(',by['lib.Context']['content'])
            self.assertTrue(by['lib.Context']['truncated'])
            self.assertTrue(by['lib.Context']['member_projection']['matching_signatures_complete'])
            self.assertFalse(by['lib.Context']['member_projection']['inherited_members_inspected'])

    def test_enum_checks_require_complete_fields_and_ignore_comments_or_literals(self):
        before=artifact('import java.example.Unit;\nclass A { Object u = Unit.MILLIS; }')
        after=artifact('import java.example.Unit;\nclass A { Object u = Unit.GUESSED; }')
        evidence={'documents':[{'name':'java.example.Unit','declared_fields':['MILLIS'],'enum_fields_complete':True}]}
        found=patch_findings(before.files,after.files,evidence)
        self.assertEqual(found[0]['constant'],'GUESSED')
        self.assertEqual(found[0]['kind'],'constant-absent-from-pinned-enum')
        evidence['documents'][0]['enum_fields_complete']=False
        self.assertEqual(patch_findings(before.files,after.files,evidence),[])
        evidence['documents'][0]['enum_fields_complete']=True
        safe=artifact('import java.example.Unit;\nclass A { String note = "Unit.GUESSED"; /* Unit.GUESSED */ Object u = Unit.MILLIS; }')
        self.assertEqual(patch_findings(before.files,safe.files,evidence),[])

    def test_removed_import_check_uses_unchanged_code_and_declines_ambiguous_resolution(self):
        before=artifact('import lib.Feature;\nclass A {\n boolean enabled = Feature.ON;\n}')
        after=artifact('class A {\n boolean enabled = Feature.ON;\n}')
        found=patch_findings(before.files,after.files,{})
        self.assertEqual(found[0]['import'],'lib.Feature')
        self.assertEqual(found[0]['kind'],'removed-import-still-used')
        for text in ('class A {}','import alternative.Feature;\nclass A {\n boolean enabled = Feature.ON;\n}',
                     'import lib.*;\nclass A {\n boolean enabled = Feature.ON;\n}',
                     'class A {\n boolean enabled = lib.Feature.ON;\n}'):
            self.assertEqual(patch_findings(before.files,artifact(text).files,{}),[])

    def test_source_finding_uses_one_existing_edit_retry_without_changing_scope(self):
        from test_whole_project_flow import PLAN
        flow=object.__new__(WholeProjectBuildRun)
        flow.scoped_repairs,flow._regression_feedback=True,None
        flow.dependency_reference,flow.source_bytes=None,65536
        flow.artifact_type,flow.stages=ProjectArtifact,{}
        flow.model_inputs=lambda data:{'plan':PLAN,**data}
        before=artifact('import lib.Feature;\nclass A {\n boolean enabled = Feature.ON;\n}')
        bad='class A {\n boolean enabled = Feature.ON;\n int changed;\n}'
        fixed='import lib.Feature;\n'+bad
        def worker(stage,*args,**kwargs):
            flow.stages[stage]={}
            return {'files':{'api/A.java':fixed if stage.endswith('_edit_retry') else bad},'receipt':{
                'scratch_cleanup_confirmed':True,'events':[{'type':'step_finish','part':{'reason':'stop'}}]}}
        flow._opencode=Mock(side_effect=worker)
        evidence={'policy':POLICY_V2,'documents':[],'missing_imports':[]}
        with patch('orchestrator.compiler_api.inspect_failure',return_value=evidence):
            result=flow._patch('build_compile_repair_1_1','repairer',before,SimpleNamespace(files=[],commands=[]),
                {'compiler_dependency_evidence':evidence},paths=['api/A.java'],compile_patch=False)
        self.assertEqual(flow._opencode.call_count,2)
        self.assertEqual(flow._opencode.call_args.args[0],'build_compile_repair_1_1_edit_retry')
        self.assertEqual(flow._opencode.call_args.kwargs['inputs']['remaining_java_api_source'][0]['import'],'lib.Feature')
        self.assertIn('import lib.Feature;',result.files[0].content)

    def test_unsupported_constructor_routes_to_the_existing_scoped_edit_correction(self):
        from test_whole_project_flow import PLAN
        flow=object.__new__(WholeProjectBuildRun)
        flow.scoped_repairs,flow._regression_feedback=True,None
        flow.dependency_reference,flow.source_bytes=None,65536
        flow.artifact_type,flow.stages=ProjectArtifact,{}
        flow.model_inputs=lambda data:{'plan':PLAN,**data}
        before=ProjectArtifact(summary='Scoped source',files=[
            {'path':'api/A.java','content':'import lib.Foo;\nclass A { Object x; }'},
            {'path':'api/ReadOnly.java','content':'import lib.Foo;\nclass ReadOnly { Object x = new Foo(1); }'}])
        bad='import lib.Foo;\nclass A { Object x = new Foo(1); }'
        fixed='import lib.Foo;\nclass A { Object x = new Foo(); }'
        def worker(stage,*args,**kwargs):
            flow.stages[stage]={}
            return {'files':{'api/A.java':fixed if stage.endswith('_edit_retry') else bad},'receipt':{
                'scratch_cleanup_confirmed':True,'events':[{'type':'step_finish','part':{'reason':'stop'}}]}}
        flow._opencode=Mock(side_effect=worker)
        evidence={'policy':POLICY_V2,'documents':[{'name':'lib.Foo','public_constructors_complete':True,
            'public_constructors':[{'parameter_count':0,'varargs':False,'signature':'public lib.Foo();'}]}],'missing_imports':[]}
        # The post-edit compiler query can disappear when the diagnostic line
        # changes. Retain pinned facts already supplied to this worker, while
        # limiting corrections to its editable files.
        with patch('orchestrator.compiler_api.inspect_failure',return_value=None):
            result=flow._patch('build_compile_repair_1_2','repairer',before,SimpleNamespace(files=[],commands=[]),
                {'compiler_dependency_evidence':evidence},paths=['api/A.java'],compile_patch=False)
        self.assertEqual(flow._opencode.call_count,2)
        self.assertEqual(flow._opencode.call_args.args[0],'build_compile_repair_1_2_edit_retry')
        self.assertEqual(flow._opencode.call_args.kwargs['inputs']['remaining_java_api_source'][0]['argument_count'],1)
        self.assertEqual(len(flow._opencode.call_args.kwargs['inputs']['remaining_java_api_source']),1)
        self.assertIn('new Foo()',result.files[0].content)

    def test_same_missing_api_fact_cannot_earn_another_reserve_by_changing_projection_metadata(self):
        evidence={'policy':POLICY_V2,'complete_index':True,'complete_jdk_index':True,'missing_imports':['lib.Missing'],
            'symbols':['Missing'],'documents':[{'name':'lib.Existing','content':'public class lib.Existing {}'}],
            'image_id':'sha256:'+'a'*64,'classpath_roots':['/opt/cache'],'receipt_digest':'b'*64}
        proof=dependency_recovery_proof([],evidence)
        self.assertIsNotNone(proof)
        changed=deepcopy(evidence);changed['documents'][0]['member_projection']={'requested':['anotherMethod']}
        self.assertIsNone(dependency_recovery_proof([{'dependency_recovery':proof}],changed))


if __name__=='__main__':unittest.main()
