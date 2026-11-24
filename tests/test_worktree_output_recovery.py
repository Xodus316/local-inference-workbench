"""A proven length stop can use one existing correction, never passing credit."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from orchestrator.project_opencode import OpenCodeOutputLimitError
from orchestrator.project_opencode_whole import WORKTREE_PARTIAL_POLICY
from orchestrator.project_build import ProjectArtifact
from orchestrator.whole_project import WholeProjectBuildRun
from test_whole_project_test_repair_v2 import fake_worker


def response(files,finish='stop',clean=True):
    return {'files':files,**({'finish_reason':'length'} if finish=='length' else {}),
        'receipt':{'scratch_cleanup_confirmed':clean,'events':[{'type':'step_finish','part':{'reason':finish}}],
            **({'worktree_partial_policy':WORKTREE_PARTIAL_POLICY} if finish=='length' else {})}}


def flow():
    value=object.__new__(WholeProjectBuildRun)
    value.source_bytes,value.artifact_type,value.stages=65536,ProjectArtifact,{}
    value.scoped_repairs,value._regression_feedback,value.dependency_reference=True,None,None
    value.model_inputs=lambda data:{'plan':{'implementation_files':['a.py','b.py']},**data}
    return value


class WorktreeOutputRecoveryTests(unittest.TestCase):
    def worker_result(self,*,schema=None,unsafe=False):
        worker=fake_worker();worker.role='implementer';worker.source_bytes=65536;worker.test_bytes=16384
        worker.endpoint='http://127.0.0.1:1'
        events=[{'type':'step_start','sessionID':'isolated'},
                {'type':'step_finish','sessionID':'isolated','part':{'reason':'length','tokens':{
                    'input':100,'output':400,'reasoning':100,'cache':{'read':0,'write':0}}}}]
        def execute(root,*args,**kwargs):
            (root/('readonly.py' if unsafe else 'a.py')).write_text('written',encoding='utf8')
            return ('\n'.join(json.dumps(e) for e in events)+'\n').encode()
        worker._execute=Mock(side_effect=execute)
        with TemporaryDirectory() as directory,patch('orchestrator.project_opencode_whole._executable_identity',return_value=worker.sha256),patch('orchestrator.project_opencode_whole.subprocess.run'):
            if schema is not None:
                return worker._session(stage='whole_self_tests',role='implementer',model=worker.model,
                    settings={'context':worker.context,'think':worker.thinking},original={'readonly.py':'fixed'},
                    editable=(),system='task',prompt='task',schema=schema,collect=Mock(),cancel=Event(),
                    deadline=time.monotonic()+30,scratch_parent=Path(directory),on_event=None)
            result=worker.run(stage='implement',role='implementer',model=worker.model,
                settings={'context':worker.context,'think':worker.thinking},visible_files={'readonly.py':'fixed'},
                editable_paths=['a.py','b.py'],allow_create=True,instruction='task',inputs={},cancel=Event(),
                deadline=time.monotonic()+30,scratch_parent=Path(directory))
            self.assertEqual(list(Path(directory).iterdir()),[])
            return result

    def test_worker_preserves_only_safe_scoped_partial_files_after_clean_length_stop(self):
        result=self.worker_result()
        self.assertEqual(result['files'],{'a.py':'written'})
        self.assertEqual(result['finish_reason'],'length')
        self.assertEqual(result['receipt']['worktree_partial_policy'],WORKTREE_PARTIAL_POLICY)
        self.assertTrue(result['receipt']['scratch_cleanup_confirmed'])
        self.assertEqual(result['usage']['completion_tokens'],500)

    def test_length_stop_does_not_bypass_readonly_scope_or_structured_output_rules(self):
        with self.assertRaisesRegex(ValueError,'approved file changes'):self.worker_result(unsafe=True)
        with self.assertRaises(OpenCodeOutputLimitError):self.worker_result(schema={'type':'object'})

    def test_partial_inventory_reuses_one_correction_with_retained_files_readonly(self):
        value=flow()
        def invoke(stage,*args,**kwargs):
            value.stages[stage]={}
            return response({'a.py':'retained'},'length') if stage=='implement' else response({'b.py':'finished'})
        value._opencode=Mock(side_effect=invoke)
        result=value._implement(['a.py','b.py'])
        self.assertEqual({f.path:f.content for f in result.files},{'a.py':'retained','b.py':'finished'})
        call=value._opencode.call_args
        self.assertEqual(call.args[0],'implement_inventory_retry')
        self.assertEqual(call.args[3],{'a.py':'retained'})
        self.assertEqual(call.kwargs['editable_paths'],['b.py'])
        self.assertTrue(call.kwargs['allow_create'])
        self.assertIn('one complete file per tool',call.kwargs['instruction'])

    def test_full_inventory_at_length_still_requires_one_normally_stopped_completion(self):
        value=flow()
        def invoke(stage,*args,**kwargs):
            value.stages[stage]={}
            return response({'a.py':'retained','b.py':'retained'},'length') if stage=='implement' else response({})
        value._opencode=Mock(side_effect=invoke)
        self.assertEqual(len(value._implement(['a.py','b.py']).files),2)
        self.assertFalse(value._opencode.call_args.kwargs['allow_create'])
        self.assertEqual(value._opencode.call_args.kwargs['editable_paths'],['a.py','b.py'])

    def test_unverified_or_second_length_stop_cannot_earn_another_inventory_correction(self):
        for clean,count in [(False,1),(True,2)]:
            value=flow()
            def invoke(stage,*args,**kwargs):
                value.stages[stage]={};return response({},'length',clean)
            value._opencode=Mock(side_effect=invoke)
            with self.subTest(clean=clean),self.assertRaisesRegex(ValueError,'receipt'):
                value._implement(['a.py','b.py'])
            self.assertEqual(value._opencode.call_count,count)

    def test_output_limited_repair_uses_existing_edit_correction_even_with_nonempty_changes(self):
        before=ProjectArtifact(summary='source',files=[{'path':'a.py','content':'old'},{'path':'b.py','content':'readonly'}])
        for finish in ['stop','length']:
            value=flow()
            def invoke(stage,*args,**kwargs):
                value.stages[stage]={}
                return response({'a.py':'partial'},'length') if not stage.endswith('_edit_retry') else response({'a.py':'fixed'},finish)
            value._opencode=Mock(side_effect=invoke)
            def repair():return value._patch('repair_1','repairer',before,SimpleNamespace(files=[],commands=[]),{},paths=['a.py'],compile_patch=False)
            if finish=='length':
                with self.assertRaisesRegex(ValueError,'normally stopped'):repair()
            else:
                result=repair();self.assertEqual({f.path:f.content for f in result.files},{'a.py':'fixed','b.py':'readonly'})
            self.assertEqual(value._opencode.call_count,2)
            self.assertEqual(value._opencode.call_args.args[0],'repair_1_edit_retry')
            self.assertEqual(value._opencode.call_args.kwargs['editable_paths'],['a.py'])
            self.assertEqual(value._opencode.call_args.args[3]['a.py'],'partial')


if __name__=='__main__':unittest.main()
