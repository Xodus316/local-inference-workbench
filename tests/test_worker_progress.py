from copy import deepcopy
import unittest
from unittest.mock import Mock
from io import BytesIO
from threading import Event
import json

from orchestrator.worker_progress import OpenCodeProgressError,progress_guard


def tool(name='glob',output='No files found',**kw):
    return {'type':'tool_use','part':{'tool':name,'state':{'status':'completed',
        'input':{'pattern':'**/*'},'output':output,**kw}}}


class ProgressTests(unittest.TestCase):
    def test_varying_search_patterns_cannot_evade_no_result_detection(self):
        guard=progress_guard()
        for index in range(32):guard(tool(input={'pattern':f'absent/{index}/**'}))
        with self.assertRaises(OpenCodeProgressError) as raised:guard(tool(input={'pattern':'another-absent-path'}))
        self.assertEqual(raised.exception.proof['results_without_progress'],32)

    def test_transport_settles_before_the_failed_capture_closes_its_pipe(self):
        from orchestrator.project_opencode import _capture
        for failed in (False,True):
            event={**tool(),'sessionID':'isolated'}
            pipe=BytesIO((json.dumps(event)+'\n').encode());errors=[];seen=[]
            def settle():
                self.assertFalse(pipe.closed);seen.append('settled while client alive')
                if failed:raise TimeoutError('Unresolved native transport')
            _capture(pipe,[],Event(),progress_guard(deny_tools=True),errors,
                before_progress_stop=settle)
            self.assertTrue(pipe.closed)
            self.assertEqual(seen,['settled while client alive'])
            self.assertIsInstance(errors[0],OpenCodeProgressError)
            self.assertEqual(getattr(errors[0],'progress_settlement_failed',False),failed)

    def test_alternating_empty_reads_survive_compaction_but_not_the_progress_guard(self):
        guard=progress_guard()
        for _ in range(7):
            guard(tool());guard(tool('read','Only .git/'));guard({'type':'compaction'})
        guard(tool())
        with self.assertRaises(OpenCodeProgressError) as raised:guard(tool('read','Only .git/'))
        self.assertEqual(raised.exception.proof['identical_results'],8)
        self.assertFalse(raised.exception.proof['compaction_resets_guard'])

    def test_real_source_changes_reset_counts_but_noop_writes_do_not(self):
        revision=[0];guard=progress_guard(revision=lambda:deepcopy(revision))
        for _ in range(7):guard(tool())
        revision[0]+=1;guard(tool('write','written'))
        for _ in range(7):guard(tool())
        guard(tool('write','written'))
        with self.assertRaises(OpenCodeProgressError):guard(tool())

    def test_new_results_and_distinct_inputs_do_not_look_like_a_loop(self):
        guard=progress_guard()
        for index in range(100):
            guard(tool(output=f'Located source {index}'))
            guard(tool('read','Shared configuration'))
        callback=Mock();guard=progress_guard(callback)
        guard({'type':'step_start'});callback.assert_called_once()

    def test_task_only_generation_rejects_exploration_before_callbacks_or_side_effects(self):
        callback=Mock();guard=progress_guard(callback,deny_tools=True)
        with self.assertRaisesRegex(OpenCodeProgressError,'task-only'):guard(tool())
        callback.assert_not_called()
