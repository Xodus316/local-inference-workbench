"""Static fixture diagnostics and bounded helper repairs; never execute supplied tests."""
import ast
import copy
import unittest
from types import SimpleNamespace

from orchestrator.build_models import BuildFile
from orchestrator.build_workflow import BuildRun, fixture_diagnostics
from orchestrator.project_build import (ProjectBuildRun, offline_project_contract,
                                        project_enabled)
from orchestrator.project_fixture_checks import POLICY, diagnostics, validate_repair


HTTP = '''import unittest
import json
import urllib.request

class API(unittest.TestCase):
    def request(self, method, path):
        with urllib.request.urlopen(path) as response:
            return response.getcode(), json.loads(response.read().decode('utf-8'))

    def other_helper(self):
        return 42

    def test_delete(self):
        status, body = self.request('DELETE', 'http://example.invalid/items/1')
        self.assertEqual(status, 204)
        self.assertIsNone(body)
'''
FIXED_HTTP = HTTP.replace(
    "            return response.getcode(), json.loads(response.read().decode('utf-8'))",
    "            raw = response.read()\n"
    "            return response.getcode(), json.loads(raw.decode('utf-8')) if raw else None")
AUTH = '''import unittest

class API(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.token = None

    def auth_header(self):
        return {'Authorization': f'Bearer {self.token}'}

    def test_login(self):
        self.token = 'login-result'
        self.assertEqual(self.token, 'login-result')

    def test_read(self):
        self.assertIn('Authorization', self.auth_header())
'''


def files(source):
    return [BuildFile(path="tests/test_api.py", content=source)]


def suite(source):
    return SimpleNamespace(files=files(source))


class FixtureDiagnosticsTests(unittest.TestCase):
    def test_exact_unguarded_delete_shape_without_a_204_assertion(self):
        source = HTTP.replace("        self.assertEqual(status, 204)\n", "")
        problems = diagnostics(suite(source))
        self.assertEqual([(d["kind"], d["helpers"]) for d in problems],
                         [("empty_http_json", ["request"])])
        self.assertEqual(problems[0]["policy"], POLICY)

    def test_json_read_variable_provenance_and_import_aliases(self):
        source = HTTP.replace("import json", "from json import loads as parse_json").replace(
            "import urllib.request", "from urllib.request import urlopen as fetch").replace(
            "urllib.request.urlopen", "fetch").replace(
            "            return response.getcode(), json.loads(response.read().decode('utf-8'))",
            "            raw = response.read()\n"
            "            text = raw.decode('utf-8')\n"
            "            return response.getcode(), parse_json(text)")
        self.assertEqual([d["kind"] for d in diagnostics(suite(source))], ["empty_http_json"])

    def test_guarded_empty_body_or_status_and_non_delete_helpers_are_not_flagged(self):
        status_guard = HTTP.replace("            return response.getcode(),",
            "            if response.getcode() == 204:\n                return 204, None\n"
            "            return response.getcode(),")
        for source in (FIXED_HTTP, status_guard, HTTP.replace("'DELETE'", "'GET'"),
                       HTTP.replace("unittest.TestCase", "object")):
            with self.subTest(source=source):
                self.assertEqual(diagnostics(suite(source)), [])

    def test_instance_credentials_cannot_supply_another_case(self):
        problem, = diagnostics(suite(AUTH))
        self.assertEqual(problem["kind"], "cross_case_instance_auth")
        self.assertEqual(problem["readers"], ["test_read"])
        self.assertEqual(problem["helpers"], ["auth_header"])

    def test_independent_class_and_per_case_setup_are_not_flagged(self):
        variants = [AUTH.replace("cls.token = None", "cls.token = 'independent-fixture-token'"),
                    AUTH.replace("    def auth_header", "    def setUp(self):\n"
                                 "        self.token = 'independent-fixture-token'\n\n    def auth_header")]
        for source in variants:
            self.assertEqual(diagnostics(suite(source)), [])

    def test_scan_does_not_import_or_execute_test_source(self):
        source = "raise RuntimeError('must not execute')\n" + HTTP
        self.assertEqual(len(diagnostics(suite(source))), 1)


