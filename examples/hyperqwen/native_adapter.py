"""Explicit same-weight Qwen roles for a fresh public-runtime workflow.

Import/profile construction never starts a server or allocates a GPU. Every
native request is bound to its literal role, stage, xhigh budget and session.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import hashlib
import json
import math
import re
import secrets
import sys
import threading
import time

from native_import_guard import ROOT, PUBLIC_ROOT, assert_public_runtime
assert_public_runtime()
from native_transport import HyperQwenOpenCodeBridge, HyperQwenBridgeError
from native_launch_profile import CONTEXT, MODEL, IMAGE, MODEL_DIR, MODELS_MOUNT, docker
from native_backend import OwnedBackend, _validated_pin, _stopped
from native_worker_support import HyperQwenWholeProjectWorker, NATIVE_SAMPLING
from orchestrator.project_opencode import OpenCodeOutputLimitError
from orchestrator.project_opencode_whole import (OpenCodeProjectWorker, OpenCodeSettledResponseError,
    OpenCodeStructuredOutputError, _before_dispatch, _STRUCTURED_SYSTEM_PREFIX)
from orchestrator.store import digest, encoded
from orchestrator.whole_project_limits import WHOLE_PROJECT_COMPLETION_TOKENS, WHOLE_PROJECT_TEST_BYTES, DEFAULT_SOURCE_BYTES
from orchestrator.whole_project_selection import INLINE_SELECTION_POLICY, COMPACTED_DECISION_STEPS
from orchestrator.whole_project_smoke import INLINE_SMOKE_POLICY, SMOKE_STAGES
from orchestrator.whole_project_test_author import AUTHOR_RESTART_STAGE, INLINE_AUTHOR_RESTART_POLICY
from orchestrator.whole_project_test_repair import DIAGNOSIS_STAGE, METHOD_PATCH_STAGE

POLICY = 'hyperqwen-fresh-public-xhigh-roles-v3'
ISOLATION = 'fresh-role-session-v1'
ROLES = ('planner', 'implementer', 'repairer', 'test_author')
REVIEW_POLICY = 'native-isolated-code-review-v1'
DIAGNOSIS_POLICY = 'inline-reference-diagnosis-v2'
PATCH_POLICY = 'inline-reference-method-patch-v4'
INLINE_PREFIX = ('Use only the supplied inline task, diagnostics and current source. No tools are available. '
                 'Return one final JSON object now; do not explore a worktree.\n\n')
SAMPLING = {**NATIVE_SAMPLING, 'min_p': 0.0, 'presence_penalty': 0.0,
            'frequency_penalty': 0.0, 'repetition_penalty': 1.0}


PLANNER_SYSTEM = (
    'Plan a fresh project in an empty workspace from the supplied brief and constraints. '
    'Application paths are proposed files. Do not explore the filesystem or implement source. '
    'Return the complete requested plan JSON in the final answer.')
PLANNER_PROMPT = 'Return the structured plan now from this task; later stages create the files.'

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native_source_identity():
    names = ('native_adapter.py', 'native_backend.py', 'native_import_guard.py',
             'native_launch_profile.py', 'native_transport.py', 'native_worker_support.py')
    return {name: sha(Path(__file__).parent / name) for name in names}


def generation_policy(stage, role, tool_policy=None):
    if not isinstance(stage, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', stage):
        raise ValueError('Require a canonical workflow stage')
    thinking = 16384
    if role == 'reviewer':
        valid = (tool_policy == REVIEW_POLICY and
                 re.fullmatch(r'build_code_review_\d+_chunk_[1-9]\d*(?:_citation_retry)?', stage))
    elif role == 'planner':
        valid = tool_policy is None and stage in {'build_plan', 'build_plan_retry'}
        thinking = 8192 if stage == 'build_plan' else 4096
    elif tool_policy == DIAGNOSIS_POLICY:
        valid = role == 'test_author' and DIAGNOSIS_STAGE.fullmatch(stage)
        thinking = 4096
    elif tool_policy == PATCH_POLICY:
        valid = role == 'test_author' and METHOD_PATCH_STAGE.fullmatch(stage)
    elif tool_policy == INLINE_SELECTION_POLICY:
        valid = role == 'repairer' and stage.endswith('_select')
    elif tool_policy == INLINE_SMOKE_POLICY:
        valid = role == 'implementer' and stage in SMOKE_STAGES
    elif tool_policy == INLINE_AUTHOR_RESTART_POLICY:
        valid = role == 'test_author' and stage == AUTHOR_RESTART_STAGE
    else:
        valid = tool_policy is None and (
            role in {'implementer', 'repairer'}
            or role == 'test_author' and re.fullmatch(r'build_tests(?:_retry|_(?:syntax|fixture|coverage|strengthen)_[1-9]\d*)?', stage))
    if not valid:
        raise ValueError('Unsupported role/stage/tool-policy combination')
    return {'policy': POLICY, 'role': role, 'tool_policy': tool_policy,
            'thinking_token_budget': thinking, 'max_completion_tokens': WHOLE_PROJECT_COMPLETION_TOKENS}


def transport_schema(original):
    """Work around the pinned XGrammar string bug; callers retain strict validation."""
    value, removed = deepcopy(original), []
    def visit(node, path=''):
        if isinstance(node, dict):
            if node.get('type') == 'string':
                for key in ('minLength', 'maxLength', 'pattern'):
                    if key in node:
                        removed.append({'path': path + '/' + key, 'value': node.pop(key)})
            for key, child in node.items():
                visit(child, path + '/' + key.replace('~', '~0').replace('/', '~1'))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, path + '/' + str(index))
    visit(value)
    return value, removed


def is_opencode_compaction(body):
    """Recognize the pinned CLI's separate, tool-free context summary request."""
    if (body.get('response_format') is not None or body.get('tools') not in (None, [])
            or body.get('tool_choice') not in (None, 'none')):
        return False
    messages = body['messages']
    if any(m.get('role') not in {'system', 'user'} or m.get('tool_calls') for m in messages):
        return False
    user = [m.get('content') for m in messages if m.get('role') == 'user']
    return (len(user) == 1 and isinstance(user[0], str)
            and user[0].startswith('Here is the conversation so far:\n\n<conversation>\n')
            and '\n</conversation>' in user[0] and '\n<template>\n' in user[0]
            and any(isinstance(m.get('content'), str)
                    and m['content'].startswith('You are a context summarization agent.')
                    for m in messages if m.get('role') == 'system'))


