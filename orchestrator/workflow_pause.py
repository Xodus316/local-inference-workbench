"""Durable operator pause requests, outside model-visible worktrees."""
import json
from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from uuid import UUID


class WorkflowPaused(BaseException):
    """Control flow must bypass model/verification failure-repair handlers."""


def request_path(directory, run_id):
    UUID(run_id)
    return Path(directory) / 'control' / (run_id + '.pause.json')


def write_request(directory, run_id, approval_digest):
    if not isinstance(approval_digest, str) or not re.fullmatch('[a-f0-9]{64}', approval_digest):
        raise ValueError('Pause requires the exact saved approval digest')
    path = request_path(directory, run_id)
    path.parent.mkdir(parents=True, exist_ok=True, **({'mode': 0o700} if os.name != 'nt' else {}))
    value = {'policy': 'workflow-pause-v1', 'run_id': run_id,
             'approval_digest': approval_digest, 'requested_at': time.time()}
    if path.exists():
        saved = json.loads(path.read_text(encoding='utf8'))
        if any(saved.get(key) != value[key] for key in ('policy', 'run_id', 'approval_digest')):
            raise ValueError('Saved pause request differs from this approval')
        return saved
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.pause-', suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf8', newline='\n') as stream:
            json.dump(value, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return value


def request_pause(directory, run_id):
    """An external launcher can request a pause without taking the Store owner lock."""
    database = (Path(directory).resolve() / 'runs.sqlite3').as_uri() + '?mode=ro'
    with closing(sqlite3.connect(database, uri=True)) as connection:
        row = connection.execute('SELECT status,digest,approved FROM runs WHERE id=?', (run_id,)).fetchone()
    if row is None or not row[2] or row[0] not in {'running', 'queued', 'pausing', 'paused'}:
        raise ValueError('Only an approved active or paused workflow can be paused')
    if row[0] != 'paused':
        write_request(directory, run_id, row[1])
    return {'run_id': run_id, 'status': row[0], 'pause_requested': True,
            'pause_acknowledged': row[0] == 'paused'}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['pause', 'status'])
    parser.add_argument('--data', required=True, type=Path)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    if args.action == 'pause':
        print(json.dumps(request_pause(args.data, args.run_id)))
        return
    UUID(args.run_id)
    with closing(sqlite3.connect((args.data.resolve() / 'runs.sqlite3').as_uri() + '?mode=ro', uri=True)) as connection:
        row = connection.execute('SELECT status,error FROM runs WHERE id=?', (args.run_id,)).fetchone()
    if row is None:
        raise ValueError('Unknown run ID')
    print(json.dumps({'run_id': args.run_id, 'status': row[0], 'error': row[1],
                     'pause_requested': request_path(args.data, args.run_id).exists(),
                     'pause_acknowledged': row[0] == 'paused'}))


if __name__ == '__main__':
    main()
