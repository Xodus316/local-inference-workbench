"""Import a settled initial compiler draft without resetting its spent allowances."""
from copy import deepcopy
import hashlib
import re
from uuid import UUID

from .store import digest

KEY = 'whole_project_initial_compile_recovery'
POLICY = 'settled-initial-compile-continuation-v1'
_MIGRATION = {'fresh_project', 'backend', 'config_digest', 'whole_project_engine_digest',
              'whole_project_workers', 'whole_project_prompt_tokenizers', 'same_model_role_isolation',
              'whole_project_java_api_probe', KEY}


def contract_digest(bundle):
    # Runtime/provider implementation identities may change only in the new,
    # separately approved bundle. Task, model settings, verifier and budgets may not.
    return digest({k: v for k, v in bundle.items() if k not in _MIGRATION})


def pack(row, requests, raw, target):
    from .whole_project import WholeProjectPlan, whole_project_types, _stopped_opencode_result
    source = row['bundle']
    output = (row.get('result') or {}).get('output') or {}
    if (row.get('status') != 'review_required' or not row.get('approved')
            or row.get('digest') != digest(source) or (row.get('approval') or {}).get('digest') != row['digest']
            or output.get('suite') is not None or output.get('repair_attempts') != 0
            or not re.fullmatch(r'build_compile_checkpoint_[1-4]_[0-4]', (output.get('error') or {}).get('stage', ''))
            or contract_digest(source) != contract_digest(target)):
        raise ValueError('Compile continuation requires a settled initial compile failure and unchanged contracts')
    for field in ('model', 'context', 'thinking', 'model_manifest_sha256', 'backend_identity', 'sampling'):
        if source['backend'].get(field) != target['backend'].get(field):
            raise ValueError('Compile continuation cannot change the pinned model or generation profile')
    plan = WholeProjectPlan.model_validate(output['plan'])
    if 'build_plan' in raw and raw['build_plan'].get('value') != plan.model_dump():
        raise ValueError('Retained compiler plan differs from its completed planner receipt')
    artifact_type, _ = whole_project_types(source)
    files, receipts = {}, {}
    for call in row['calls']:
        stage = call['stage']
        if call.get('status') != 'terminal' or stage not in raw or stage not in requests:
            raise ValueError('Compile continuation cannot import unresolved native execution')
        value, request = raw[stage], requests[stage]
        if not _stopped_opencode_result(value):
            raise ValueError('Compile continuation requires stopped worker and scratch-cleanup receipts')
        receipts[stage] = {'request_digest': digest(request), 'raw_digest': digest(value)}
        if stage == 'build_plan':
            continue
        changes = value.get('files')
        if (stage not in {'implement', 'implement_inventory_retry'}
                and not re.fullmatch(r'build_compile_repair_[1-4]_[1-4](?:_edit_retry)?', stage)):
            raise ValueError('Only initial implementation and compiler corrections may supply this draft')
        if (not isinstance(changes, dict) or not changes or not changes.keys() <= set(plan.implementation_files)
                or not changes.keys() <= set(request.get('editable_paths', []))):
            raise ValueError('Retained compiler edits differ from their journaled file scope')
        files.update(changes)
    if set(files) != set(plan.implementation_files):
        raise ValueError('Retained implementation inventory is incomplete')
    artifact = artifact_type(summary='Receipt-reconstructed compiler continuation',
                             files=[{'path': path, 'content': text} for path, text in sorted(files.items())])
    evidence = output['verification']['whole_project']
    history = [deepcopy(value['repair_attempt_evidence']) for value in row['result'].get('stages', {}).values()
               if isinstance(value, dict) and 'repair_attempt_evidence' in value]
    for attempt in history:
        stage = attempt['stage']
        before = requests[stage].get('visible_files')
        if not isinstance(before, dict):
            raise ValueError('A retained compiler correction has no source binding')
        changes = {**raw[stage]['files'], **raw.get(stage + '_edit_retry', {}).get('files', {})}
        after = {**before, **{path: digest(text) for path, text in changes.items()}}
        attempt.update(before_source_digest=digest(before), after_source_digest=digest(after))
    from .whole_project_checkpoint import binding_digest
    value = {'policy': POLICY, 'source_run_id': row['id'], 'source_approval_digest': row['digest'],
             'source_contract_digest': contract_digest(source), 'target_binding_digest': binding_digest(target),
             'plan': plan.model_dump(), 'artifact': artifact.model_dump(), 'artifact_digest': digest(artifact.model_dump()),
             'counters': deepcopy(evidence['budget_counters']),
             'compile_repair_attempts': output['compile_repair_attempts'],
             'compile_checks': deepcopy(output['verification']['compile_checkpoints']),
             'repair_history': history, 'source_receipts': receipts}
    validate(value, target)
    return value