class Bridge(HyperQwenOpenCodeBridge):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if (self.model, self.context) != (MODEL, CONTEXT):
            raise ValueError('Require the pinned Qwen model and context')
        self._role_profile = None
        self._identity.update(e2e_policy=POLICY, reasoning_effort='xhigh',
                              adapter_sha256=sha(__file__), session_isolation=ISOLATION,
                              native_source_sha256=native_source_identity())

    @contextmanager
    def role_profile(self, stage, role, tool_policy=None):
        policy = generation_policy(stage, role, tool_policy)
        session = str(uuid4())
        with self._dispatch:
            if self._turn_profile is not None or self._failure or self._stopping.is_set():
                raise HyperQwenBridgeError('The bridge has an active, failed or unsettled turn')
            # Tool writers must retain their real tool protocol. Inline/reviewer
            # calls use the base bridge's strict no-tools JSON schema checks.
            self._turn_profile = (stage, 'xhigh', policy if tool_policy or role == 'planner' else None)
            self._role_profile = (stage, deepcopy(policy), session)
        try:
            yield session
        finally:
            with self._dispatch:
                self._turn_profile = None
                self._role_profile = None

    def _forward(self, handler, body, result, started):
        active = self._role_profile
        if not active or active[0] != result.get('stage'):
            raise HyperQwenBridgeError('Unprofiled native requests are forbidden')
        stage, policy, session = active
        expected = generation_policy(stage, policy['role'], policy['tool_policy'])
        if (policy != expected or result.get('reasoning_effort') != 'xhigh'
                or body.get('reasoning_effort') != 'xhigh'
                or body.get('chat_template_kwargs') != {'enable_thinking': True, 'reasoning_effort': 'xhigh'}):
            raise HyperQwenBridgeError('The native request differs from its xhigh profile')
        for key, value in SAMPLING.items():
            if body.get(key, value) != value:
                raise HyperQwenBridgeError('The native sampling profile changed')
            body[key] = value
        for key in ('max_tokens', 'max_completion_tokens'):
            if key in body and (type(body[key]) is not int or not 0 < body[key] <= policy['max_completion_tokens']):
                raise HyperQwenBridgeError('Native completion cap differs from the profile')
            body.pop(key, None)
        body['max_tokens'] = policy['max_completion_tokens']
        body['thinking_token_budget'] = policy['thinking_token_budget']
        result.update(role=policy['role'], session_id=session, session_isolation=ISOLATION,
                      dispatch_sequence=self.request_count + 1,
                      generation_policy=deepcopy(policy), generation_policy_verified=False,
                      native_model_manifest_sha256=self._identity['model_manifest_sha256'],
                      wire_messages_digest=digest(body['messages']), sampling=deepcopy(SAMPLING))
        if is_opencode_compaction(body):
            result['auxiliary_request'] = 'opencode-compaction'
        if body.get('response_format') is not None:
            material = (encoded(body['response_format']) + '\n').encode('utf8')
            folder = self.directory / 'wire-schemas'; folder.mkdir(exist_ok=True)
            path = folder / (result['request_id'] + '.json')
            with path.open('xb') as stream:
                stream.write(material)
            result['wire_response_format'] = {'path': 'wire-schemas/' + path.name,
                'sha256': hashlib.sha256(material).hexdigest(),
                'content_digest': digest(body['response_format'])}
        return super()._forward(handler, body, result, started)


