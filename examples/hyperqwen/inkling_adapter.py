"""Pinned HyperInkling repair role sharing the strict OpenCode receipt protocol."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import native_adapter as native
from native_backend import OwnedBackend
from native_transport import HyperQwenOpenCodeBridge
from native_launch_profile import docker
from orchestrator.project_opencode_whole import OpenCodeProjectWorker
from orchestrator.whole_project_limits import WHOLE_PROJECT_COMPLETION_TOKENS

MODEL = 'inkling-2-mini'
IMAGE = 'ghcr.io/pento95/hyperinkling@sha256:e6db7291d84c32821789d0e56c38fdd935fbe6375f6057dcf7f64302455c7878'
POLICY = 'hyperinkling-dspark-isolated-repair-v1'
SETTINGS = {'MODEL':'/checkpoint','HOST':'0.0.0.0','ENV_FILE':'','SERVED_NAME':MODEL,
    'MAX_LEN':'98304','MAX_SEQS':'1','NBT':'1024','GPU_UTIL':'0.92','KV_DTYPE':'fp8',
    'KV_OFFLOAD_GB':'0','MM_CACHE_GB':'1','IMAGES':'0','WARMUP':'0','SPEC':'dspark',
    'SPEC_DRAFT':'probabilistic','HYPERINKLING_LOOKUP':'0',
    'PYTORCH_CUDA_ALLOC_CONF':'garbage_collection_threshold:0.8,per_process_memory_fraction:0.95',
    'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}


def source_identity():
    return {name:native.sha(Path(__file__).with_name(name)) for name in
            ('inkling_adapter.py','mixed_models.py')}


def validate_pin(pin):
    if not isinstance(pin,dict):
        raise ValueError('Require an explicit HyperInkling deployment pin')
    value = deepcopy(pin)
    manifest = value.pop('model_manifest_sha256',None)
    if (set(value) != {'image','model_root','checkpoint_path','settings','inventory'}
            or value['image'] != IMAGE or value['model_root'] != '/checkpoint' or value['settings'] != SETTINGS
            or not isinstance(value['checkpoint_path'],str) or not Path(value['checkpoint_path']).is_absolute()
            or ',' in value['checkpoint_path'] or not isinstance(value['inventory'],dict)
            or not value['inventory'].get('metadata_sha256')
            or hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest() != manifest):
        raise ValueError('HyperInkling launch differs from its pinned DSpark profile')
    return {**value,'model_manifest_sha256':manifest}


class Bridge(native.Bridge):
    def __init__(self,*args,**kwargs):
        HyperQwenOpenCodeBridge.__init__(self,*args,**kwargs)
        if (self.model,self.context) != (MODEL,native.CONTEXT):
            raise ValueError('Require the pinned Inkling repair model and context')
        self._role_profile = None
        self._identity.update(name='hyperinkling-vllm',e2e_policy=POLICY,reasoning_effort='xhigh',
            adapter_sha256=native.sha(native.__file__),session_isolation=native.ISOLATION,
            native_source_sha256=native.native_source_identity(),mixed_native_source_sha256=source_identity())

    def role_profile(self,stage,role,tool_policy=None):
        if role != 'repairer':
            raise ValueError('HyperInkling is approved only for application repair')
        return super().role_profile(stage,role,tool_policy)

    def _forward(self,handler,body,result,started):
        result['repair_backend_policy'] = POLICY
        # The pinned Inkling parser consumes reasoning_effort from its template
        # kwargs; the same explicit thinking/completion bounds are enforced by
        # the shared stream validator before it releases downstream DONE.
        return super()._forward(handler,body,result,started)


class Backend(OwnedBackend):
    image,model,launch_profile = IMAGE,MODEL,SETTINGS
    start_command,shm_size = (), '8g'
    validate_pin = staticmethod(validate_pin)

    def __init__(self,directory,pin,*,port=18133):
        pin = validate_pin(pin)
        self.mounts = (f'type=bind,source={Path(pin["checkpoint_path"]).as_posix()},target=/checkpoint,readonly',
                       'type=volume,source=hyperinkling-commerce-20261123_jit-cache,target=/cache')
        super().__init__(directory,pin,port=port)
        self.bridge = Bridge(f'http://127.0.0.1:{port}',native.secrets.token_urlsafe(32),model=MODEL,
            context=native.CONTEXT,directory=self.directory/'requests',backend_identity=self.pin)
        self.bridge.endpoint = 'http://127.0.0.1:1'

    def _make_bridge(self,*args,**kwargs):
        return Bridge(*args,**kwargs)


class Worker(native.Worker):
    def __init__(self,executable,bridge,pin,*,source_bytes,test_bytes,role='repairer'):
        if role != 'repairer' or not isinstance(bridge,Bridge):
            raise ValueError('Require an explicit HyperInkling repair bridge')
        self.bridge,self._backend_identity = bridge,validate_pin(pin)
        self._manifest = self._backend_identity['model_manifest_sha256']
        self._initializing = True
        OpenCodeProjectWorker.__init__(self,executable,bridge.endpoint,MODEL,role=role,
            context=native.CONTEXT,thinking='xhigh',source_bytes=source_bytes,test_bytes=test_bytes,
            completion_token_ceiling=WHOLE_PROJECT_COMPLETION_TOKENS)
        self._initializing = False

    def identity(self):
        return {**super().identity(),'backend':'hyperinkling-vllm','e2e_policy':POLICY,
                'mixed_native_source_sha256':source_identity()}

    def _verify_backend(self,observed):
        if (self.bridge.endpoint != self.endpoint or not isinstance(observed,dict)
                or observed.get('name') != 'hyperinkling-vllm' or observed.get('model') != MODEL
                or observed.get('context') != native.CONTEXT or observed.get('e2e_policy') != POLICY
                or observed.get('session_isolation') != native.ISOLATION
                or observed.get('native_source_sha256') != native.native_source_identity()
                or observed.get('mixed_native_source_sha256') != source_identity()
                or any(observed.get(key) != value for key,value in self._backend_identity.items())):
            raise ValueError('HyperInkling repair identity differs from its approved source/model binding')

    def _resident(self):
        self.bridge.wait_cleanup()
        self._verify_backend(self.bridge.resident())
        return {'name':MODEL,'digest':self._manifest,'context_length':native.CONTEXT,'backend':'hyperinkling-vllm'}


class PromptTokenizer(native.PromptTokenizer):
    def __init__(self,pin,*,role='repairer'):
        if role != 'repairer':
            raise ValueError('Inkling prompt admission is repair-only')
        self.pin,self.role,self.direct = validate_pin(pin),role,False
        names = {'tokenizer.json','tokenizer_config.json','chat_template.jinja','config.json'}
        metadata = self.pin['inventory']['metadata_sha256']
        if not names <= metadata.keys():
            raise ValueError('Missing pinned Inkling tokenizer metadata')
        self.metadata = {name:metadata[name] for name in sorted(names)}
        self._identity = {'policy':POLICY+'-cpu-tokenizer','thinking':'xhigh','role':role,
            'image':IMAGE,'model_root':'/checkpoint','model_manifest_sha256':self.pin['model_manifest_sha256'],
            'metadata_sha256':self.metadata,'material_policy':'maximum-public-role-framing-plus-schema',
            'counter_sha256':hashlib.sha256(native.TOKEN_COUNT_SCRIPT.encode()).hexdigest(),
            'mixed_native_source_sha256':source_identity()}

    def __call__(self,payload):
        # Use the parent's exact framing/admission logic, substituting only its
        # pinned deployment through an overridable execution hook.
        messages = deepcopy(payload.get('messages'))
        if (not isinstance(messages,list) or len(messages)!=2
                or [row.get('role') for row in messages] != ['system','user']
                or not all(isinstance(row.get('content'),str) for row in messages)
                or not isinstance(payload.get('schema'),dict)):
            raise ValueError('Require fresh repair messages and complete schema')
        variants=[]
        for prefix in (native._STRUCTURED_SYSTEM_PREFIX,native.INLINE_PREFIX):
            current=deepcopy(messages)
            current[0]['content']=(prefix+current[0]['content']).strip()
            current[0]['content']+='\nResponse JSON schema:\n'+native.encoded(payload['schema'])
            variants.append(current)
        mount=f'type=bind,source={Path(self.pin["checkpoint_path"]).as_posix()},target=/checkpoint,readonly'
        result=json.loads(docker('run','--rm','--network','none','--read-only','--mount',mount,
            '--tmpfs','/tmp:rw,nosuid,size=256m','--env','HF_HUB_OFFLINE=1','--env','TRANSFORMERS_OFFLINE=1',
            '--env','HF_HOME=/tmp/hf','--entrypoint','python3','-i',IMAGE,
            '-c',native.TOKEN_COUNT_SCRIPT,'/checkpoint',
            input_text=json.dumps({'variants':variants,'metadata':self.metadata}),timeout=60))
        if type(result.get('tokens')) is not int or result['tokens']<=0:
            raise ValueError('Invalid pinned Inkling CPU token count')
        return result['tokens']
