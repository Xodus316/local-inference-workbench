"""General preparation is CPU-only; changed inputs and unknown execution block launch."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock,Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'tests'))
import general_workflow as runner
import test_general_coding as core


class GeneralRunnerTests(unittest.TestCase):
    def test_normal_prepare_is_inert_and_input_tamper_blocks_backend_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);scenario=core.GeneralCodingTests();runtime=scenario.runtime(root,'snake')
            config=runtime.config;executor=runtime.build_executor
            bindings={'whole_project_workers':{r:w.identity() for r,w in runtime.whole_project_workers.items()},
                'whole_project_prompt_tokenizers':{},'backend':runtime.provider.identity()}
            runtime.close()
            for name,value in [('config.json',config.model_dump()),('request.json',{'mode':'build','prompt':'Build Snake.'}),('pin.json',{})]:
                runner.save(root/name,value)
            (root/'opencode.exe').write_bytes(b'CPU fixture, never execute')
            def factory(path,settings,provider):return runner.Runtime(path,settings,provider,executor)
            with patch.object(runner.native,'profile',return_value=bindings),patch.object(runner,'validate_opencode_routes'),\
                    patch.object(runner,'GeneralRuntime',side_effect=factory),patch.object(runner.native,'Backend') as backend,\
                    redirect_stdout(io.StringIO()):
                result=runner.prepare(root/'run',root/'config.json',root/'request.json',root/'pin.json',root/'opencode.exe')
                self.assertEqual(result['model_calls'],0);backend.assert_not_called()
                manifest,bundle=runner.validate_launch(root/'run',result['manifest_sha256'])
                self.assertIn('general_coding_workflow',bundle)
                self.assertNotIn('whole_project_seed',bundle)
                (root/'request.json').write_text('{"mode":"build","prompt":"Changed"}')
                with self.assertRaisesRegex(ValueError,'inputs changed'):
                    runner.run(root/'run',result['manifest_sha256'],execute=True)
                backend.assert_not_called()
            executor.test.assert_not_called()

    def test_pause_waits_for_settlement_and_runtime_closes_before_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory);manifest={'policy':'general-coding-launch-v1','run_id':'saved','approval_digest':'approved','config':{
                'build':{'general_coding':{}}},'pin':{},'opencode':'fixture','profile':{}}
            bundle={'backend':{'fixture':True},'whole_project_workers':{},'whole_project_prompt_tokenizers':{}}
            store=Mock();store.get.return_value={'id':'saved','digest':'approved','status':'paused'}
            store.has_unknown_calls.return_value=False;store.unsettled_operations.return_value=[]
            runtime=Mock();runtime.whole_project_workers={};runtime.whole_project_prompt_tokenizers={}
            runtime.store.get.side_effect=[{'status':'pausing','calls':[],'checkpoints':[]},
                {'status':'paused','calls':[],'checkpoints':[],'result':None}]
            closed=[];runtime.close.side_effect=lambda:closed.append('runtime')
            provider=Mock();provider.identity.return_value=bundle['backend']
            def backend_factory(path,pin):
                backend=MagicMock()
                def finish(*args):
                    closed.append('backend');runner.save(path/'cleanup.json',{
                        'stopped':True,'transport_drained':True,'bridge_closed':True})
                backend.__exit__.side_effect=finish
                return backend
            with patch.object(runner,'validate_launch',return_value=(manifest,bundle)),\
                    patch.object(runner,'Store',return_value=store),patch.object(runner,'install'),\
                    patch.object(runner.native,'Backend',side_effect=backend_factory),\
                    patch.object(runner.native,'ReviewerProvider',return_value=provider),\
                    patch.object(runner,'verify_closed_lease',return_value={'stopped':True,'transport_drained':True,'bridge_closed':True}),\
                    patch.object(runner,'GeneralRuntime',return_value=runtime),patch.object(runner.time,'sleep') as sleep,\
                    redirect_stdout(io.StringIO()):
                result=runner.run(data,'manifest',resume=True,execute=True)
            self.assertEqual(result['status'],'paused');self.assertEqual(closed,['runtime','backend'])
            sleep.assert_called_once_with(5)
            runtime.approve.assert_called_once_with('saved','approved',resume=True)
            self.assertFalse((data/'delivery.json').exists())

    def test_physical_owner_and_stopped_state_are_checked_not_only_cleanup_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);owner={'container_id':'a'*64,'image_id':'sha256:'+'b'*64,
                'name':'general-fixture','ownership_label':'org.fixture.owner','ownership_nonce':'nonce'}
            closed={'stopped':True,'transport_drained':True,'bridge_closed':True}
            runner.save(path/'owner.json',owner);runner.save(path/'cleanup.json',closed)
            observed={'Id':owner['container_id'],'Image':owner['image_id'],'Name':'/'+owner['name'],
                'Config':{'Labels':{owner['ownership_label']:owner['ownership_nonce']}},'State':{'Running':False,'Pid':0}}
            with patch.object(runner.subprocess,'check_output',return_value=json.dumps([observed])):
                self.assertEqual(runner.verify_closed_lease(path),closed)
            observed['State']={'Running':True,'Pid':42}
            with patch.object(runner.subprocess,'check_output',return_value=json.dumps([observed])):
                with self.assertRaisesRegex(ValueError,'physically closed'):runner.verify_closed_lease(path)

    def test_explicit_execution_is_required(self):
        with patch.object(runner.native,'Backend') as backend:
            with self.assertRaisesRegex(ValueError,'explicit --execute'):runner.run('unused','hash')
            backend.assert_not_called()


if __name__=='__main__':unittest.main()