class Backend(OwnedBackend):
    def __init__(self, directory, pin, *, port=18031):
        assert_public_runtime()
        super().__init__(directory, pin, port=port)
        self.bridge = Bridge(f'http://127.0.0.1:{port}', secrets.token_urlsafe(32),
                             model=MODEL, context=CONTEXT, directory=self.directory/'requests',
                             backend_identity=self.pin)
        self.bridge.endpoint = 'http://127.0.0.1:1'

    @staticmethod
    def preview(pin):
        assert_public_runtime()
        return _validated_pin(pin)


def native_receipts(bridge, stage):
    bridge.wait_cleanup()
    rows = []
    for path in bridge.directory.glob('*.json'):
        value = json.loads(path.read_text(encoding='utf8'))
        if value.get('stage') == stage:
            rows.append({'path': path.name, 'sha256': sha(path), 'receipt': value})
    return sorted(rows, key=lambda row: row['receipt'].get('dispatch_sequence', -1))


def completed_rows(rows):
    return bool(rows) and all(row['receipt'].get('status') == 'completed'
        and row['receipt'].get('done_event') is True
        and row['receipt'].get('generation_policy_verified') is True for row in rows)


def validate_session_schema(rows, directory, schema, *, allow_compaction):
    for index, row in enumerate(rows):
        native = row['receipt']
        if (allow_compaction and index < len(rows) - 1
                and native.get('auxiliary_request') == 'opencode-compaction'
                and native.get('finish_reason') == 'stop' and 'wire_response_format' not in native):
            continue
        wire = native.get('wire_response_format') or {}
        path = directory / wire.get('path', '')
        try:
            if not path.is_file() or not path.resolve().is_relative_to(directory.resolve()):
                return False
            material = path.read_bytes()
            actual = json.loads(material)
        except (OSError, ValueError):
            return False
        if not isinstance(actual, dict) or hashlib.sha256(material).hexdigest() != wire.get('sha256'):
            return False
        config = actual.get('json_schema')
        if (actual.get('type') != 'json_schema' or not isinstance(config, dict) or config.get('strict') is not True
                or config.get('schema') != schema or digest(actual) != wire.get('content_digest')):
            return False
    return True


def settled_response_proof(rows, session, policy, scratch_cleanup):
    if (scratch_cleanup is not True or not completed_rows(rows)
            or any(row['receipt'].get('session_id') != session or row['receipt'].get('generation_policy') != policy
                   for row in rows)):
        return None
    proof = {'session_id': session, 'native_receipts_digest': digest(rows),
             'completed_requests': len(rows), 'scratch_cleanup_confirmed': True}
    compactions = sum(row['receipt'].get('auxiliary_request') == 'opencode-compaction' for row in rows)
    if compactions:
        proof.update(compaction_requests=compactions, primary_requests=len(rows) - compactions)
    return proof


