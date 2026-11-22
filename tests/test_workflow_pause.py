"""Durable safe-boundary pause, process restart, replay and unresolved-operation barriers."""
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from orchestrator.config import Config
from orchestrator.runtime import Runtime
from orchestrator.server import Server
from orchestrator.store import Store
from orchestrator.workflow_pause import request_pause, WorkflowPaused


def configuration():
    return Config(run_timeout_seconds=None)


class RecordingProvider:
    def __init__(self, log, started=None, release=None, fail=False):
        self.log, self.started, self.release, self.fail = Path(log), started, release, fail
        self.releases = 0

    def admit(self, *args):
        pass

    def complete(self, model, settings, payload, cancel, deadline):
        with self.log.open('a', encoding='utf8') as stream:
            stream.write(model + '\n')
        if self.started is not None:
            self.started.set()
        if self.release is not None and not self.release.wait(10):
            raise TimeoutError('Fixture release missing')
        if self.fail:
            raise OSError('Unknown native completion')
        value = {'answer': 'saved answer', 'key_claims': [], 'assumptions': [],
                 'uncertainties': [], 'evidence': [], 'verification_needed': []}
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]}

    def pause(self, run_id):
        self.releases += 1
        return {'owned_backend': 'fixture', 'released': True}


def wait_status(runtime, run_id, statuses):
    for _ in range(500):
        row = runtime.store.get(run_id)
        if row['status'] in statuses:
            return row
        time.sleep(.01)
    raise AssertionError('Expected ' + repr(statuses) + ', got ' + repr(row['status']))


