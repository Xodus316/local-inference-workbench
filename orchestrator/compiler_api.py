"""Approval-bound dependency evidence for compiler recovery, separate from application tests."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
import uuid

from .store import digest
from .java_api_probe import POLICY, POLICY_V2

KEY = 'whole_project_java_api_probe'


def validate_policy(value):
    if value is None:
        return None
    fields={'policy','classpath_roots'}
    if isinstance(value,dict) and value.get('policy')==POLICY_V2:
        fields.add('include_jdk')
    if (not isinstance(value, dict) or value.keys() != fields
            or value.get('policy') not in {POLICY,POLICY_V2}
            or value.get('policy')==POLICY_V2 and value.get('include_jdk') is not True
            or not isinstance(value['classpath_roots'], list)
            or not 1 <= len(value['classpath_roots']) <= 4):
        raise ValueError('Invalid approved Java API probe policy')
    for root in value['classpath_roots']:
        if (not isinstance(root, str) or not root.startswith('/') or root.startswith('//')
                or str(PurePosixPath(root)) != root or len(PurePosixPath(root).parts) < 3
                or '..' in root.split('/') or not re.fullmatch(r'/[A-Za-z0-9_./-]{1,220}', root)):
            raise ValueError('Java API probes require explicit absolute image-cache roots')
    return value


def query(files, feedback, policy):
    validate_policy(policy)
    if policy is None:
        return None
    # Use trusted, retained compiler excerpts, not arbitrary model instructions.
    execution = feedback.get('failed_execution') or {}
    diagnostics = []
    for row in execution.get('commands') or []:
        summary = row.get('sandbox_stage_results') or {}
        diagnostics.extend(f.get('diagnostic_excerpt', '') for f in summary.get('failures', [])
                           if f.get('stage') in {'gradle-compile', 'gradle-build'})
    text = '\n'.join(diagnostics)
    if not text or '.java:' not in text:
        return None
    symbols = sorted(set(re.findall(r'symbol:\s+(?:class|interface|variable|method)\s+(\w+)', text)))[:32]
    focused=policy['policy']==POLICY_V2
    if not symbols and not focused:
        return None
    from .whole_project_selection import compiler_diagnostic_selection
    selection = compiler_diagnostic_selection(files, feedback,
        byte_limit=sum(len(file.content.encode('utf8')) for file in files), file_limit=len(files))
    if selection is None or selection['pending_paths']:
        return None
    paths = set(selection['paths'])
    local = set()
    for file in files:
        package = re.search(r'(?m)^\s*package\s+([\w.]+)\s*;', file.content)
        if package:
            local.update(package[1] + '.' + name for name in re.findall(
                r'\b(?:class|interface|enum|record)\s+(\w+)', file.content))
    imports = sorted({name for file in files if file.path in paths and file.path.endswith('.java')
                      for name in re.findall(r'(?m)^\s*import\s+([\w.$]+)\s*;', file.content)
                      if name not in local and (focused or not name.startswith(('java.', 'javax.', 'jdk.', 'sun.')))})
    if not imports or len(imports) > 64:
        return None
    request={'policy':policy['policy'],'classpath_roots':list(policy['classpath_roots']),
             'imports':imports,'symbols':symbols}
    if focused:
        from .java_source_evidence import focus
        request.update(include_jdk=True,**focus(files,selection))
    return request


def execute_probe(executor, run_id, request, image, cancel, deadline):
    """Use the normal ownership/drain protocol; mount tooling and a query only."""
    from .build_sandbox import final_json_record
    uuid.UUID(run_id)
    scratch = executor.directory / run_id
    scratch.mkdir(parents=True, exist_ok=True)
    tooling = Path(tempfile.mkdtemp(prefix='api-', dir=scratch))
    script = Path(__file__).with_name('java_api_probe.py').read_bytes()
    (tooling / 'probe.py').write_bytes(script)
    (tooling / 'request.json').write_text(json.dumps(request), encoding='utf8')
    name = 'workbench-api-' + uuid.uuid4().hex
    lifecycle = scratch / ('l-' + uuid.uuid4().hex[:12] + '.lifecycle')
    args = ['docker', 'run', '--rm', '--pull', 'never', '--name', name, '--log-driver=none',
            '--network', 'none', '--read-only', '--ipc', 'none', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--pids-limit', '64', '--memory', '1024m',
            '--memory-swap', '1024m', '--cpus', '2', '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m',
            '--env', 'HOME=/tmp', '--mount', f'type=bind,source={tooling},target=/probe,readonly',
            '--entrypoint', 'python3', image, '-I', '/probe/probe.py', '/probe/request.json']
    row = executor._bounded_container(args, 65, name, cancel, deadline, image_id=image,
        directory=lifecycle, stdout_capture_bytes=100_000, create_timeout_seconds=30, cleanup_timeout_seconds=15)
    if row.get('cleanup_confirmed') is not True or type(row.get('exit_code')) is not int:
        raise ValueError('Java dependency inspection did not finish with confirmed cleanup')
    result = {'status': 'unavailable', 'commands': [row], 'image_id': image, 'request_digest': digest(request),
              'probe_sha256': hashlib.sha256(script).hexdigest()}
    if (row.get('timed_out') or row.get('cancelled') or row['exit_code'] != 0
            or (row.get('stdout_capture') or {}).get('complete') is not True):
        return result  # Preserve a settled failure before rejecting it as API evidence.
    evidence = final_json_record(row.get('stdout', ''), 'WORKBENCH_JAVA_API')
    if (not evidence or evidence.get('policy') != request.get('policy',POLICY) or evidence.get('complete_index') is not True
            or evidence.get('classpath_roots') != request['classpath_roots']):
        return result
    return {**result, 'status': 'passed', 'evidence': evidence}


def inspect_failure(flow, stage, artifact, feedback):
    policy = validate_policy(flow.bundle.get(KEY))
    request = query(artifact.files, feedback, policy)
    if request is None:
        return None
    image = flow.bundle['sandbox']['image_id']
    binding = {'image_id': image, 'request': request, 'source_digest': digest(artifact.model_dump()),
               'probe_sha256': hashlib.sha256(Path(__file__).with_name('java_api_probe.py').read_bytes()).hexdigest()}
    execute = lambda: execute_probe(flow.runtime.build_executor, flow.state['run_id'], request, image,
                                   flow.runtime.events[flow.state['run_id']], flow.runtime.deadline)
    receipt = flow.runtime._build_receipt(flow.state['run_id'], stage, binding, execute)
    if (receipt.get('status') != 'passed' or receipt.get('image_id') != image
            or receipt.get('request_digest') != digest(request) or receipt.get('probe_sha256') != binding['probe_sha256']
            or len(receipt.get('commands', [])) != 1
            or any(row.get('cleanup_confirmed') is not True or row.get('exit_code') != 0
                   or row.get('timed_out') or row.get('cancelled')
                   or (row.get('stdout_capture') or {}).get('complete') is not True for row in receipt['commands'])
            or receipt.get('evidence', {}).get('policy') != request['policy']
            or receipt['evidence'].get('complete_index') is not True
            or request['policy']==POLICY_V2 and receipt['evidence'].get('complete_jdk_index') is not True
            or receipt['evidence'].get('classpath_roots') != request['classpath_roots']
            or not set(receipt['evidence'].get('missing_imports', [])) <= set(request['imports'])):
        raise ValueError('Saved Java API evidence differs from its approved query')
    return {**receipt['evidence'], 'image_id': image, 'request_digest': digest(request),
            'symbols': request['symbols'], 'receipt_digest': digest(receipt)}


def missing_import_findings(files, evidence):
    missing = set(evidence.get('missing_imports', []))
    return [{'path': file.path, 'line': number, 'import': match[1], 'kind': 'import-absent-from-pinned-cache'}
            for file in files for number, line in enumerate(file.content.splitlines(), 1)
            if (match := re.fullmatch(r'\s*import\s+([\w.$]+)\s*;\s*', line)) and match[1] in missing]


def dependency_recovery_proof(records, evidence):
    """Spend an existing reserve only for new, verified missing-API evidence."""
    if (not evidence or evidence.get('policy') not in {POLICY,POLICY_V2}
            or evidence.get('policy')==POLICY_V2 and evidence.get('complete_jdk_index') is not True
            or evidence.get('complete_index') is not True or not evidence.get('missing_imports')
            or not evidence.get('documents')):
        return None
    symbols = set(evidence['symbols'])
    missing = [name for name in evidence['missing_imports'] if name.rsplit('.', 1)[-1] in symbols]
    if not missing:
        return None
    if any(sorted((record.get('dependency_recovery') or {}).get('missing_imports',[]))==sorted(missing)
           for record in records):
        return None
    facts = {'missing_imports': missing, 'documents': evidence['documents'], 'image_id': evidence['image_id'],
             'classpath_roots': evidence['classpath_roots']}
    fingerprint = digest(facts)
    if any((record.get('dependency_recovery') or {}).get('facts_digest') == fingerprint for record in records):
        return None
    return {'policy': evidence['policy'], 'facts_digest': fingerprint, 'missing_imports': missing,
            'receipt_digest': evidence['receipt_digest'], 'acceptance_credit': False}
