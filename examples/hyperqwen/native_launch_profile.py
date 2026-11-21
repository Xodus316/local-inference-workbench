"""Local model launch allowlist only; no application or prior run loader."""
import json
import subprocess
from pathlib import Path
BACKEND='hyperqwen-vllm'
MODEL='qwen3.8-27b'
CONTEXT=98304
KEY_ENV='WORKBENCH_HYPERQWEN_BRIDGE_KEY'
KEY_REFERENCE='{env:'+KEY_ENV+'}'
SAMPLING={'temperature':1.0,'top_p':0.95,'top_k':20,'min_p':0.0,'presence_penalty':0.0,'frequency_penalty':0.0,'repetition_penalty':1.0}
MAX_FRAME_BYTES=1048576
MAX_STREAM_BYTES=64*1048576
MODELS_MOUNT='type=volume,source=hyperqwen-commerce-models,target=/app/models,readonly'
CACHE_MOUNT='type=volume,source=hyperqwen-commerce-cache,target=/cache'
IMAGE = 'ghcr.io/syv-ai/hyperqwen@sha256:e0e4f9db81c0ce6eda3089d1df445fd50b1abdac8ae92c6eb985b498ec4fd7a4'
REVISION = '3d6c9543292efc17372b824071ef8d9f33366b21'
MODEL_DIR = '/app/models/Qwen3.8-27B-W4A16-AutoRound-fast'
PROFILE = {'CTX': 'long', 'DRAFT_SAMPLE': 'probabilistic', 'DRAFT_TOKENS': '3', 'EXTRA_ARGS': '--structured-outputs-config.backend xgrammar', 'GPU_UTIL': '0.89', 'HF_HUB_OFFLINE': '1', 'HOME': '/cache', 'MAX_LEN': '98304', 'MAX_SEQS': '1', 'MODEL': '/app/models/Qwen3.8-27B-W4A16-AutoRound-fast', 'PREFIX_CACHE': '1', 'PREPARE': '0', 'PTH_FA': '1', 'REQ_METRICS': '1', 'SPEC': 'mtp', 'TOOLS': '1', 'TOOL_PARSER': 'qwen3_coder', 'TOOL_STRICT': 'parameter', 'TRANSFORMERS_OFFLINE': '1', 'VLLM_WSL2_ENABLE_PIN_MEMORY': '1'}

def encode(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')

def write_json(path,value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(encode(value)+b'\n')

def docker(*args,env=None,input_text=None,timeout=60):
    result=subprocess.run(['docker',*args],env=env,input=input_text,text=True,encoding='utf-8',errors='replace',capture_output=True,timeout=timeout)
    if result.returncode:
        raise ValueError('Docker '+args[0]+' failed: '+result.stderr.strip()[-600:])
    return result.stdout.strip()
