"""Bound full TypeScript diagnostics to the same source and pinned compiler image."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
import uuid

from .frontend_diagnostic_probe import POLICY
from .store import digest

KEY = 'whole_project_frontend_diagnostics'


def validate_policy(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or value.keys() != {'policy', 'node_modules'} or value['policy'] != POLICY
            or not isinstance(value['node_modules'], str)
            or not re.fullmatch(r'/[A-Za-z0-9_./-]{1,220}/node_modules', value['node_modules'])
            or '..' in value['node_modules'].split('/')
            or str(PurePosixPath(value['node_modules'])) != value['node_modules']):
        raise ValueError('Frontend diagnostics require an approved absolute image dependency path')
    return value


def execute(executor, run_id, artifact, image, policy, cancel, deadline):
    from .build_sandbox import final_json_record, _hashes
    uuid.UUID(run_id)
    validate_policy(policy)
    scratch = executor.directory / run_id
    scratch.mkdir(parents=True, exist_ok=True)
    tooling = Path(tempfile.mkdtemp(prefix='fd-', dir=scratch))
    script = Path(__file__).with_name('frontend_diagnostic_probe.py').read_bytes()
    (tooling / 'probe.py').write_bytes(script)
    (tooling / 'query.json').write_text(json.dumps(policy), encoding='utf8')
    # Only this compiler component is mounted. No reference, tests or GPU devices.
    paths = [file.path for file in artifact.files]
    prefixes = {path.split('/', 1)[0] for path in paths}
    if len(prefixes) != 1 or any('/' not in path for path in paths):
        raise ValueError('Frontend diagnostics require one existing compiler component')
    root = next(iter(prefixes)) + '/'
    project = executor.materialize(run_id, 20_000_000 + int(digest(artifact.model_dump())[:10], 16), artifact) / root[:-1]
    before = _hashes(project)
    expected = {file.path[len(root):]: hashlib.sha256(file.content.encode('utf8')).hexdigest()
                for file in artifact.files}
    if before != expected:
        raise ValueError('Frontend diagnostic workspace differs from the approved source')
    name = 'workbench-frontend-diag-' + uuid.uuid4().hex
    lifecycle = scratch / ('l-' + uuid.uuid4().hex[:12] + '.lifecycle')
    args = ['docker', 'run', '--rm', '--pull', 'never', '--name', name, '--log-driver=none',
            '--network', 'none', '--read-only', '--ipc', 'none', '--cap-drop', 'ALL', '--security-opt',
            'no-new-privileges', '--pids-limit', '128', '--memory', '2048m', '--memory-swap', '2048m',
            '--cpus', '2', '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m', '--env', 'HOME=/tmp',
            '--mount', f'type=bind,source={tooling},target=/probe,readonly',
            '--mount', f'type=bind,source={project},target=/project,readonly',
            '--entrypoint', 'python3', image, '-I', '/probe/probe.py', '/probe/query.json']
    row = executor._bounded_container(args, 100, name, cancel, deadline, image_id=image, directory=lifecycle,
        stdout_capture_bytes=160_000, create_timeout_seconds=30, cleanup_timeout_seconds=15)
    if row.get('cleanup_confirmed') is not True or type(row.get('exit_code')) is not int:
        raise ValueError('Frontend diagnostic execution has unresolved completion or cleanup')
    source_hashes = {file.path: hashlib.sha256(file.content.encode('utf8')).hexdigest() for file in artifact.files}
    result = {'status': 'unavailable', 'commands': [row], 'image_id': image, 'source_sha256': source_hashes,
              'probe_sha256': hashlib.sha256(script).hexdigest(), 'policy': policy,
              'source_unchanged': before == _hashes(project), 'acceptance_credit': False}
    if (row.get('exit_code') != 0 or row.get('timed_out') or row.get('cancelled')
            or (row.get('stdout_capture') or {}).get('complete') is not True or not result['source_unchanged']):
        return result
    value = final_json_record(row.get('stdout', ''), 'WORKBENCH_FRONTEND_DIAGNOSTICS')
    if not value or value.get('policy') != POLICY or value.get('diagnostic_only') is not True:
        return result
    return {**result, 'status': 'passed', 'evidence': value}


def inspect_failure(flow, stage, artifact, feedback):
    policy = validate_policy(flow.bundle.get(KEY))
    if policy is None:
        return None
    failures = [failure for row in feedback['failed_execution'].get('commands', [])
                for failure in (row.get('sandbox_stage_results') or {}).get('failures', [])]
    if not any(failure.get('stage') == 'frontend-typecheck' for failure in failures):
        return None
    image = flow.bundle['sandbox']['image_id']
    script_sha = hashlib.sha256(Path(__file__).with_name('frontend_diagnostic_probe.py').read_bytes()).hexdigest()
    binding = {'image_id': image, 'policy': policy, 'source_digest': digest(artifact.model_dump()), 'probe_sha256': script_sha}
    receipt = flow.runtime._build_receipt(flow.state['run_id'], stage, binding,
        lambda: execute(flow.runtime.build_executor, flow.state['run_id'], artifact, image, policy,
                        flow.runtime.events[flow.state['run_id']], flow.runtime.deadline))
    expected = {file.path: hashlib.sha256(file.content.encode('utf8')).hexdigest() for file in artifact.files}
    rows = receipt.get('commands') or []
    if (receipt.get('status') != 'passed' or receipt.get('image_id') != image or receipt.get('policy') != policy
            or receipt.get('source_sha256') != expected or receipt.get('probe_sha256') != script_sha
            or receipt.get('source_unchanged') is not True or len(rows) != 1
            or rows[0].get('cleanup_confirmed') is not True or rows[0].get('exit_code') != 0
            or rows[0].get('timed_out') or rows[0].get('cancelled')
            or (rows[0].get('stdout_capture') or {}).get('complete') is not True):
        raise ValueError('Frontend diagnostic replay did not produce complete, source-bound evidence')
    value = receipt.get('evidence') or {}
    from .build_sandbox import final_json_record
    if (value.get('policy') != POLICY or value.get('diagnostic_only') is not True
            or value.get('acceptance_credit') is not False
            or value != final_json_record(rows[0].get('stdout', ''), 'WORKBENCH_FRONTEND_DIAGNOSTICS')
            or type(value.get('typecheck_exit_code')) is not int
            or not value.get('stdout', {}).get('complete') or not value.get('stderr', {}).get('complete')):
        raise ValueError('Frontend compiler diagnostic output is incomplete')
    for stream in ('stdout', 'stderr'):
        captured = value[stream]
        data = captured.get('text', '').encode('utf8')
        if captured.get('bytes') != len(data) or captured.get('sha256') != hashlib.sha256(data).hexdigest():
            raise ValueError('Frontend compiler diagnostic capture differs from its retained bytes')
    text = value['stdout']['text'] + '\n' + value['stderr']['text']
    if value['typecheck_exit_code'] == 0 or not re.search(r'error TS\d+:', text):
        raise ValueError('Frontend diagnostic replay disagrees with the original failed typecheck')
    return {**value, 'source_sha256': expected, 'image_id': image, 'receipt_digest': digest(receipt)}


def literal_findings(sources, feedback):
    """Project compiler-provided type alternatives; never invent library prop values."""
    from .whole_project_selection import _LOCATION
    texts = [failure.get('diagnostic_excerpt', '')
             for row in feedback.get('failed_execution', {}).get('commands', [])
             for failure in (row.get('sandbox_stage_results') or {}).get('failures', [])
             if failure.get('stage') == 'frontend-typecheck']
    details = feedback.get('compiler_diagnostics') or {}
    texts += [details.get('stdout', {}).get('text', '')]
    findings = {}
    for text in texts:
        for line in text.splitlines():
            location = _LOCATION.search(line)
            mismatch = re.search(r"""error TS2322: Type '("[^"\r\n]*")' is not assignable to type '([^'\r\n]+)'""", line)
            if not location or not mismatch:
                continue
            reported = location['path'].removeprefix('file:///').lstrip('/')
            paths = [path for path in sources if path == reported or reported.endswith('/' + path)
                     or path.endswith('/' + reported)]
            if len(paths) != 1:
                continue
            path = paths[0]
            number = int(location['line'] or location['ts_line'])
            code = sources[path].splitlines()
            if not 1 <= number <= len(code):
                continue
            try:
                literal = json.loads(mismatch[1])
            except json.JSONDecodeError:
                continue  # TypeScript may print JavaScript escapes that JSON does not support.
            if not literal or literal not in code[number - 1]:
                continue
            alternatives = sorted(set(re.findall(r'"([^"\r\n]*)"', mismatch[2])))
            findings[(path, number, literal)] = {'path': path, 'line': number, 'diagnostic_code': 'TS2322',
                'rejected_literal': literal, 'compiler_literal_alternatives': alternatives,
                'expected_type_excerpt': mismatch[2], 'alternatives_are_exhaustive': False,
                'source': code[number - 1].strip()[:500],
                'guidance': 'Use the pinned compiler evidence to choose a valid type while preserving behavior. '
                            'The expected union may also contain non-literal alternatives; do not suppress the error.'}
    return list(findings.values())[:64]
