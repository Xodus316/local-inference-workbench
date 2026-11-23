"""Immutable checkpoints for completed graph steps and settled workflow operations."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
from uuid import UUID

POLICY = 'workflow-step-checkpoint-v1'
GRAPH_STEPS = frozenset({'code_build', 'primary', 'secondary', 'research', 'decision', 'synthesis',
                         'deep', 'plan', 'patch', 'review', 'paired_primary', 'paired_secondary', 'pair_complete'})


def engine_identity():
    from .store import digest
    root = Path(__file__).resolve().parent
    return digest({p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(root.rglob('*.py'))})


class CheckpointStore:
    def checkpoint_index(self, run_id):
        with self.lock:
            return [dict(row) for row in self.db.execute(
                'SELECT kind,stage,binding,content_sha256,created FROM step_checkpoints WHERE run_id=? ORDER BY created',
                (run_id,))]

    def receipt_frontier(self, run_id, stage=None):
        from .store import STAGES
        UUID(run_id)
        folder = self.directory / 'artifacts' / run_id
        requests = [folder / (stage + '.request.json')] if stage is not None else sorted(folder.glob('*.request.json'))
        result = {}
        for request in requests:
            name = request.name.removesuffix('.request.json')
            if name not in STAGES:
                raise ValueError('Checkpoint receipt has an invalid stage')
            for suffix in ('request', 'raw'):
                path = folder / (name + '.' + suffix + '.json')
                if not path.is_file():
                    raise ValueError('Cannot checkpoint an unresolved operation')
                result[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def _checkpoint_file(self, run_id, sha):
        UUID(run_id)
        if not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha):
            raise ValueError('Invalid checkpoint content identity')
        path = self.directory / 'checkpoints' / run_id / (sha + '.json')
        if not path.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError('Checkpoint path escapes the owned data directory')
        return path

    def load_step_checkpoint(self, run_id, kind, stage, binding):
        with self.lock:
            row = self.db.execute('SELECT * FROM step_checkpoints WHERE run_id=? AND kind=? AND stage=? AND binding=?',
                                  (run_id, kind, stage, binding)).fetchone()
            if row is None:
                return None
            data = self._checkpoint_file(run_id, row['content_sha256']).read_bytes()
            if hashlib.sha256(data).hexdigest() != row['content_sha256']:
                raise ValueError('Saved workflow checkpoint content changed')
            value = json.loads(data)
            approval = self.db.execute('SELECT digest FROM approvals WHERE run_id=?', (run_id,)).fetchone()
            if (value.get('policy') != POLICY or value.get('run_id') != run_id or value.get('kind') != kind
                    or value.get('stage') != stage or value.get('binding') != binding
                    or not approval or value.get('approval_digest') != approval[0]):
                raise ValueError('Saved workflow checkpoint differs from its approval')
            for name, sha in value['receipts'].items():
                if not re.fullmatch(r'[A-Za-z0-9_]+\.(?:request|raw)\.json', name):
                    raise ValueError('Invalid checkpoint receipt path')
                path = self.directory / 'artifacts' / run_id / name
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                    raise ValueError('A workflow checkpoint receipt changed or disappeared')
            return value

    def save_step_checkpoint(self, run_id, kind, stage, binding, engine, state, receipts):
        from .store import STAGES, MAX_ARTIFACT_BYTES, _private_directory, encoded
        if kind not in {'graph', 'native', 'sandbox'} or stage not in STAGES | GRAPH_STEPS:
            raise ValueError('Invalid workflow checkpoint identity')
        with self.lock, self.db:
            prior = self.load_step_checkpoint(run_id, kind, stage, binding)
            if prior is not None:
                return prior
            self.capacity()
            approval = self.db.execute('SELECT digest FROM approvals WHERE run_id=?', (run_id,)).fetchone()
            if not approval:
                raise ValueError('Only an approved workflow may save a step checkpoint')
            value = {'policy': POLICY, 'run_id': run_id, 'kind': kind, 'stage': stage, 'binding': binding,
                     'engine_digest': engine, 'approval_digest': approval[0], 'state': state,
                     'receipts': receipts, 'created': time.time(),
                     'completion': 'node_returned' if kind == 'graph' else 'receipt_settled_before_result_application'}
            data = encoded(value).encode('utf8')
            if len(data) > MAX_ARTIFACT_BYTES:
                raise ValueError('Workflow checkpoint exceeds its storage bound')
            sha = hashlib.sha256(data).hexdigest()
            path = self._checkpoint_file(run_id, sha)
            _private_directory(path.parent)
            temporary = path.with_suffix('.tmp')
            with temporary.open('wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self.db.execute('INSERT INTO step_checkpoints VALUES(?,?,?,?,?,?)',
                            (run_id, kind, stage, binding, sha, value['created']))
            return value

    def validate_checkpoint_engine(self, run_id, engine):
        for row in self.checkpoint_index(run_id):
            value = self.load_step_checkpoint(run_id, row['kind'], row['stage'], row['binding'])
            if value['engine_digest'] != engine:
                raise ValueError('Checkpoint runtime changed; prepare an explicit continuation')
