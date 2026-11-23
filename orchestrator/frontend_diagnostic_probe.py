"""CPU-only diagnostic replay using the pinned frontend toolchain; never a passing gate."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

POLICY = 'pinned-frontend-diagnostics-v1'
LIMIT = 64_000


def capture(data):
    return {'text': data[:LIMIT].decode('utf8', 'replace'), 'bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest(), 'complete': len(data) <= LIMIT}


def main(request):
    source = Path('/project')
    working = Path('/tmp/frontend-diagnostics')
    shutil.copytree(source, working)
    modules = Path(request['node_modules'])
    (working / 'node_modules').symlink_to(modules, target_is_directory=True)
    packages = ['typescript', 'vue-tsc', 'vue', 'vue-router', 'vuetify']
    versions = {name: json.loads((modules / name / 'package.json').read_text())['version'] for name in packages}
    config = 'tsconfig.app.json' if (working / 'tsconfig.app.json').is_file() else 'tsconfig.json'
    command = ['node', str(modules / 'vue-tsc/bin/vue-tsc.js'), '--noEmit', '--pretty', 'false',
               '--noErrorTruncation', '--project', config]
    result = subprocess.run(command, cwd=working, capture_output=True, timeout=90,
                            env={**os.environ, 'HOME': '/tmp', 'NODE_PATH': str(modules), 'NO_COLOR': '1'})
    return {'policy': POLICY, 'diagnostic_only': True, 'typecheck_exit_code': result.returncode,
            'stdout': capture(result.stdout), 'stderr': capture(result.stderr),
            'versions': versions, 'command': command[2:], 'acceptance_credit': False}


if __name__ == '__main__':
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
    print('WORKBENCH_FRONTEND_DIAGNOSTICS ' + json.dumps(main(request)), flush=True)