class PauseTests(unittest.TestCase):
    def test_pause_waits_for_native_receipt_then_resume_in_another_process_never_repeats_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            started, release = threading.Event(), threading.Event()
            provider = RecordingProvider(root / 'calls.txt', started, release)
            runtime = Runtime(root / 'data', configuration(), provider)
            try:
                row = runtime.prepare({'mode': 'balanced', 'prompt': 'Test persistence'})
                runtime.approve(row['id'], row['digest'])
                self.assertTrue(started.wait(5))
                pending = runtime.pause(row['id'])
                self.assertEqual(pending['status'], 'pausing')
                self.assertFalse(runtime.events[row['id']].is_set())
                self.assertEqual(provider.releases, 0)
                release.set()
                paused = wait_status(runtime, row['id'], {'paused', 'blocked', 'failed'})
                self.assertEqual(paused['status'], 'paused', paused)
                self.assertEqual(len(paused['calls']), 1)
                self.assertEqual(paused['calls'][0]['status'], 'terminal')
                self.assertEqual(provider.releases, 1)
                self.assertTrue(paused['pause']['cleanup']['released'])
                self.assertIsNone(paused['pause']['remaining_seconds'])
            finally:
                release.set()
                runtime.close()
            code = '''import sys,json
sys.path.insert(0, 'tests')
from test_workflow_pause import RecordingProvider,configuration,wait_status
from orchestrator.runtime import Runtime
runtime=Runtime(sys.argv[1],configuration(),RecordingProvider(sys.argv[2]))
try:
 row=runtime.store.get(sys.argv[3]); assert row['status']=='paused'
 runtime.approve(row['id'],row['digest'],resume=True)
 result=wait_status(runtime,row['id'],{'completed','failed','blocked'})
 assert result['status']=='completed',result
 assert len(result['calls'])==3,result
 print(json.dumps({'status':result['status'],'calls':len(result['calls'])}))
finally: runtime.close()
'''
            result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', code,
                str(root / 'data'), str(root / 'calls.txt'), row['id']], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len((root / 'calls.txt').read_text().splitlines()), 3)

    def test_external_pause_request_and_nested_sandbox_receipt_wait_until_outer_operation_settles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = Runtime(root / 'data', configuration(), RecordingProvider(root / 'unused'))
            started, release = threading.Event(), threading.Event()
            executions = []
            def graph(state):
                def execute():
                    executions.append('execute')
                    started.set()
                    self.assertTrue(release.wait(5))
                    runtime._guard(state['run_id'])  # Pending intent prevents an unsafe pause here.
                    return {'status': 'passed'}
                runtime._build_receipt(state['run_id'], 'build_test_preflight_0', {'source': 'fixed'}, execute)
                return {**state, 'primary': {'ok': True, 'result': {'answer': 'finished'}}}
            runtime.graph = type('Graph', (), {'invoke': staticmethod(graph)})()
            try:
                row = runtime.prepare({'mode': 'fast', 'prompt': 'Test sandbox pause'})
                runtime.approve(row['id'], row['digest'])
                self.assertTrue(started.wait(5))
                self.assertFalse(request_pause(root / 'data', row['id'])['pause_acknowledged'])
                release.set()
                paused = wait_status(runtime, row['id'], {'paused', 'failed', 'blocked'})
                self.assertEqual(paused['status'], 'paused', paused)
                self.assertFalse(runtime.store.unsettled_operations(row['id']))
                runtime.approve(row['id'], row['digest'], resume=True)
                finished = wait_status(runtime, row['id'], {'completed', 'failed'})
                self.assertEqual(finished['status'], 'completed', finished)
                self.assertEqual(executions, ['execute'])
            finally:
                release.set()
                runtime.close()

    def test_unknown_completion_blocks_pause_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            started, release = threading.Event(), threading.Event()
            runtime = Runtime(root / 'data', configuration(), RecordingProvider(root / 'calls', started, release, True))
            try:
                row = runtime.prepare({'mode': 'fast', 'prompt': 'Unknown completion'})
                runtime.approve(row['id'], row['digest'])
                self.assertTrue(started.wait(5))
                runtime.pause(row['id'])
                release.set()
                self.assertEqual(wait_status(runtime, row['id'], {'blocked', 'paused'})['status'], 'blocked')
                with self.assertRaises(ValueError):
                    runtime.approve(row['id'], row['digest'], resume=True)
            finally:
                release.set()
                runtime.close()

    def test_queued_run_can_pause_repeatedly_without_dispatch_and_cancel_is_final(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = RecordingProvider(root / 'calls')
            runtime = Runtime(root / 'data', configuration(), provider)
            try:
                row = runtime.prepare({'mode': 'fast', 'prompt': 'Queued pause'})
                for resume in (False, True, True):
                    # Hold management so the worker cannot start before the request.
                    with runtime.management:
                        runtime.approve(row['id'], row['digest'], resume=resume)
                        runtime.pause(row['id'])
                    paused = wait_status(runtime, row['id'], {'paused', 'failed', 'blocked'})
                    self.assertEqual(paused['status'], 'paused', paused)
                    self.assertEqual(paused['calls'], [])
                    self.assertEqual(runtime.pause(row['id'])['status'], 'paused')
                self.assertEqual(provider.releases, 3)
                self.assertFalse((root / 'calls').exists())
                self.assertEqual(runtime.cancel(row['id'])['status'], 'cancelled')
                with self.assertRaises(ValueError):
                    runtime.approve(row['id'], row['digest'], resume=True)
            finally:
                runtime.close()

    def test_paused_resume_refuses_configuration_drift_or_unresolved_sandbox(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = Runtime(root / 'data', configuration(), RecordingProvider(root / 'unused'))
            try:
                row = runtime.prepare({'mode': 'fast', 'prompt': 'Resume guards'})
                with runtime.management:
                    runtime.approve(row['id'], row['digest'])
                    runtime.pause(row['id'])
                self.assertEqual(wait_status(runtime, row['id'], {'paused', 'failed'})['status'], 'paused')
                original = runtime.config
                runtime.config = original.model_copy(update={'run_timeout_seconds': 60})
                with self.assertRaisesRegex(ValueError, 'Configuration changed'):
                    runtime.approve(row['id'], row['digest'], resume=True)
                runtime.config = original
                runtime.provider.identity = lambda: {'name': 'different-backend'}
                with self.assertRaisesRegex(ValueError, 'Backend identity changed'):
                    runtime.approve(row['id'], row['digest'], resume=True)
                del runtime.provider.identity
                runtime.store.save_artifact(row['id'], 'build_test_preflight_0', 'request', {'source': 'fixed'})
                with self.assertRaisesRegex(ValueError, 'not settled'):
                    runtime.approve(row['id'], row['digest'], resume=True)
                self.assertNotIn(row['id'], runtime.events)
                self.assertEqual(runtime.store.get(row['id'])['status'], 'paused')
            finally:
                runtime.close()

    def test_cleanup_failure_does_not_claim_paused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            started, release = threading.Event(), threading.Event()
            provider = RecordingProvider(root / 'calls', started, release)
            provider.pause = lambda run_id: (_ for _ in ()).throw(OSError('Cannot release backend'))
            runtime = Runtime(root / 'data', configuration(), provider)
            try:
                row = runtime.prepare({'mode': 'fast', 'prompt': 'Cleanup failure'})
                runtime.approve(row['id'], row['digest'])
                self.assertTrue(started.wait(5))
                runtime.pause(row['id']); release.set()
                blocked = wait_status(runtime, row['id'], {'blocked', 'paused'})
                self.assertEqual(blocked['status'], 'blocked')
                self.assertIn('Pause cleanup failed', blocked['error'])
            finally:
                release.set()
                runtime.close()

    def test_pause_deadline_excludes_offline_time_without_refunding_active_time(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            try:
                run_id = store.create({'run_timeout_seconds': 100})
                row = store.get(run_id)
                with patch('orchestrator.store.time.time', return_value=1000):
                    store.approve(run_id, row['digest'], 100)
                    store.update(run_id, 'running', approved=True)
                with patch('orchestrator.store.time.time', return_value=1010):
                    store.request_pause(run_id)
                    store.complete_pause(run_id, {'released': True})
                self.assertEqual(store.get(run_id)['pause']['remaining_seconds'], 90)
                with patch('orchestrator.store.time.time', return_value=1000000):
                    store.resume_pause(run_id, 100)
                    store.update(run_id, 'running')
                with patch('orchestrator.store.time.time', return_value=1000010):
                    store.request_pause(run_id)
                    store.complete_pause(run_id, {'released': True})
                self.assertEqual(store.get(run_id)['pause']['remaining_seconds'], 80)
                self.assertEqual(store.get(run_id)['approval']['approved_at'], 1000)
                with store.db:
                    store.db.execute('UPDATE workflow_pauses SET remaining_seconds=101 WHERE run_id=?', (run_id,))
                with self.assertRaisesRegex(ValueError, 'allowance differs'):
                    store.resume_pause(run_id, 100)
            finally:
                store.close()

    def test_saved_sandbox_report_requires_confirmed_owned_process_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            try:
                run_id = store.create({'run_timeout_seconds': None})
                row = store.get(run_id)
                store.approve(run_id, row['digest'], None)
                store.update(run_id, 'running', approved=True)
                store.request_pause(run_id)
                stage = 'build_test_preflight_0'
                store.save_artifact(run_id, stage, 'request', {'source': 'fixed'})
                store.save_artifact(run_id, stage, 'raw', {'commands': [{'cleanup_confirmed': False}]})
                with self.assertRaisesRegex(ValueError, 'unresolved operations'):
                    store.complete_pause(run_id, {'released': True})
                store.save_artifact(run_id, stage, 'raw', {'commands': [{'cleanup_confirmed': True}]})
                lifecycle = Path(directory) / 'build-workspaces' / run_id / 'owned.lifecycle'
                lifecycle.mkdir(parents=True)
                with self.assertRaisesRegex(ValueError, 'unresolved operations'):
                    store.complete_pause(run_id, {'released': True})
                (lifecycle / 'result.json').write_text(json.dumps({'cleanup_confirmed': True}))
                store.complete_pause(run_id, {'released': True})
                self.assertEqual(store.get(run_id)['status'], 'paused')
            finally:
                store.close()

    def test_http_pause_and_resume_require_the_saved_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            started, release = threading.Event(), threading.Event()
            runtime = Runtime(root / 'data', configuration(), RecordingProvider(root / 'calls', started, release))
            server = Server(0, runtime)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def post(path, body):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
                try:
                    connection.request('POST', path, json.dumps(body), {'Content-Type': 'application/json'})
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()
            try:
                row = runtime.prepare({'mode': 'fast', 'prompt': 'Pause API'})
                runtime.approve(row['id'], row['digest']); self.assertTrue(started.wait(5))
                path = '/api/runs/' + row['id']
                self.assertEqual(post(path + '/pause', {})[1]['status'], 'pausing')
                release.set()
                self.assertEqual(wait_status(runtime, row['id'], {'paused', 'failed'})['status'], 'paused')
                self.assertEqual(post(path + '/resume', {'digest': 'wrong'})[0], 400)
                self.assertEqual(post(path + '/resume', {'digest': row['digest']})[0], 200)
                self.assertEqual(wait_status(runtime, row['id'], {'completed', 'failed'})['status'], 'completed')
                self.assertEqual(len((root / 'calls').read_text().splitlines()), 1)
            finally:
                release.set()
                server.shutdown(); thread.join(); server.server_close(); runtime.close()


if __name__ == '__main__':
    unittest.main()
