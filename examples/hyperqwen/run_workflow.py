"""Run or resume one prepared, approved HyperQwen workflow with durable pause support."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]
from orchestrator.config import Config
from orchestrator.runtime import Runtime
from orchestrator.whole_project_limits import DEFAULT_SOURCE_BYTES, WHOLE_PROJECT_TEST_BYTES
from native_adapter import Backend, Worker, ReviewerProvider, PromptTokenizer, profile
from role_routes import validate_opencode_routes


class OpenCodeRuntime(Runtime):
    def before_whole_project_worker(self, role, cancel, deadline):
        self.native_backend.activate(cancel, deadline)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'resume'])
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--pin', type=Path, required=True)
    parser.add_argument('--opencode', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--digest', required=True)
    args = parser.parse_args()
    config = Config.model_validate_json(args.config.read_text(encoding='utf-8-sig'))
    pin = json.loads(args.pin.read_text(encoding='utf8'))
    bindings = profile(pin, args.opencode, source_bytes=DEFAULT_SOURCE_BYTES)
    validate_opencode_routes(bindings)
    # Each process owns a distinct lease; previous paused leases remain auditable.
    directory = args.data.resolve() / 'backend-leases' / uuid4().hex
    directory.parent.mkdir(parents=True, exist_ok=True)
    with Backend(directory, pin) as backend:
        provider = ReviewerProvider(backend, pin, args.opencode)
        with closing(OpenCodeRuntime(args.data, config, provider)) as runtime:
            runtime.native_backend = backend
            runtime.whole_project_workers = {role: Worker(args.opencode, backend.bridge, pin, role=role,
                source_bytes=DEFAULT_SOURCE_BYTES, test_bytes=WHOLE_PROJECT_TEST_BYTES)
                for role in bindings['whole_project_workers']}
            runtime.whole_project_prompt_tokenizers = {role: PromptTokenizer(pin, role=role)
                for role in runtime.whole_project_workers}
            saved = runtime.store.get(args.run_id)['bundle']
            if (saved.get('whole_project_workers') != bindings['whole_project_workers']
                    or saved.get('backend') != provider.identity()):
                raise ValueError('Saved approval differs from the current OpenCode deployment')
            runtime.approve(args.run_id, args.digest, resume=args.action == 'resume')
            previous = None
            while True:
                row = runtime.store.get(args.run_id)
                current = {'run_id': args.run_id, 'status': row['status'],
                           'calls': len(row['calls']), 'error': row['error']}
                if current != previous:
                    print(json.dumps(current), flush=True)
                    previous = current
                if row['status'] not in {'queued', 'running', 'pausing', 'cancelling'}:
                    break
                time.sleep(.5)
    print(json.dumps({'run_id': args.run_id, 'status': row['status'], 'owned_backend_closed': True}), flush=True)


if __name__ == '__main__':
    main()