def decision_rows(rows, *, finish='stop'):
    """Compaction may surround one decision; it cannot substitute for the decision."""
    if not completed_rows(rows) or len(rows) > COMPACTED_DECISION_STEPS:
        return False
    primary = [row for row in rows if row['receipt'].get('auxiliary_request') != 'opencode-compaction']
    return (len(primary) == 1 and primary[0] is rows[-1]
        and primary[0]['receipt'].get('finish_reason') == finish
        and all(row['receipt'].get('finish_reason') == 'stop' and 'wire_response_format' not in row['receipt']
                for row in rows[:-1]))


class Worker(HyperQwenWholeProjectWorker):
    def __init__(self, executable, bridge, backend_identity, *, role, context=CONTEXT,
                 thinking='xhigh', source_bytes, test_bytes):
        self.bridge, self._backend_identity = bridge, _validated_pin(backend_identity)
        if role not in (*ROLES, 'reviewer') or thinking != 'xhigh' or context != CONTEXT or not isinstance(bridge, Bridge):
            raise ValueError('Require an explicit isolated xhigh Qwen role')
        if (not isinstance(bridge.api_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,}', bridge.api_key)
                or bridge.api_key in json.dumps(self._backend_identity)):
            raise ValueError('Require a private per-run bridge credential')
        self._manifest = self._backend_identity['model_manifest_sha256']
        self._initializing = True
        OpenCodeProjectWorker.__init__(self, executable, bridge.endpoint, MODEL, role=role,
            context=context, thinking=thinking, source_bytes=source_bytes, test_bytes=test_bytes,
            completion_token_ceiling=WHOLE_PROJECT_COMPLETION_TOKENS)
        self._initializing = False

    def identity(self):
        value = super().identity()
        value.update(endpoint='owned-authenticated-loopback-per-run', e2e_policy=POLICY,
                     session_isolation=ISOLATION, adapter_sha256=sha(__file__),
                     sampling=deepcopy(SAMPLING),
                     native_source_sha256=native_source_identity())
        return value

    def _model_metadata(self, context, effort):
        if context != CONTEXT or effort != 'xhigh':
            raise ValueError('The approved role context/effort changed')
        if not self._initializing:
            self._verify_backend(self.bridge.identity())
        return self._manifest

    def _verify_backend(self, observed):
        super()._verify_backend(observed)
        if (observed.get('e2e_policy') != POLICY or observed.get('adapter_sha256') != sha(__file__)
                or observed.get('session_isolation') != ISOLATION or observed.get('reasoning_effort') != 'xhigh'
                or observed.get('native_source_sha256') != native_source_identity()):
            raise ValueError('The native backend role binding changed')

    def _project_environment(self, state, settings, *args, **kwargs):
        if any(settings.get(key) not in (None, value) for key, value in SAMPLING.items()):
            raise ValueError('The frozen sampling settings changed')
        return super()._project_environment(state, {**settings, **SAMPLING}, *args, **kwargs)

    def _session(self, *, stage, **arguments):
        with _before_dispatch():
            policy = generation_policy(stage, self.role, arguments.get('tool_policy'))
            original_schema = arguments.get('schema')
            removed = []
            if original_schema is not None:
                arguments['schema'], removed = transport_schema(original_schema)
        if self.role == 'planner':
            arguments['system'] = PLANNER_SYSTEM + '\n\n' + arguments['system']
            arguments['prompt'] = PLANNER_PROMPT + '\n\n' + arguments['prompt']
        with self.bridge.role_profile(stage, self.role, arguments.get('tool_policy')) as session:
            with _before_dispatch():
                self.bridge.ready()
                if native_receipts(self.bridge, stage):
                    raise ValueError('A native stage cannot be silently dispatched twice')
            restartable = ((self.role == 'test_author' and stage in {'build_tests', AUTHOR_RESTART_STAGE})
                           or arguments.get('tool_policy') == INLINE_SELECTION_POLICY)
            try:
                result = OpenCodeProjectWorker._session(self, stage=stage, **arguments)
            except BaseException as error:
                # Physical release alone never proves native completion.
                try:
                    rows = native_receipts(self.bridge, stage)
                    known = (getattr(error, 'not_dispatched', False) if not rows else
                             all(row['receipt'].get('upstream_dispatched') is False or
                                 row['receipt'].get('done_event') is True for row in rows))
                    if ((self.role == 'planner' or arguments.get('tool_policy') == INLINE_SELECTION_POLICY)
                            and isinstance(error, OpenCodeOutputLimitError)):
                        known = (1 <= error.steps <= COMPACTED_DECISION_STEPS and decision_rows(rows, finish='length')
                                 and all(row['receipt'].get('session_id') == session
                                     and row['receipt'].get('generation_policy') == policy for row in rows))
                except BaseException:
                    known = False
                if not known:
                    for key in ('backend_cleanup_confirmed', 'not_dispatched'):
                        if hasattr(error, key): delattr(error, key)
                    error.diagnostics = {'native_completion': 'unknown', 'automatic_retry': False}
                elif restartable and getattr(error, 'backend_cleanup_confirmed', False):
                    proof = settled_response_proof(rows, session, policy,
                        getattr(error, 'scratch_cleanup', {}).get('scratch_cleanup_confirmed'))
                    if (self.role == 'test_author' and proof and isinstance(error, OpenCodeStructuredOutputError)
                            and rows[-1]['receipt'].get('finish_reason') == 'stop'):
                        raise OpenCodeSettledResponseError('structured-output-invalid', proof) from error
                    if proof and isinstance(error, OpenCodeOutputLimitError) and rows[-1]['receipt'].get('finish_reason') == 'length':
                        raise OpenCodeSettledResponseError('structured-output-limit', proof) from error
                raise
            rows = native_receipts(self.bridge, stage)
            if (not completed_rows(rows) or ((policy['tool_policy'] is not None or self.role == 'planner') and not decision_rows(rows))
                    or rows[-1]['receipt'].get('finish_reason') != 'stop'):
                raise ValueError('The stage lacks complete normally stopped native evidence')
            for row in rows:
                native = row['receipt']
                if native.get('session_id') != session or native.get('generation_policy') != policy:
                    raise ValueError('Native session or role profile differs')
            schema = arguments.get('schema')
            if schema is not None and not validate_session_schema(
                    rows, self.bridge.directory, schema,
                    allow_compaction=True):
                proof = settled_response_proof(rows, session, policy, result['receipt'].get('scratch_cleanup_confirmed'))
                if self.role == 'test_author' and restartable and proof:
                    raise OpenCodeSettledResponseError('unverified-wire-format', proof)
                raise ValueError('Actual native response schema differs')
            result['receipt'].update(backend_requests=len(rows), native_receipts=rows,
                generation_policy=policy, session_id=session, session_isolation=ISOLATION,
                original_schema_digest=digest(original_schema) if original_schema is not None else None,
                transport_schema_override={'policy':'xgrammar-string-bounds-runtime-v2','removed_keywords':removed})
            return result


