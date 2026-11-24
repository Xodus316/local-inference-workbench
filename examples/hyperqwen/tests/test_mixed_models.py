"""CPU-only model routing, immutable identity and exact-owner handoff checks."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch

sys.path[:0]=[str(Path(__file__).resolve().parents[3]),str(Path(__file__).resolve().parents[1])]
import inkling_adapter as inkling
import mixed_models as mixed
import native_adapter as native
import native_backend
import native_transport
from orchestrator.role_isolation import KEY, MIXED_POLICY, isolation_binding, validate_role_isolation
from orchestrator.whole_project_limits import DEFAULT_SOURCE_BYTES, WHOLE_PROJECT_TEST_BYTES
from test_native_adapter import pin as qwen_pin, executable


def inkling_pin():
    value={'image':inkling.IMAGE,'model_root':'/checkpoint','checkpoint_path':str(Path(tempfile.gettempdir()).resolve()/'synthetic-inkling'),
        'settings':deepcopy(inkling.SETTINGS),'inventory':{'metadata_sha256':{name:'a'*64 for name in
        ('tokenizer.json','tokenizer_config.json','chat_template.jinja','config.json')}}}
    return {**value,'model_manifest_sha256':hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()}


class MixedModelTests(unittest.TestCase):
    def test_alias_listing_accepts_only_the_explicit_same_root_context_deployment(self):
        row={'id':inkling.MODEL,'root':'/checkpoint','max_model_len':98304}
        listing={'data':[row,{**row,'id':'local'}]}
        self.assertEqual(native_transport.validate_model_listing(listing,inkling.MODEL,98304,'/checkpoint',
            aliases=[inkling.MODEL,'local']),row)
        with tempfile.TemporaryDirectory() as directory:
            bridge=inkling.Backend(Path(directory),inkling_pin()).bridge
            connection=MagicMock()
            response=connection.getresponse.return_value.__enter__.return_value
            response.status=200
            response.read.return_value=json.dumps({'data':list(reversed(listing['data']))}).encode()
            with patch.object(native_transport,'HTTPConnection',return_value=connection):
                self.assertEqual(bridge.model_record()['id'],inkling.MODEL)
            connection.close.assert_called_once()
        # Preserve strict single-model behavior for existing Qwen pins.
        with self.assertRaises(native_transport.HyperQwenBridgeError):
            native_transport.validate_model_listing(listing,inkling.MODEL,98304,'/checkpoint')
        for kind in ('missing','duplicate','foreign','root','context','malformed'):
            changed=deepcopy(listing)
            if kind=='missing':changed['data'].pop(0)
            if kind=='duplicate':changed['data'][1]['id']=inkling.MODEL
            if kind=='foreign':changed['data'][1]['id']='unapproved'
            if kind=='root':changed['data'][1]['root']='/another-model'
            if kind=='context':changed['data'][1]['max_model_len']=32768
            if kind=='malformed':changed['data'][1]=None
            with self.subTest(kind=kind),self.assertRaises(native_transport.HyperQwenBridgeError):
                native_transport.validate_model_listing(changed,inkling.MODEL,98304,'/checkpoint',aliases=[inkling.MODEL,'local'])

    def preview(self,root):
        return mixed.profile(qwen_pin(),inkling_pin(),executable(root),source_bytes=DEFAULT_SOURCE_BYTES)

    def test_profile_uses_only_inkling_for_repair_and_constructs_no_process_or_network(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('subprocess.run',side_effect=AssertionError('No process')), \
                patch('subprocess.Popen',side_effect=AssertionError('No process')):
            value=self.preview(Path(directory))
        self.assertEqual(value['roles']['repairer'],inkling.MODEL)
        self.assertTrue(all(model==native.MODEL for role,model in value['roles'].items() if role!='repairer'))
        self.assertEqual(value['whole_project_workers']['repairer']['backend'],'hyperinkling-vllm')
        self.assertEqual(value['whole_project_prompt_tokenizers']['repairer']['model_manifest_sha256'],inkling_pin()['model_manifest_sha256'])
        self.assertTrue(all(w['auto_compaction'] for w in value['whole_project_workers'].values()))
        self.assertEqual(value['backend']['review_worker']['model'],native.MODEL)
        bundle=deepcopy(value)
        bundle[KEY]=isolation_binding(bundle)
        self.assertEqual(bundle[KEY]['policy'],MIXED_POLICY)
        self.assertTrue(validate_role_isolation(bundle))
        for mutation in ('model','manifest','context','session','provider','role'):
            changed=deepcopy(bundle)
            worker=changed['whole_project_workers']['repairer']
            if mutation=='model':worker['model']=native.MODEL
            if mutation=='manifest':worker['model_digest']='0'*64
            if mutation=='context':worker['context']=32768
            if mutation=='session':worker['session_isolation']='shared'
            if mutation=='provider':changed['backend']['model']=inkling.MODEL
            if mutation=='role':worker['role']='test_author'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                validate_role_isolation(changed)

    def test_handoff_drains_and_stops_previous_owner_before_new_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            calls=[]
            manager=mixed.Backends(Path(directory),qwen_pin(),inkling_pin())
            def fake(name):
                return SimpleNamespace(owner=name,activate=Mock(side_effect=lambda *args:
                    (calls.append(name+' activate') or {'model':name,'model_manifest_sha256':'a'*64})),
                    deactivate=Mock(side_effect=lambda:
                    (calls.append(name+' stop/drain') or {'stopped':True,'transport_drained':True})))
            manager.qwen,manager.inkling=fake('qwen'),fake('inkling')
            for role in ('implementer','repairer','test_author','reviewer'):
                manager.acquire(role,threading.Event())
            self.assertEqual(calls,['qwen activate','qwen stop/drain','inkling activate','inkling stop/drain',
                                    'qwen activate','qwen activate'])
            self.assertEqual(len(list(Path(directory).glob('route-*.json'))),4)

    def test_unknown_or_incomplete_release_never_activates_next_model(self):
        for failure in ({'stopped':False,'transport_drained':True},{'stopped':True,'transport_drained':False},
                        RuntimeError('Unknown native completion')):
            with tempfile.TemporaryDirectory() as directory:
                manager=mixed.Backends(Path(directory),qwen_pin(),inkling_pin())
                previous=SimpleNamespace(deactivate=Mock())
                if isinstance(failure,Exception):previous.deactivate.side_effect=failure
                else:previous.deactivate.return_value=failure
                manager.active=previous
                manager.inkling.activate=Mock()
                with self.assertRaises((ValueError,RuntimeError)):
                    manager.acquire('repairer',threading.Event())
                manager.inkling.activate.assert_not_called()
                self.assertTrue(manager.failed)
                with self.assertRaisesRegex(ValueError,'cannot be retried'):
                    manager.acquire('planner',threading.Event())

    def test_pause_attempts_to_release_both_owned_backends_even_if_one_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=mixed.Backends(Path(directory),qwen_pin(),inkling_pin())
            manager.qwen.deactivate=Mock(side_effect=ValueError('Unknown drain'))
            manager.inkling.deactivate=Mock(return_value={'stopped':True,'transport_drained':True})
            with self.assertRaisesRegex(ValueError,'Both owned models'):
                manager.pause()
            manager.qwen.deactivate.assert_called_once()
            manager.inkling.deactivate.assert_called_once()

    def test_worker_refuses_other_roles_changed_deployment_or_different_live_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);backend=inkling.Backend(root/'unused',inkling_pin())
            worker=inkling.Worker(executable(root),backend.bridge,inkling_pin(),source_bytes=DEFAULT_SOURCE_BYTES,test_bytes=WHOLE_PROJECT_TEST_BYTES)
            observed=deepcopy(backend.bridge._identity)
            worker._verify_backend(observed)
            with self.assertRaisesRegex(ValueError,'repair'):
                backend.bridge.role_profile('build_tests','test_author')
            for key in ('model_manifest_sha256','model','mixed_native_source_sha256'):
                bad={**observed,key:'changed'}
                with self.subTest(key=key),self.assertRaises(ValueError):worker._verify_backend(bad)
            pin=inkling_pin();pin['settings']['SPEC']='off'
            with self.assertRaises(ValueError):inkling.validate_pin(pin)

    def test_inkling_compaction_retains_exact_role_budget_manifest_and_strict_primary_protocol(self):
        from test_all_opencode_roles import compaction
        with tempfile.TemporaryDirectory() as directory:
            bridge=inkling.Backend(Path(directory),inkling_pin()).bridge
            policy=native.generation_policy('repair_1_select','repairer',native.INLINE_SELECTION_POLICY)
            body=native_transport._body(json.dumps({**compaction(),'model':inkling.MODEL}).encode(),inkling.MODEL,
                native.CONTEXT,'synthetic','xhigh',policy)
            result={'stage':'repair_1_select','request_id':'synthetic','reasoning_effort':'xhigh'}
            with bridge.role_profile('repair_1_select','repairer',native.INLINE_SELECTION_POLICY), \
                    patch.object(native.HyperQwenOpenCodeBridge,'_forward',return_value=iter([b'sent'])):
                self.assertEqual(list(bridge._forward(None,body,result,0)),[b'sent'])
            self.assertEqual(result['native_model_manifest_sha256'],inkling_pin()['model_manifest_sha256'])
            self.assertEqual(result['generation_policy']['thinking_token_budget'],16384)
            self.assertEqual(result['auxiliary_request'],'opencode-compaction')
            self.assertEqual(body['model'],inkling.MODEL)
            with self.assertRaises(native_transport.HyperQwenBridgeError):
                native_transport._body(json.dumps({'model':inkling.MODEL,'stream':True,
                    'messages':[{'role':'user','content':'untyped'}]}).encode(),inkling.MODEL,
                    native.CONTEXT,'synthetic','xhigh',policy)

    def test_inkling_create_keeps_exact_owner_protocol_and_approved_image_mounts(self):
        with tempfile.TemporaryDirectory() as directory:
            backend=inkling.Backend(Path(directory),inkling_pin())
            owner='b'*64;image_id='sha256:'+'c'*64
            state={'Status':'created','Running':False,'Paused':False,'Restarting':False,'Dead':False,'Pid':0}
            def command(*args,**kwargs):
                if args[:2]==('image','inspect'):return image_id
                if args[0]=='create':
                    self.assertIn(inkling.IMAGE,args)
                    self.assertNotIn('single',args)
                    self.assertIn('SPEC=dspark',args)
                    self.assertIn('--label',args)
                    self.assertIn('type=bind,source='+Path(inkling_pin()['checkpoint_path']).as_posix()+',target=/checkpoint,readonly',args)
                    return owner
                raise AssertionError(args)
            def inspect():
                return {'container_id':owner,'name':'/'+backend.create_intent['name'],'image_id':image_id,
                    'image_reference':inkling.IMAGE,'ownership_label':backend.create_intent['ownership_nonce'],'state':state}
            bridge=MagicMock()
            with patch.object(native_backend,'checked_command',side_effect=command), \
                    patch.object(backend,'_inspect_exact_name',side_effect=inspect), \
                    patch.object(backend,'_make_bridge',return_value=bridge):
                with backend:pass
            result=json.loads((Path(directory)/'cleanup.json').read_text())
            self.assertTrue(result['stopped'])
            self.assertTrue(result['transport_drained'])
            self.assertTrue(result['bridge_closed'])


if __name__=='__main__':unittest.main()