class FixtureRepairGuardTests(unittest.TestCase):
    def validate(self, original, revised):
        validate_repair(files(original), files(revised), diagnostics(suite(original)))

    def test_helper_only_empty_body_repair_preserves_full_test_ast(self):
        self.validate(HTTP, FIXED_HTTP)
        self.assertEqual(diagnostics(suite(FIXED_HTTP)), [])
        before = ast.parse(HTTP).body[-1].body[-1]
        after = ast.parse(FIXED_HTTP).body[-1].body[-1]
        self.assertEqual(ast.dump(before), ast.dump(after))

    def test_setup_body_or_new_setup_can_initialize_independent_credentials(self):
        self.validate(AUTH, AUTH.replace("cls.token = None", "cls.token = 'fixture-token'"))
        original = AUTH.replace("    @classmethod\n    def setUpClass(cls):\n        cls.token = None\n",
                                "    token = None\n")
        revised = original.replace("    def auth_header", "    @classmethod\n"
            "    def setUpClass(cls):\n        cls.token = 'fixture-token'\n\n    def auth_header")
        self.validate(original, revised)

    def test_assertion_test_name_and_nonassertion_test_body_changes_are_rejected(self):
        for revised in (FIXED_HTTP.replace("status, 204", "status, 200"),
                        FIXED_HTTP.replace("test_delete", "test_changed"),
                        FIXED_HTTP.replace("'items/1'", "'items/2'").replace(
                            "http://example.invalid/items/1", "http://example.invalid/items/2"),
                        FIXED_HTTP.replace("        self.assertIsNone(body)",
                                           "        if False:\n            self.assertIsNone(body)")):
            with self.subTest(revised=revised), self.assertRaises(ValueError):
                self.validate(HTTP, revised)

    def test_unrelated_helper_import_class_attribute_and_signature_changes_rejected(self):
        for revised in (FIXED_HTTP.replace("return 42", "return 43"),
                        "import os\n" + FIXED_HTTP,
                        FIXED_HTTP.replace("class API(unittest.TestCase):", "class API(unittest.TestCase):\n    value = 1"),
                        FIXED_HTTP.replace("def request(self, method, path)", "def request(self, method, path, other=None)"),
                        FIXED_HTTP.replace("        with urllib", "        import os\n        with urllib"),
                        FIXED_HTTP + "\nraise RuntimeError('changed')\n"):
            with self.subTest(revised=revised), self.assertRaises(ValueError):
                self.validate(HTTP, revised)

    def test_helper_and_setup_skip_bypasses_are_rejected(self):
        for statement in ("self.skipTest('skip')", "raise unittest.SkipTest('skip')",
                          "unittest.expectedFailure(self.test_delete)"):
            revised = FIXED_HTTP.replace("        with urllib", "        " + statement + "\n        with urllib")
            with self.subTest(statement=statement), self.assertRaisesRegex(ValueError, "bypass"):
                self.validate(HTTP, revised)

    def test_diagnosed_consumer_that_is_itself_a_test_stays_protected(self):
        original = AUTH.replace("self.auth_header()", "f'Bearer {self.token}'")
        revised = original.replace("f'Bearer {self.token}'", "'Bearer fixture-token'")
        with self.assertRaises(ValueError):
            self.validate(original, revised)

    def test_added_setup_must_have_the_unittest_signature(self):
        for setup in ("def setUp(self, other):", "def setUp(self: print('annotation')):",
                      "def setUp(self) -> print('annotation'):"):
            revised = FIXED_HTTP.replace("    def request", "    " + setup + "\n        pass\n\n    def request")
            with self.subTest(setup=setup), self.assertRaisesRegex(ValueError, "unsupported setup"):
                self.validate(HTTP, revised)


class ProfileFixturePolicyTests(unittest.TestCase):
    def test_contract_binds_narrow_policy_and_two_repairs(self):
        contract = offline_project_contract()
        self.assertEqual(contract["test_fixture_policy"], POLICY)
        self.assertEqual(contract["max_test_fixture_repairs"], 2)
        bundle = {"request": {"build_profile": "offline-project-v1"},
                  "build": {"offline_project_enabled": True}, "build_contract": contract,
                  "roles": {"implementer": "code", "repairer": "code", "test_author": "tests"}}
        self.assertTrue(project_enabled(bundle))
        for key, value in (("test_fixture_policy", "unchecked"), ("max_test_fixture_repairs", 3)):
            changed = copy.deepcopy(bundle)
            changed["build_contract"][key] = value
            with self.assertRaises(ValueError):
                project_enabled(changed)

    def test_ordinary_python_fixture_policy_remains_unchanged(self):
        self.assertEqual(fixture_diagnostics(suite(HTTP)), [])
        self.assertEqual(BuildRun.fixture_repair_constraints, "")
        self.assertIn("fresh instance", ProjectBuildRun.test_brief)
        self.assertIn("malformed nonempty JSON", ProjectBuildRun.test_brief)


if __name__ == "__main__":
    unittest.main()