def validate(value, bundle):
    from .whole_project import WholeProjectPlan, whole_project_types
    from .whole_project_checkpoint import binding_digest
    from .compile_progress import additional_repairs
    from .stage_recovery import validate_costs
    from .project_build import compile_checkpoint_status
    fields = {'policy', 'source_run_id', 'source_approval_digest', 'source_contract_digest', 'target_binding_digest',
              'plan', 'artifact', 'artifact_digest', 'counters', 'compile_repair_attempts', 'compile_checks',
              'repair_history', 'source_receipts'}
    if (not isinstance(value, dict) or value.keys() != fields or value['policy'] != POLICY
            or value['source_contract_digest'] != contract_digest(bundle)
            or value['target_binding_digest'] != binding_digest(bundle)
            or 'fresh_project' in bundle or 'whole_project_seed' in bundle or 'whole_project_checkpoint' in bundle):
        raise ValueError('Compiler continuation differs from its newly approved binding')
    UUID(value['source_run_id'])
    if not re.fullmatch('[a-f0-9]{64}', value['source_approval_digest']):
        raise ValueError('Compiler continuation requires its original approval digest')
    artifact_type, _ = whole_project_types(bundle)
    artifact = artifact_type.model_validate(value['artifact'])
    plan = WholeProjectPlan.model_validate(value['plan'])
    if (set(plan.implementation_files) != {file.path for file in artifact.files}
            or digest(artifact.model_dump()) != value['artifact_digest']):
        raise ValueError('Retained compiler source or inventory changed')
    counters = value['counters']
    if (not isinstance(counters, dict) or any(not isinstance(key, str) or type(count) is not int or count < 0
                                           for key, count in counters.items())):
        raise ValueError('Invalid retained compiler counters')
    validate_costs(bundle, counters)
    total = 0
    for key, count in counters.items():
        match = re.fullmatch(r'whole_compile_initial_([1-4])', key)
        if match:
            if count > bundle['build_contract']['max_compile_repairs'] + additional_repairs(bundle):
                raise ValueError('Retained compiler allowance exceeds its original bound')
            total += count
        elif key not in {'recovery_request', 'recovery_diagnosis'} and not re.fullmatch(r'recovery_issue_[a-f0-9]{32}', key):
            raise ValueError('Initial compiler continuation has unsupported spent counters')
    if type(value['compile_repair_attempts']) is not int or total != value['compile_repair_attempts']:
        raise ValueError('Compiler continuation must retain every spent repair')
    records = value['compile_checks']
    if not isinstance(records, list) or not 1 <= len(records) <= 20:
        raise ValueError('Compiler continuation requires bounded settled compiler records')
    latest = {}
    for record in records:
        slot = record.get('group_index', -1) + 1
        checkpoints = bundle['project_environment']['compile_checkpoints']
        if not 1 <= slot <= len(checkpoints):
            raise ValueError('Retained compiler checkpoint is outside the approved environment')
        checkpoint = checkpoints[slot - 1]
        execution, summary = record['execution'], record['stage_results']
        rows = execution.get('commands') or []
        if (len(rows) != 1 or record['stage'] != f'build_compile_checkpoint_{slot}_{record["attempt"]}'
                or compile_checkpoint_status(execution, summary, checkpoint,
                    bundle['project_environment']['verification']['policy'], source_hashes=rows[0].get('source_sha256'),
                    image_id=bundle['sandbox']['image_id']) not in {'failed', 'passed'}):
            raise ValueError('Compiler continuation has unresolved or changed execution evidence')
        latest[slot] = record
    for slot, record in latest.items():
        expected = {file.path: hashlib.sha256(file.content.encode('utf8')).hexdigest() for file in artifact.files
                    if file.path.startswith(bundle['project_environment']['compile_checkpoints'][slot - 1]['source_root'] + '/')}
        if (record['attempt'] != counters.get(f'whole_compile_initial_{slot}', 0)
                or record['execution']['commands'][0]['source_sha256'] != expected):
            raise ValueError('Latest compiler receipt does not bind the recovered source and spent attempts')
    return plan, artifact


def restore(flow):
    value = flow.bundle[KEY]
    plan, artifact = validate(value, flow.bundle)
    flow.counters.update(value['counters'])
    flow.output.update(plan=plan.model_dump(), artifact=artifact.model_dump(),
                       compile_repair_attempts=value['compile_repair_attempts'])
    flow.output['verification']['compile_checkpoints'] = deepcopy(value['compile_checks'])
    flow._repair_history = deepcopy(value['repair_history'])
    flow.evidence.update(budget_counters=dict(flow.counters), compiler_continuation={
        'source_run_id': value['source_run_id'], 'source_approval_digest': value['source_approval_digest'],
        'checkpoint_digest': digest(value), 'prior_acceptance_credit': False})
    return plan, artifact
