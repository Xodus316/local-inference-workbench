"""The standalone launcher must not close its backend while a pause is pending."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_workflow as runner


class PauseRunnerTests(unittest.TestCase):
    def test_resume_waits_through_pausing_and_closes_runtime_before_owned_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.json'
            pin = root / 'pin.json'
            config.write_text('{}')
            pin.write_text('{}')
            bindings = {'whole_project_workers': {'implementer': {'identity': 'frozen'}}}
            backend = MagicMock()
            provider = Mock()
            provider.identity.return_value = {'identity': 'provider'}
            runtime = Mock()
            runtime.store.get.side_effect = [
                {'bundle': {**bindings, 'backend': provider.identity()}},
                {'status': 'pausing', 'calls': [], 'error': None},
                {'status': 'paused', 'calls': [], 'error': None},
            ]
            closed = []
            runtime.close.side_effect = lambda: closed.append('runtime')
            backend.__exit__.side_effect = lambda *_: closed.append('backend')
            stdout = io.StringIO()
            argv = ['run_workflow.py', 'resume', '--config', str(config), '--pin', str(pin),
                    '--data', str(root / 'data'), '--opencode', str(root / 'opencode.exe'),
                    '--run-id', 'saved-run', '--digest', 'saved-digest']
            with patch.object(sys, 'argv', argv), redirect_stdout(stdout), \
                    patch.object(runner, 'profile', return_value=bindings), \
                    patch.object(runner, 'validate_opencode_routes'), \
                    patch.object(runner, 'Backend', return_value=backend), \
                    patch.object(runner, 'ReviewerProvider', return_value=provider), \
                    patch.object(runner, 'OpenCodeRuntime', return_value=runtime), \
                    patch.object(runner, 'Worker'), patch.object(runner, 'PromptTokenizer'), \
                    patch.object(runner.time, 'sleep') as sleep:
                runner.main()
            runtime.approve.assert_called_once_with('saved-run', 'saved-digest', resume=True)
            sleep.assert_called_once_with(.5)
            self.assertEqual(closed, ['runtime', 'backend'])
            rows = [json.loads(line) for line in stdout.getvalue().splitlines()]
            self.assertEqual([row['status'] for row in rows], ['pausing', 'paused', 'paused'])
            self.assertTrue(rows[-1]['owned_backend_closed'])


if __name__ == '__main__':
    unittest.main()