TOKEN_COUNT_SCRIPT = r'''
import hashlib,json,pathlib,sys
from collections.abc import Mapping
from transformers import AutoTokenizer
root=pathlib.Path(sys.argv[1]); request=json.load(sys.stdin)
for name,expected in request['metadata'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
t=AutoTokenizer.from_pretrained(str(root),local_files_only=True,trust_remote_code=False)
counts=[]
for messages in request['variants']:
    tokens=t.apply_chat_template(messages,tokenize=True,add_generation_prompt=True,
                                 enable_thinking=True,reasoning_effort='xhigh')
    if isinstance(tokens,Mapping): tokens=tokens['input_ids']
    if isinstance(tokens,list) and len(tokens)==1 and isinstance(tokens[0],list): tokens=tokens[0]
    assert isinstance(tokens,list) and tokens and all(type(x) is int for x in tokens)
    counts.append(len(tokens))
print(json.dumps({'tokens':max(counts),'variants':counts}))
'''



class PromptTokenizer:
    def __init__(self, backend_identity, *, role='test_author', direct=False):
        self.pin = _validated_pin(backend_identity)
        if role not in (*ROLES, 'reviewer'):
            raise ValueError('Unapproved prompt-tokenizer role')
        required = {'tokenizer.json', 'tokenizer_config.json', 'chat_template.jinja', 'config.json'}
        metadata = self.pin['inventory']['metadata_sha256']
        if not required <= metadata.keys():
            raise ValueError('Missing pinned tokenizer metadata')
        self.metadata = {name: metadata[name] for name in sorted(required)}
        self.direct, self.role = direct, role
        self._identity = {'policy': POLICY+'-cpu-tokenizer', 'thinking': 'xhigh', 'role': role,
            'image': IMAGE, 'model_root': MODEL_DIR, 'model_manifest_sha256': self.pin['model_manifest_sha256'],
            'metadata_sha256': self.metadata,
            'material_policy': 'direct-messages-plus-schema' if direct else 'maximum-public-role-framing-plus-schema',
            'counter_sha256': hashlib.sha256(TOKEN_COUNT_SCRIPT.encode()).hexdigest(), 'adapter_sha256': sha(__file__)}

    def identity(self):
        return deepcopy(self._identity)

    def __call__(self, payload):
        messages = deepcopy(payload.get('messages'))
        if (not isinstance(messages, list) or len(messages) != 2
                or not all(isinstance(row, dict) for row in messages)
                or [row.get('role') for row in messages] != ['system', 'user']
                or not all(isinstance(row.get('content'), str) for row in messages)
                or not isinstance(payload.get('schema'), dict)):
            raise ValueError('Require fresh system/user messages and complete response schema')
        variants = []
        prefixes = ('',) if self.direct else (_STRUCTURED_SYSTEM_PREFIX, INLINE_PREFIX)
        for prefix in prefixes:
            current = deepcopy(messages)
            current[0]['content'] = (prefix + current[0]['content']).strip()
            if self.role == 'planner' and not self.direct:
                current[0]['content'] = PLANNER_SYSTEM + '\n\n' + current[0]['content']
                current[1]['content'] = PLANNER_PROMPT + '\n\n' + current[1]['content']
            current[0]['content'] += '\nResponse JSON schema:\n' + encoded(payload['schema'])
            variants.append(current)
        value = json.loads(docker('run','--rm','--network','none','--read-only','--mount',MODELS_MOUNT,
            '--tmpfs','/tmp:rw,nosuid,size=256m','--env','HF_HUB_OFFLINE=1','--env','TRANSFORMERS_OFFLINE=1',
            '--env','HF_HOME=/tmp/hf','--entrypoint','/app/venv/bin/python','-i',IMAGE,
            '-c',TOKEN_COUNT_SCRIPT,MODEL_DIR,input_text=json.dumps({'variants':variants,'metadata':self.metadata}),timeout=60))
        if type(value.get('tokens')) is not int or value['tokens'] <= 0:
            raise ValueError('CPU tokenizer returned an invalid count')
        return value['tokens']


