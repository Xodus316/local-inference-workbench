from copy import deepcopy
import unittest

from orchestrator.recovery_limits import KEY,EXPANDED,approved
from orchestrator.repair_allowance import output_limit,coordinated_limit
from orchestrator.stage_recovery import validate_costs,KEY as RECOVERY,DYNAMIC_POLICY
from orchestrator.whole_project_checkpoint import binding_digest
import test_whole_project_setup as fixtures


class FreshRecoveryLimitsTests(unittest.TestCase):
    def setUp(self):
        scenario=fixtures.FreshSetupTests()
        self.addCleanup(scenario.doCleanups)
        scenario.setUp()
        self.runtime=scenario.runtime

    def test_fresh_approval_freezes_explicit_limits_without_importing_prior_work(self):
        from orchestrator.whole_project_setup import configure_fresh_bundle,validate_fresh_run
        value=configure_fresh_bundle(self.runtime,fixtures.bundle(),source_bytes=128*1024,recovery_limits=EXPANDED)
        self.assertEqual(approved(value),EXPANDED)
        self.assertEqual((output_limit(value),coordinated_limit(value)),(16,16))
        self.assertTrue(validate_fresh_run(value))
        self.assertIsNone(value['fresh_project']['application_seed'])
        self.assertEqual(value['fresh_project']['initial_general_repairs'],0)
        validate_costs(value,{'recovery_output_correction':16,'coordinated_patch':16,'build_tests_defect':16})
        for key in ['recovery_output_correction','coordinated_patch','build_tests_defect']:
            with self.assertRaises(ValueError):validate_costs(value,{key:17})
        old=deepcopy(value);old.pop(KEY)
        self.assertNotEqual(binding_digest(old),binding_digest(value))
        self.assertEqual((output_limit(old),coordinated_limit(old)),(4,6))

    def test_bad_or_unapproved_limits_are_rejected(self):
        for bad in [{**EXPANDED,'output_corrections':17},{**EXPANDED,'test_defect_repairs':16.0},None,{}]:
            with self.subTest(bad=bad),self.assertRaises(ValueError):approved({KEY:bad,RECOVERY:DYNAMIC_POLICY,'whole_project_repair_policy':'verified-scoped-repair-v1'})
        with self.assertRaises(ValueError):approved({KEY:EXPANDED})

    def test_explicit_limits_do_not_silently_override_an_older_checkpoint_grant(self):
        from orchestrator.checkpoint_repair_continuation import POLICY,budgets
        value={KEY:EXPANDED,RECOVERY:DYNAMIC_POLICY,'whole_project_repair_policy':'verified-scoped-repair-v1',
            'whole_project_stage_continuation':{'policy':POLICY,'fresh_budgets':budgets()}}
        with self.assertRaisesRegex(ValueError,'differ'):approved(value)
        value['whole_project_stage_continuation']['fresh_budgets']=budgets(True)
        self.assertEqual(approved(value),EXPANDED)
