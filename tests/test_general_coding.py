"""Commerce and browser-only tasks enter the same normal Build preparation path."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from orchestrator.config import Config,BuildConfig,DEFAULT_ROLES
from orchestrator.general_coding import KEY,GeneralCodingConfig,validate
from orchestrator.project_build import REVIEWED_POLICY
from orchestrator.runtime import Runtime
from orchestrator.recovery_limits import EXPANDED
from orchestrator.whole_project_checkpoint import binding_digest
import test_whole_project_setup as fixtures
from test_project_environment import environment


class GeneralCodingTests(unittest.TestCase):
    def test_stopped_docker_is_not_misreported_as_a_missing_project_image(self):
        from orchestrator.build_sandbox import DockerBuildExecutor
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            executor=DockerBuildExecutor(Path(directory),BuildConfig())
            with patch('orchestrator.build_sandbox.subprocess.run',return_value=SimpleNamespace(returncode=1)) as call:
                with self.assertRaisesRegex(ValueError,'Docker sandbox is unavailable'):executor.preflight()
                self.assertEqual(call.call_count,1)
            with patch('orchestrator.build_sandbox.subprocess.run',side_effect=[SimpleNamespace(returncode=0),SimpleNamespace(returncode=1)]):
                with self.assertRaisesRegex(ValueError,'image is not installed'):executor.preflight()

    def test_declared_commerce_compiler_and_packaging_checks_are_optional_for_general(self):
        from orchestrator.candidate_source_contract import KEY as SOURCE_KEY,POLICY as SOURCE_POLICY
        from orchestrator.java_api_probe import POLICY_V2
        from orchestrator.frontend_diagnostic_probe import POLICY as FRONTEND_POLICY
        with tempfile.TemporaryDirectory() as directory:
            runtime=self.runtime(directory,'commerce')
            try:
                runtime.config.build.general_coding=GeneralCodingConfig(source_bytes=fixtures.SOURCE_BYTES,
                    java_api_probe={'policy':POLICY_V2,'classpath_roots':['/opt/gradle/cache'],'include_jdk':True},
                    frontend_diagnostics={'policy':FRONTEND_POLICY,'node_modules':'/opt/frontend/node_modules'},
                    candidate_source_contract=SOURCE_POLICY)
                bundle=runtime.prepare({'mode':'build','prompt':'Build the catalog.'})['bundle']
                self.assertEqual(bundle[SOURCE_KEY],SOURCE_POLICY)
                self.assertEqual(bundle['whole_project_java_api_probe']['policy'],POLICY_V2)
                self.assertEqual(bundle['whole_project_frontend_diagnostics']['policy'],FRONTEND_POLICY)
            finally:runtime.close()

    def runtime(self,directory,kind):
        reference=Path(directory)/'reference';reference.mkdir()
        paths=['api/App.java','frontend/App.vue'] if kind=='commerce' else ['web/index.html']
        for path in paths:
            p=reference/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('reference-only source')
        requirements=['backend-compile','frontend-compile','python-unittest'] if kind=='commerce' else ['frontend-syntax','trusted-browser','python-unittest']
        env=environment(required_application_files=paths,api=({'base_url_env':'API_BASE_URL','base_url_kind':'api_root','path_prefix':'/api'} if kind=='commerce' else None),
            verification={'policy':'fixture-quality-v1','required_checks':requirements},
            compile_checkpoints=([{'name':'backend','source_root':'api','required_checks':['backend-compile']},
                {'name':'frontend','source_root':'frontend','required_checks':['frontend-compile']}] if kind=='commerce' else
                [{'name':'frontend','source_root':'web','required_checks':['frontend-syntax']}]))
        config=Config(roles={r:fixtures.MODEL for r in DEFAULT_ROLES},
            generation={r:{'context':98304,'think':'xhigh'} for r in DEFAULT_ROLES},
            build={'offline_project_enabled':True,'allow_shared_test_author':True,'reference_project':str(reference),
                'project_environment':env,'general_coding':{'source_bytes':fixtures.SOURCE_BYTES}})
        def identity(role):return {**fixtures.identity(role),'name':'opencode','auto_compaction':True}
        backend={**fixtures.backend(),'agent_transport':'opencode-cli','auto_compaction':True,'review_worker':identity('reviewer')}
        provider=SimpleNamespace(identity=lambda:deepcopy(backend),complete=Mock(side_effect=AssertionError('No inference')))
        executor=SimpleNamespace(preflight=Mock(return_value={'engine':'fixture','image':'fixture:offline','image_id':fixtures.IMAGE}),
            test=Mock(side_effect=AssertionError('No application execution')))
        runtime=Runtime(Path(directory)/'state',config,provider,executor)
        runtime.whole_project_workers={r:SimpleNamespace(identity=lambda r=r:identity(r)) for r in fixtures.ROLES}
        return runtime

    def test_normal_build_api_uses_one_general_engine_for_both_project_capabilities(self):
        policies=[]
        for kind in ('commerce','snake'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                runtime=self.runtime(directory,kind)
                try:
                    row=runtime.prepare({'mode':'build','prompt':'Build the requested application.'})
                    b=row['bundle'];policies.append((b['whole_project_workflow'],b['whole_project_stage_recovery_policy']))
                    self.assertTrue(validate(b));self.assertEqual(b['request']['build_profile'],REVIEWED_POLICY)
                    self.assertEqual(b['whole_project_recovery_limits'],EXPANDED)
                    self.assertEqual(row['status'],'prepared');self.assertFalse(row['approved']);self.assertEqual(row['calls'],[])
                    self.assertNotIn('whole_project_seed',b);self.assertNotIn('whole_project_stage_continuation',b)
                    self.assertEqual(len(b['project_environment']['compile_checkpoints']),2 if kind=='commerce' else 1)
                    self.assertEqual(b['project_environment']['api'] is None,kind=='snake')
                    if kind=='snake':
                        for key in ('whole_project_java_api_probe','whole_project_frontend_diagnostics','whole_project_candidate_source_contract'):
                            self.assertNotIn(key,b)
                    runtime.provider.complete.assert_not_called();runtime.build_executor.test.assert_not_called()
                    changed=deepcopy(b);changed[KEY]['options_digest']='a'*64
                    self.assertNotEqual(binding_digest(b),binding_digest(changed))
                    with self.assertRaises(ValueError):validate(changed)
                finally:runtime.close()
        self.assertEqual(policies[0],policies[1])

    def test_missing_compaction_and_per_request_policy_overrides_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=self.runtime(directory,'snake')
            try:
                with self.assertRaisesRegex(ValueError,'not per-request'):
                    runtime.prepare_whole_project({'mode':'build','prompt':'Build Snake.'},source_bytes=1024)
                row=runtime.prepare({'mode':'build','prompt':'Build Snake.'});bad=deepcopy(row['bundle'])
                bad['whole_project_workers']['implementer']['auto_compaction']=False
                with self.assertRaisesRegex(ValueError,'all five roles'):validate(bad)
                bad=deepcopy(row['bundle']);bad['whole_project_java_api_probe']={'policy':'unexpected'}
                with self.assertRaisesRegex(ValueError,'declared configuration'):validate(bad)
                bad=deepcopy(row['bundle']);bad.pop('whole_project_stage_recovery_policy')
                with self.assertRaisesRegex(ValueError,'agent-consulted'):validate(bad)
                with self.assertRaises(ValueError):runtime.prepare({'mode':'build','build_profile':'offline-project-v1','prompt':'Conflict.'})
            finally:runtime.close()

    def test_general_defaults_do_not_change_historical_disabled_config_or_grants(self):
        self.assertNotIn('general_coding',BuildConfig().model_dump())
        self.assertIsNone(BuildConfig().general_coding)
        self.assertEqual(GeneralCodingConfig().options()['recovery_limits'],EXPANDED)
        self.assertNotIn('recovery_limits',GeneralCodingConfig(recovery='legacy').options())

    def test_general_uses_direct_evidence_bound_edits_without_a_continuation_hack(self):
        import test_repair_coordinator as repairs
        from orchestrator.whole_project import POLICY as ENGINE
        from orchestrator.reference_repair_coordinator import repair
        flow,suite=repairs.fixture();cfg=GeneralCodingConfig(source_bytes=65536,require_opencode=False)
        flow.bundle.update(build={'general_coding':cfg.model_dump()},whole_project_workflow=ENGINE,
            whole_project_source_bytes=65536,test_suite_bytes=65536,whole_project_recovery_limits=EXPANDED)
        from orchestrator.store import digest
        flow.bundle[KEY]={'policy':'general-coding-v1','options_digest':digest(cfg.model_dump())}
        original=flow._generate.side_effect
        def generate(stage,role,model_type,instruction,data,*args,**kwargs):
            if stage.endswith('_consult'):return original(stage,role,model_type,instruction,data,*args,**kwargs)
            self.assertNotIn('clusters',model_type.model_json_schema()['properties'])
            return {'summary':'Fix shared helper','edits':[{'path':'tests/test_math.py','before':'return 0','after':'return 1',
                'cause':'Both observed failures share the helper','evidence_refs':[f['evidence_id'] for f in data['failures']]}]}
        flow._generate.side_effect=generate
        flow.control=Mock(side_effect=lambda candidate:repairs.receipt(flow,candidate,failed=()))
        fixed=repair(flow,suite,repairs.receipt(flow,suite))
        self.assertIn('return 1',fixed.files[0].content)
        self.assertEqual(flow.control.call_count,2)


if __name__=='__main__':unittest.main()
