"""Sequential, exact-owner GPU handoff for Qwen roles and Inkling repairs."""
from contextlib import ExitStack
from copy import deepcopy
import math
from pathlib import Path
import threading
import time

import native_adapter as qwen
import inkling_adapter as inkling
from native_backend import save
from orchestrator.whole_project_limits import WHOLE_PROJECT_TEST_BYTES
from role_routes import validate_opencode_routes

POLICY='qwen-with-hyperinkling-repair-v1'


class Backends:
    def __init__(self,directory,qwen_pin,inkling_pin):
        self.directory=Path(directory)
        self.qwen=qwen.Backend(self.directory/'qwen',qwen_pin)
        self.inkling=inkling.Backend(self.directory/'inkling',inkling_pin)
        self.active=None
        self.failed=False
        self.switches=0
        self._lock=threading.RLock()

    def __enter__(self):
        self.stack=ExitStack()
        try:
            self.stack.enter_context(self.qwen)
            self.stack.enter_context(self.inkling)
        except BaseException:
            self.stack.close()
            raise
        return self

    def acquire(self,role,cancel,deadline=None):
        if role not in (*qwen.ROLES,'reviewer'):
            raise ValueError('Unknown model role')
        with self._lock:
            if self.failed:
                raise ValueError('A failed or unresolved GPU handoff cannot be retried')
            target=self.inkling if role=='repairer' else self.qwen
            try:
                if self.active is not None and self.active is not target:
                    receipt=self.active.deactivate()
                    if receipt.get('stopped') is not True or receipt.get('transport_drained') is not True:
                        raise ValueError('Previous model did not settle and release before GPU handoff')
                    self.active=None
                identity=target.activate(cancel,math.inf if deadline is None else deadline)
                self.active=target
                self.switches+=1
                save(self.directory/f'route-{self.switches}.json',{
                    'policy':POLICY,'role':role,'model':identity['model'],
                    'manifest':identity['model_manifest_sha256'],'container_id':target.owner,
                    'recorded_epoch':time.time(),'previous_backend_released':True})
                return identity
            except BaseException:
                self.failed=True
                raise

    def pause(self):
        with self._lock:
            receipts,errors=[],[]
            for backend in (self.qwen,self.inkling):
                try:
                    receipts.append(backend.deactivate())
                except Exception as error:
                    errors.append(error)
            if errors or any(r.get('stopped') is not True or r.get('transport_drained') is not True for r in receipts):
                self.failed=True
                raise ValueError('Both owned models must stop and drain before pause')
            self.active=None
            return {'stopped':True,'transport_drained':True,'backends':receipts}

    def __exit__(self,*args):
        return self.stack.__exit__(*args)


class ReviewLease:
    def __init__(self,backends):
        self.backends=backends
        self.bridge=backends.qwen.bridge
        self.directory=backends.qwen.directory

    def activate(self,cancel,deadline):
        return self.backends.acquire('reviewer',cancel,deadline)


class ReviewerProvider(qwen.ReviewerProvider):
    def __init__(self,backends,pin,executable,*,source_bytes):
        self.backends=backends
        super().__init__(None if backends is None else ReviewLease(backends),pin,executable,source_bytes=source_bytes)

    def identity(self):
        return {**super().identity(),'same_model_roles':False,'mixed_model_policy':POLICY,
                'mixed_native_source_sha256':inkling.source_identity()}

    def pause(self,run_id):
        if self.backends is None:
            raise ValueError('Pause requires both owned model lifecycles')
        return self.backends.pause()


def profile(qwen_pin,inkling_pin,executable,*,source_bytes):
    value=qwen.profile(qwen_pin,executable,source_bytes=source_bytes)
    preview=inkling.Backend(Path('__not_executed__'),inkling_pin)
    worker=inkling.Worker(executable,preview.bridge,inkling_pin,
        source_bytes=source_bytes,test_bytes=WHOLE_PROJECT_TEST_BYTES)
    value['roles']['repairer']=inkling.MODEL
    value['whole_project_workers']['repairer']=worker.identity()
    value['whole_project_prompt_tokenizers']['repairer']=inkling.PromptTokenizer(inkling_pin).identity()
    value['backend']=ReviewerProvider(None,qwen_pin,executable,source_bytes=source_bytes).identity()
    validate_opencode_routes(value)
    return deepcopy(value)
