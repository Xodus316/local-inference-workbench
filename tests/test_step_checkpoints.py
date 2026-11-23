"""A process can disappear immediately after a completed step without repeating it."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from orchestrator.runtime import Runtime
from test_workflow_pause import RecordingProvider, configuration, wait_status


class StepCheckpointTests(unittest.TestCase):
    def crash_after_primary(self, root):
        code = '''import os,sys,json
from pathlib import Path
sys.path.insert(0,'tests')
from orchestrator.runtime import Runtime
from test_workflow_pause import RecordingProvider,configuration
root=Path(sys.argv[1]);runtime=Runtime(root/'data',configuration(),RecordingProvider(root/'calls'))
step=runtime.run_step
def interrupted(stage,state,action):
 result=step(stage,state,action)
 if stage=='primary': os._exit(23)
 return result
runtime.run_step=interrupted
# Graph closures bind the step callback during construction.
from orchestrator.graph import build
runtime.graph=build(runtime)
row=runtime.prepare({'mode':'balanced','prompt':'Save every completed step'})
(root/'run.json').write_text(json.dumps({'id':row['id'],'digest':row['digest']}))
runtime.approve(row['id'],row['digest'])
runtime.queue.join()
raise AssertionError('Fixture must exit immediately after checkpoint')
'''
        child = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', code, str(root)],
                               capture_output=True, timeout=30)
        self.assertEqual(child.returncode, 23, child.stderr.decode('utf8', 'replace'))
        return json.loads((root / 'run.json').read_text())

    def test_restart_skips_completed_node_and_keeps_each_model_step(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = self.crash_after_primary(root)
            runtime = Runtime(root / 'data', configuration(), RecordingProvider(root / 'calls'))
            try:
                saved = runtime.store.get(row['id'])
                self.assertEqual(saved['status'], 'interrupted')
                self.assertEqual({(c['kind'], c['stage']) for c in saved['checkpoints']},
                                 {('native', 'primary'), ('graph', 'primary')})
                assess = runtime.assess
                def no_primary(state, stage, *args, **kwargs):
                    self.assertNotEqual(stage, 'primary', 'A completed graph node must be restored')
                    return assess(state, stage, *args, **kwargs)
                runtime.assess = no_primary
                runtime.approve(row['id'], row['digest'], resume=True)
                done = wait_status(runtime, row['id'], {'completed', 'failed', 'blocked'})
                self.assertEqual(done['status'], 'completed', done)
                self.assertEqual(len((root / 'calls').read_text().splitlines()), 3)
                self.assertEqual({c['stage'] for c in done['checkpoints'] if c['kind'] == 'graph'},
                                 {'primary', 'secondary', 'synthesis'})
            finally:
                runtime.close()

    def test_modified_checkpoint_receipt_or_engine_refuses_resume_before_dispatch(self):
        for problem in ('checkpoint', 'receipt', 'engine', 'unresolved_sandbox'):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                row = self.crash_after_primary(root)
                runtime = Runtime(root / 'data', configuration(), RecordingProvider(root / 'calls'))
                try:
                    saved = runtime.store.get(row['id'])
                    if problem == 'checkpoint':
                        checkpoint = saved['checkpoints'][0]
                        runtime.store._checkpoint_file(row['id'], checkpoint['content_sha256']).write_text('{}')
                    elif problem == 'receipt':
                        runtime.store.artifact(row['id'], 'primary', 'raw').write_text('{}')
                    elif problem == 'engine':
                        runtime.checkpoint_engine = '0' * 64
                    else:
                        runtime.store.save_artifact(row['id'], 'build_test_preflight_0', 'request', {'source': 'fixed'})
                    with self.assertRaises(ValueError):
                        runtime.approve(row['id'], row['digest'], resume=True)
                    self.assertNotIn(row['id'], runtime.events)
                    self.assertEqual(len((root / 'calls').read_text().splitlines()), 1)
                finally:
                    runtime.close()

    def test_completed_sandbox_operation_has_an_immutable_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = Runtime(root / 'data', configuration(), RecordingProvider(root / 'calls'))
            try:
                def graph(state):
                    runtime._build_receipt(state['run_id'], 'build_test_preflight_0', {'source': 'fixed'},
                                           lambda: {'status': 'passed', 'commands': [{'cleanup_confirmed': True}]})
                    return {**state, 'primary': {'result': {'answer': 'finished'}}}
                runtime.graph = type('Graph', (), {'invoke': staticmethod(graph)})()
                row = runtime.prepare({'mode': 'fast', 'prompt': 'Save the test result'})
                runtime.approve(row['id'], row['digest'])
                done = wait_status(runtime, row['id'], {'completed', 'failed'})
                self.assertEqual(done['status'], 'completed', done)
                saved = done['checkpoints'][0]
                self.assertEqual((saved['kind'], saved['stage']), ('sandbox', 'build_test_preflight_0'))
                checkpoint = runtime.store.load_step_checkpoint(row['id'], saved['kind'], saved['stage'], saved['binding'])
                self.assertEqual(set(checkpoint['receipts']),
                                 {'build_test_preflight_0.request.json', 'build_test_preflight_0.raw.json'})
                runtime.store.delete(row['id'])
                self.assertFalse((root / 'data/checkpoints' / row['id']).exists())
            finally:
                runtime.close()


if __name__ == '__main__':
    unittest.main()
