"""Keep one owned HyperQwen container across sequential Commerce GPU stages."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
from pathlib import Path
import secrets
from subprocess import TimeoutExpired
from threading import RLock
import time

from native_transport import HyperQwenOpenCodeBridge
from native_launch_profile import CONTEXT, IMAGE, MODEL, MODEL_DIR, PROFILE, REVISION, docker, write_json


def _validated_pin(pin):
    if not isinstance(pin, dict):
        raise ValueError("Supply the prepared HyperQwen model manifest")
    supplied = deepcopy(pin)
    manifest = supplied.pop("model_manifest_sha256", None)
    if (set(supplied) != {"image", "upstream_revision", "model_root", "settings", "inventory"}
            or supplied["image"] != IMAGE or supplied["upstream_revision"] != REVISION
            or supplied["model_root"] != MODEL_DIR or supplied["settings"] != PROFILE
            or not isinstance(supplied["inventory"], dict) or not supplied["inventory"]
            or hashlib.sha256(json.dumps(supplied, sort_keys=True).encode("utf-8")).hexdigest() != manifest):
        raise ValueError("HyperQwen requires the prepared manifest for the current launch profile")
    return {**supplied, "model_manifest_sha256": manifest}


def _stopped(state):
    return (state.get("Status") in {"created", "exited"} and state.get("Running") is False
            and state.get("Paused") is False and state.get("Restarting") is False and state.get("Dead") is False
            and type(state.get("Pid")) is int and state["Pid"] == 0)


def _remaining(cancel, deadline):
    if cancel.is_set():
        raise InterruptedError("HyperQwen activation was cancelled")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("HyperQwen activation exceeded the workflow deadline")
    return remaining


class HyperQwenWholeBackend:
    """Start HyperQwen only when the next OpenCode stage owns the GPU."""

    def __init__(self, directory, pin, *, port=18021):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("HyperQwen requires a local trial port")
        self.directory = Path(directory)
        self.pin = _validated_pin(pin)
        self.port = port
        self.owner = None
        self.bridge = None
        self._bridge_open = False
        self._active = False
        self._closed = False
        self._epoch = 0
        self._deactivations = 0
        self._lock = RLock()


    def __exit__(self, _exc_type, error, _traceback):
        if error is None:
            self.close()
        else:
            self._cleanup_after_error(error, self.close)

    @staticmethod
    def _cleanup_after_error(original, cleanup):
        try:
            cleanup()
        except Exception as error:
            original.add_note(f"Owned HyperQwen cleanup raised {type(error).__name__}: {error}")

    def _state(self):
        state = json.loads(docker("inspect", "--format", "{{json .State}}", self.owner, timeout=5))
        if not isinstance(state, dict):
            raise ValueError("Docker did not return the owned HyperQwen container state")
        return state

    def _receipt(self, filename, **details):
        value = {"container_id": self.owner, "epoch": self._epoch,
                 "recorded_utc": datetime.now(timezone.utc).isoformat(), **details}
        write_json(self.directory / filename, value)
        return value

    def activate(self, cancel, deadline):
        """Start the same container and return its authenticated, checked identity."""
        with self._lock:
            if self._closed or not self._bridge_open:
                raise ValueError("Open the owned HyperQwen lifecycle before activation")
            _remaining(cancel, deadline)
            if self._active:
                try:
                    return self.bridge.resident()
                except (OSError, HTTPException, ValueError) as error:
                    self._cleanup_after_error(error, self.deactivate)
                    raise
            self._epoch += 1
            started = time.monotonic()
            try:
                if not _stopped(self._state()):
                    raise ValueError("The owned HyperQwen container was not provably stopped before activation")
                docker("start", self.owner, timeout=min(60, _remaining(cancel, deadline)))
                while True:
                    _remaining(cancel, deadline)
                    try:
                        identity = self.bridge.resident()
                        break
                    except (OSError, HTTPException):
                        if self._state().get("Status") != "running":
                            raise
                        cancel.wait(min(2, _remaining(cancel, deadline)))
                _remaining(cancel, deadline)
                self._receipt(f"activation-{self._epoch}.json", active=True,
                              duration_seconds=round(time.monotonic() - started, 3), backend_identity=identity)
                self._active = True
                return identity
            except BaseException as error:
                self._cleanup_after_error(error, self.deactivate)
                raise

    def _drain_error(self):
        if self.bridge is not None:
            try:
                self.bridge.wait_cleanup()
            except (OSError, ValueError) as error:
                return error
        return None

    def _stop_container(self):
        control_error = None
        try:
            state = self._state()
            if _stopped(state):
                return state
        except (OSError, TimeoutExpired, ValueError) as error:
            control_error = error
        try:
            docker("stop", "--time", "20", self.owner, timeout=35)
        except (OSError, TimeoutExpired, ValueError) as error:
            control_error = error
        try:
            state = self._state()
        except (OSError, TimeoutExpired, ValueError) as error:
            raise ValueError("Could not verify the owned HyperQwen container stopped") from (control_error or error)
        if state.get("Running") is True:
            # A failed graceful stop can leave the GPU allocated. Escalate
            # only against the container ID created by this lifecycle.
            try:
                docker("kill", self.owner, timeout=15)
            except (OSError, TimeoutExpired, ValueError) as error:
                control_error = error
            state = self._state()
        if not _stopped(state):
            raise ValueError("The owned HyperQwen container did not stop") from control_error
        return state

    def deactivate(self):
        """Drain OpenCode transport, then verify the owned container released its process."""
        with self._lock:
            if self.owner is None:
                raise ValueError("The HyperQwen lifecycle has no owned container")
            drain_error = self._drain_error()
            state = None
            self._deactivations += 1
            try:
                state = self._stop_container()
            finally:
                if state is not None:
                    self._active = False
                receipt = self._receipt(f"deactivation-{self._epoch}-{self._deactivations}.json",
                                        stopped=state is not None, transport_drained=drain_error is None,
                                        container_status=state["Status"] if state is not None else None,
                                        container_pid=state["Pid"] if state is not None else None)
            if drain_error is not None:
                raise drain_error
            return receipt

    def close(self):
        """Close the bridge and stop the owned container even after an interrupted turn."""
        with self._lock:
            if self._closed or self.owner is None:
                return
            bridge_error = None
            if self._bridge_open:
                try:
                    self.bridge.__exit__(None, None, None)
                except Exception as error:
                    bridge_error = error
                else:
                    self._bridge_open = False
            drain_error = self._drain_error()
            state = None
            try:
                state = self._stop_container()
            finally:
                if state is not None:
                    self._active = False
                self._closed = state is not None and not self._bridge_open
                self._receipt("cleanup.json", stopped=state is not None, transport_drained=drain_error is None,
                              container_status=state["Status"] if state is not None else None,
                              container_pid=state["Pid"] if state is not None else None,
                              bridge_closed=not self._bridge_open)
            if bridge_error is not None:
                raise bridge_error
            if drain_error is not None:
                raise drain_error

from uuid import uuid4
import os
import re
import subprocess
from native_launch_profile import MODELS_MOUNT,CACHE_MOUNT

def make_bridge(*args,**kwargs):
    from native_adapter import Bridge
    return Bridge(*args,**kwargs)

POLICY='exact-owned-container-create-v1'
OWNER_LABEL='org.workbench.fresh-model-owner'
CREATE_TIMEOUT_SECONDS=180


def docker_command(*args,env=None,timeout=60):
    return subprocess.run(['docker',*args],env=env,text=True,encoding='utf8',errors='replace',
                          capture_output=True,timeout=timeout)


def save(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')


def checked_command(*args,env=None,timeout=60):
    result=docker_command(*args,env=env,timeout=timeout)
    if result.returncode:
        raise ValueError('Docker '+args[0]+' failed: '+result.stderr.strip()[-600:])
    return result.stdout.strip()


class OwnedBackend(HyperQwenWholeBackend):
    """Never treats a timed-out create client as proof that Docker did nothing."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.create_intent=None
        self._create_dispatched=False
        self._reconciliations=0
        self._provisioning_deadline=None

    def _provisioning_remaining(self):
        remaining=self._provisioning_deadline-time.monotonic()
        if remaining<=0:
            raise TimeoutError('The bounded Docker provisioning window expired before dispatch')
        return remaining

    def _inspect_exact_name(self):
        result=docker_command('inspect',self.create_intent['name'],timeout=5)
        if result.returncode:
            # Only the exact daemon absence reply proves absence. Connectivity,
            # permission and other CLI failures leave the outcome ambiguous.
            expected='Error: No such object: '+self.create_intent['name']
            if result.stderr.strip().casefold()==expected.casefold():
                return None
            raise ValueError('Cannot reconcile the exact create name: '+result.stderr.strip()[-600:])
        values=json.loads(result.stdout)
        if not isinstance(values,list) or len(values)!=1 or not isinstance(values[0],dict):
            raise ValueError('Exact-name inspection returned an invalid inventory')
        raw=values[0];config=raw.get('Config') or {}
        state={key:(raw.get('State') or {}).get(key) for key in
               ('Status','Running','Paused','Restarting','Dead','Pid','ExitCode','Error')}
        observed={'container_id':raw.get('Id'),'name':raw.get('Name'),
            'image_id':raw.get('Image'),'image_reference':config.get('Image'),
            'ownership_label':(config.get('Labels') or {}).get(OWNER_LABEL),'state':state}
        if (not isinstance(observed['container_id'],str) or re.fullmatch('[a-f0-9]{64}',observed['container_id']) is None
                or observed['name']!='/'+self.create_intent['name']
                or observed['image_id']!=self.create_intent['image_id']
                or observed['image_reference']!=IMAGE
                or observed['ownership_label']!=self.create_intent['ownership_nonce']
                or (self.owner is not None and self.owner!=observed['container_id'])):
            raise ValueError('Inspected container does not match exact name, nonce and pinned image ownership')
        return observed

    def _adopt(self,observed,*,reconciled):
        self.owner=observed['container_id']
        save(self.directory/'owner.json',{'container_id':self.owner,'name':self.create_intent['name'],
            'port':self.port,'lifecycle_policy':POLICY,'ownership_label':OWNER_LABEL,
            'ownership_nonce':self.create_intent['ownership_nonce'],'image_id':self.create_intent['image_id'],
            'image_reference':IMAGE,'reconciled_after_create_failure':reconciled})

    def __enter__(self):
        with self._lock:
            if self.owner is not None or self._closed or self.create_intent is not None:
                raise ValueError('The lifecycle is already used')
            self.directory.mkdir(parents=True,exist_ok=True)
            if any(p.name in {'owner.json','cleanup.json','requests','create-intent.json','provisioning-intent.json'} or
                   p.name.startswith(('activation-','deactivation-','create-reconciliation-'))
                   for p in self.directory.iterdir()):
                raise ValueError('The backend directory already has lifecycle evidence')
            name='workbench-fresh-model-'+uuid4().hex[:10]
            nonce=uuid4().hex
            self.create_intent={'policy':POLICY,'name':name,'ownership_label':OWNER_LABEL,
                'ownership_nonce':nonce,'image_reference':IMAGE,'image_id':None,'port':self.port,
                'create_timeout_seconds':CREATE_TIMEOUT_SECONDS,'settings':{**PROFILE,'PORT':str(self.port)},
                'provisioning_timeout_seconds':CREATE_TIMEOUT_SECONDS,
                'mounts':[MODELS_MOUNT,CACHE_MOUNT],'gpu_device':'0','secret_material_recorded':False}
            save(self.directory/'provisioning-intent.json',self.create_intent)
            self._provisioning_deadline=time.monotonic()+CREATE_TIMEOUT_SECONDS
            key=secrets.token_urlsafe(32)
            settings=[item for k,v in self.create_intent['settings'].items() for item in ('-e',f'{k}={v}')]
            phase='image-resolution'
            try:
                image_id=checked_command('image','inspect','--format','{{.Id}}',IMAGE,timeout=self._provisioning_remaining())
                if re.fullmatch('sha256:[a-f0-9]{64}',image_id) is None:
                    raise ValueError('Pinned image inspection returned an invalid config ID')
                self.create_intent['image_id']=image_id
                save(self.directory/'create-intent.json',self.create_intent)
                phase='create'
                create_remaining=self._provisioning_remaining()
                self.create_intent['effective_create_timeout_seconds']=create_remaining
                self.create_intent['provisioning_elapsed_before_create_seconds']=CREATE_TIMEOUT_SECONDS-create_remaining
                save(self.directory/'create-intent.json',self.create_intent)
                self._create_dispatched=True
                owner=checked_command('create','--pull','never','--name',name,'--label',OWNER_LABEL+'='+nonce,
                    '--gpus','device=0','--shm-size','1g','-p',f'127.0.0.1:{self.port}:{self.port}',
                    '--mount',MODELS_MOUNT,'--mount',CACHE_MOUNT,'-e','VLLM_API_KEY',*settings,IMAGE,'single',
                    env={**os.environ,'VLLM_API_KEY':key},timeout=create_remaining)
                if re.fullmatch('[a-f0-9]{64}',owner) is None:
                    raise ValueError('Docker did not return the exact created container ID')
                phase='ownership-inspection'
                observed=self._inspect_exact_name()
                if observed is None or observed['container_id']!=owner:
                    raise ValueError('Created container ID does not match exact ownership inspection')
                self._adopt(observed,reconciled=False)
                save(self.directory/'create-result.json',{'policy':POLICY,'client_outcome':'completed',
                    'container_id':owner,'observed':observed})
                if not _stopped(observed['state']) or observed['state']['Status']!='created':
                    raise ValueError('The owned backend must begin stopped')
                phase='bridge-open'
                self.bridge=make_bridge(f'http://127.0.0.1:{self.port}',key,model=MODEL,context=CONTEXT,
                    directory=self.directory/'requests',backend_identity=self.pin)
                self.bridge.__enter__();self._bridge_open=True
                return self
            except BaseException as error:
                # No serialized environment or argv may include the API key.
                save(self.directory/'create-error.json',{'policy':POLICY,'type':type(error).__name__,
                    'message':str(error),'create_dispatched':self._create_dispatched,
                    'provisioning_phase':phase,'native_completion_claim':False})
                self._cleanup_after_error(error,self.close)
                raise

    def _state(self):
        observed=self._inspect_exact_name()
        if observed is None:
            raise ValueError('The exact owned container is absent during an active lifecycle')
        return observed['state']

    def _stop_container(self):
        state=self._state()
        if _stopped(state):return state
        failure=None
        try:checked_command('stop','--time','20',self.owner,timeout=35)
        except (OSError,subprocess.TimeoutExpired,ValueError) as error:failure=error
        state=self._state()
        if state.get('Running') is True:
            try:checked_command('kill',self.owner,timeout=15)
            except (OSError,subprocess.TimeoutExpired,ValueError) as error:failure=error
            state=self._state()
        if not _stopped(state):raise ValueError('The exact owned container did not stop') from failure
        return state

    def close(self):
        with self._lock:
            if self._closed:return
            if self.owner is None and self.create_intent is not None and not self._create_dispatched:
                self._closed=True
                self._receipt('cleanup.json',stopped=True,transport_drained=True,container_status='not-created',
                    container_pid=0,bridge_closed=True,lifecycle_policy=POLICY,
                    create_outcome='not-dispatched',native_completion_claim=False)
                return
            if self.owner is None and self._create_dispatched:
                self._reconciliations+=1
                record={'policy':POLICY,'name':self.create_intent['name'],
                    'ownership_nonce':self.create_intent['ownership_nonce'],'native_completion_claim':False}
                try:
                    observed=self._inspect_exact_name()
                    record.update(outcome='exact-name-absent' if observed is None else 'exact-owner-found',
                                  observed=observed)
                    if observed is None:
                        # A failed create client can outlive its local timeout.
                        # One absent-name observation cannot rule out a delayed
                        # daemon-side creation; preserve unresolved ownership.
                        raise ValueError('Dispatched create remains unresolved after exact-name absence')
                    self._adopt(observed,reconciled=True)
                except BaseException as error:
                    record.update(outcome='unresolved',error_type=type(error).__name__,error=str(error))
                    self._receipt('cleanup.json',stopped=False,transport_drained=True,container_status='unknown',
                        container_pid=None,bridge_closed=True,lifecycle_policy=POLICY,
                        create_outcome='unresolved',native_completion_claim=False)
                    raise
                finally:
                    save(self.directory/f'create-reconciliation-{self._reconciliations}.json',record)
            return super().close()