class ReviewerProvider:
    """One isolated OpenCode CLI review per admitted workflow stage."""
    def __init__(self, backend, pin, opencode_executable, *, source_bytes=DEFAULT_SOURCE_BYTES):
        self.backend, self.pin = backend, _validated_pin(pin)
        self.tokenizer = PromptTokenizer(pin, role='reviewer')
        bridge = backend.bridge if backend is not None else Bridge(
            'http://127.0.0.1:18021', secrets.token_urlsafe(32), model=MODEL, context=CONTEXT,
            directory=Path('__not_executed__'), backend_identity=self.pin)
        if backend is None:
            bridge.endpoint = 'http://127.0.0.1:1'
        self.worker = Worker(opencode_executable, bridge, self.pin, role='reviewer',
            source_bytes=source_bytes, test_bytes=WHOLE_PROJECT_TEST_BYTES)
        self._pending = threading.local()

    def identity(self):
        return {'name':'hyperqwen-vllm','model':MODEL,'context':CONTEXT,'thinking':'xhigh',
            'model_manifest_sha256':self.pin['model_manifest_sha256'],'backend_identity':deepcopy(self.pin),
            'policy':POLICY,'role':'reviewer','session_isolation':ISOLATION,'same_model_roles':True,
            'adapter_sha256':sha(__file__),'tokenizer':self.tokenizer.identity(),'sampling':deepcopy(SAMPLING),
            'native_source_sha256':native_source_identity(), 'review_worker':self.worker.identity(),
            'agent_transport':'opencode-cli', 'auto_compaction':True}

    def pause(self, run_id):
        if self.backend is None:
            raise ValueError('Pause requires the owned backend lifecycle')
        if self.backend.owner is None:
            if self.backend._create_dispatched:
                raise ValueError('Dispatched backend creation remains unresolved')
            return {'owned_backend': 'not_created', 'released': True}
        receipt = self.backend.deactivate()
        if receipt.get('stopped') is not True or receipt.get('transport_drained') is not True:
            raise ValueError('Owned backend did not settle for pause')
        return receipt

    def admit(self, model, settings, payload, stage):
        self._pending.value = None
        policy = generation_policy(stage, 'reviewer', REVIEW_POLICY)
        if (model != MODEL or settings.get('context') != CONTEXT or settings.get('think') != 'xhigh'
                or settings.get('output_tokens') != policy['max_completion_tokens']
                or any(settings.get(k) not in (None,v) for k,v in SAMPLING.items())):
            raise ValueError('Review settings differ from the frozen OpenCode profile')
        tokens = self.tokenizer(payload)
        if tokens + policy['max_completion_tokens'] > CONTEXT:
            raise ValueError('Complete OpenCode reviewer prompt plus output reserve exceeds context; not dispatched')
        self._pending.value = {'stage':stage,'policy':policy,'payload_digest':digest(payload),
                               'settings_digest':digest(settings),'prompt_tokens':tokens}

    def complete(self, model, settings, payload, cancel, deadline, **_kwargs):
        admission = getattr(self._pending, 'value', None)
        self._pending.value = None
        if (admission is None or model != MODEL or admission['payload_digest'] != digest(payload)
                or admission['settings_digest'] != digest(settings)):
            error = ValueError('Review request lacks exact one-use CPU admission')
            error.diagnostics = {'native_completion':'not_started'}
            raise error
        stage, invoked = admission['stage'], False
        try:
            self.backend.activate(cancel, math.inf if deadline is None else deadline)
            with _before_dispatch():
                scratch = self.backend.directory / 'review-scratch'
                scratch.mkdir(exist_ok=True)
            invoked = True
            result = self.worker.generate(stage=stage, role='reviewer', model=model, settings=settings,
                payload=payload, visible_files={}, cancel=cancel, deadline=math.inf if deadline is None else deadline,
                scratch_parent=scratch, tool_policy=REVIEW_POLICY)
            receipt = result['receipt']
            if receipt.get('auto_compaction') is not True or receipt.get('agent_transport') != 'opencode-cli':
                raise ValueError('Reviewer did not use the approved compacting OpenCode runtime')
            return {'model':MODEL,'choices':[{'finish_reason':'stop','message':{'content':encoded(result['value'])}}],
                'usage':deepcopy(result.get('usage')), 'native':{
                    'backend':'hyperqwen-vllm','stage':stage,'role':'reviewer',
                    'agent_transport':'opencode-cli','auto_compaction':True,
                    'opencode_receipt':receipt, 'admission':admission}}
        except BaseException as error:
            if not invoked or getattr(error, 'not_dispatched', False):
                error.diagnostics = {'native_completion':'not_started'}
            else:
                try:
                    rows = native_receipts(self.backend.bridge, stage)
                    if completed_rows(rows):
                        error.raw_response = {'dispatch_status':'completed_rejected',
                            'error_code':type(error).__name__, 'native':{
                                'backend':'hyperqwen-vllm','stage':stage,'role':'reviewer',
                                'agent_transport':'opencode-cli','native_receipts':rows,'admission':admission}}
                        error.diagnostics = {'native_completion':'completed_rejected'}
                    else:
                        error.diagnostics = {'native_completion':'unknown'}
                except BaseException:
                    error.diagnostics = {'native_completion':'unknown'}
            raise


def profile(backend_pin, opencode_executable, *, source_bytes):
    """Pure prepared identities: ephemeral endpoints/credentials are not approval inputs."""
    pin = _validated_pin(backend_pin)
    bridge = Bridge('http://127.0.0.1:18021',secrets.token_urlsafe(32),model=MODEL,context=CONTEXT,
                       directory=Path('__not_executed__'),backend_identity=pin)
    bridge.endpoint = 'http://127.0.0.1:1'
    workers = {role:Worker(opencode_executable,bridge,pin,role=role,
                 source_bytes=source_bytes,
                 test_bytes=WHOLE_PROJECT_TEST_BYTES
                 ).identity() for role in ROLES}
    generation = {role:{'context':CONTEXT,'think':'xhigh','output_tokens':WHOLE_PROJECT_COMPLETION_TOKENS,**SAMPLING}
                  for role in (*ROLES,'reviewer')}
    return {'roles':{role:MODEL for role in generation},'generation':generation,
            'whole_project_workers':workers,
            'whole_project_prompt_tokenizers':{role:PromptTokenizer(pin,role=role).identity() for role in ROLES},
            'backend':ReviewerProvider(None,pin,opencode_executable,source_bytes=source_bytes).identity()}
