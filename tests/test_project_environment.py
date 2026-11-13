"""Environment metadata and pre-implementation compatibility; CPU fakes only."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from orchestrator.build_coverage import TestSyntaxError
from orchestrator.config import BuildConfig, Config
from orchestrator.project_environment import (ProjectEnvironment, suite_environment_issues,
    validate_project_environment, validate_suite_environment)
from orchestrator.runtime import Runtime
from orchestrator.store import digest

from test_project_build import BRIEF, COMMAND, TEST, Executor, Models


def environment(**changes):
    return ProjectEnvironment.model_validate({
        "required_application_files": ["api/App.java", "frontend/App.vue"],
        "python_modules": [], "tools": [{"name": "node", "path": "/usr/bin/node"}],
        "api": {"base_url_env": "API_BASE_URL", "base_url_kind": "api_root", "path_prefix": "/api"},
        "frontend_http_server": False, "frontend_base_url_env": "FRONTEND_BASE_URL",
        **changes}).model_dump()


def suite(source=TEST["content"], paths=None):
    return SimpleNamespace(implementation_files=paths if paths is not None else ["api/App.java", "frontend/App.vue"],
        files=[SimpleNamespace(path=TEST["path"], content=source)])


class EnvironmentSchemaTests(unittest.TestCase):
    def test_optional_environment_and_strict_canonical_binding(self):
        self.assertIsNone(BuildConfig().project_environment)
        self.assertIsNone(validate_project_environment({"build": {}}))
        value = environment()
        bundle = {"build": {"project_environment": value}, "project_environment": copy.deepcopy(value)}
        self.assertEqual(validate_project_environment(bundle), value)
        for field in ("project_environment", "build"):
            changed = copy.deepcopy(bundle)
            if field == "build":
                changed["build"]["project_environment"]["frontend_http_server"] = True
            else:
                changed.pop(field)
            with self.assertRaises(ValueError):
                validate_project_environment(changed)
        with self.assertRaises(ValueError):
            validate_project_environment({"build": {"project_environment": {}}, "project_environment": {}})

    def test_python_version_is_optional_and_bound_to_approval(self):
        self.assertNotIn("python_version", environment())
        value = environment(python_version="3.10")
        self.assertEqual(value["python_version"], "3.10")
        bundle = {"build": {"project_environment": value}, "project_environment": copy.deepcopy(value)}
        self.assertEqual(validate_project_environment(bundle), value)
        bundle["project_environment"]["python_version"] = "3.12"
        with self.assertRaisesRegex(ValueError, "frozen for approval"):
            validate_project_environment(bundle)
        for version in ("3.10.12", "2.7", "3.latest", 310):
            with self.subTest(version=version), self.assertRaises(ValidationError):
                environment(python_version=version)

    def test_metadata_has_no_credentials_values_or_implementation_fields(self):
        for field in ("environment_values", "api_key", "source", "reference_implementation"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ProjectEnvironment.model_validate({field: "not permitted"})

    def test_paths_are_portable_bounded_distinct_and_outside_tests(self):
        for paths in (["../app.js"], ["C:/app.js"], ["tests/test_app.py"], ["a", "A"],
                      [f"src/{i}.js" for i in range(129)]):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                environment(required_application_files=paths)
        for path in ("C:/node.exe", "../node", "/opt/../node", "/opt//node", "/opt/node\n"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                environment(tools=[{"name": "node", "path": path}])

    def test_metadata_total_and_inventory_bounds(self):
        with self.assertRaisesRegex(ValueError, "8192"):
            environment(required_application_files=[f"src/{i}/" + "a" * 100 for i in range(100)])
        for changes in ({"python_modules": ["requests"] * 2},
                        {"python_modules": ["x" + str(i) for i in range(65)]},
                        {"tools": [{"name": "node", "path": "/bin/node"}] * 2}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                environment(**changes)

    def test_url_semantics_use_names_and_prefixes_without_embedded_urls(self):
        for prefix in ("api", "/api/", "/../api", "/api?token=value"):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                environment(api={"base_url_env": "API_BASE_URL", "base_url_kind": "api_root", "path_prefix": prefix})
        with self.assertRaises(ValueError):
            environment(frontend_base_url_env="API_BASE_URL")
        with self.assertRaises(ValueError):
            environment(frontend_http_server="false")


class SuiteEnvironmentTests(unittest.TestCase):
    def kinds(self, source, **changes):
        return [i["kind"] for i in suite_environment_issues(suite(source), environment(**changes))]

    def test_missing_required_declaration_stops_before_source_generation(self):
        with self.assertRaisesRegex(ValueError, "api/App.java"):
            validate_suite_environment(suite(paths=["frontend/App.vue"]), environment())

    def test_checkpoint_environment_validation_checks_declared_python_grammar(self):
        source = "value = f'/links/{item['id']}/click'\n"
        with self.assertRaises(TestSyntaxError) as error:
            validate_suite_environment(suite(source), environment(python_version="3.10"))
        self.assertEqual(error.exception.diagnostics[0]["python_version"], "3.10")

    def test_stdlib_declared_dependencies_and_local_modules_are_allowed(self):
        source = "import json, urllib.request, unittest\nfrom helper import check\nimport requests\n" + TEST["content"]
        value = suite(source)
        value.files.append(SimpleNamespace(path="tests/helper.py", content="def check(): return True\n"))
        validate_suite_environment(value, environment(python_modules=["requests"]))

    def test_explicitly_unavailable_python_import_has_source_location(self):
        source = "from requests import Session\n" + TEST["content"]
        problem, = suite_environment_issues(suite(source), environment())
        self.assertEqual((problem["kind"], problem["line"]), ("unsupported_python_import", 1))
        self.assertIn("requests", problem["message"])
        self.assertEqual(self.kinds(source, python_modules=None), [])

    def test_literal_tool_argv_matches_names_or_container_paths(self):
        source = "import subprocess as sp\nsp.run(['node', '--version'])\nsp.run(['/usr/bin/node', '--version'])\n"
        self.assertEqual(self.kinds(source), [])
        self.assertEqual(self.kinds(source.replace("'node'", "'unlisted-tool'", 1)), ["unsupported_tool"])
        self.assertEqual(self.kinds("from subprocess import run\nrun(args=['unlisted-tool'])\n"), ["unsupported_tool"])
        self.assertEqual(self.kinds("import subprocess\nsubprocess.run(['unlisted-tool'])\n", tools=None), [])

    def test_api_root_prefix_is_not_appended_twice_directly_or_through_helper(self):
        for source in ("import os\nbase = os.environ['API_BASE_URL']\nurl = base + '/api/items'\n",
                       "import os\nbase = os.getenv('API_BASE_URL').rstrip('/')\nurl = f'{base}/api/items'\n",
                       "import os\nclass API:\n"
                       "    def setup(self): self.base = os.environ.get('API_BASE_URL')\n"
                       "    def request(self, method, path): return self.base + path\n"
                       "    def test_read(self): self.request('GET', '/api/items')\n"):
            with self.subTest(source=source):
                self.assertIn("duplicated_api_prefix", self.kinds(source))

    def test_origin_prefix_and_api_relative_paths_are_valid(self):
        source = "import os\nbase = os.environ['API_BASE_URL']\nurl = base + '/api/items'\n"
        self.assertEqual(self.kinds(source, api={"base_url_env": "API_BASE_URL", "base_url_kind": "origin",
                                                "path_prefix": "/api"}), [])
        self.assertEqual(self.kinds(source.replace("'/api/items'", "'/items'")), [])

    def test_ambiguous_reassignment_and_unrelated_helper_names_are_not_guessed(self):
        source = "import os\nbase = os.environ['API_BASE_URL']\nbase = 'http://example.invalid'\nurl = base + '/api/items'\n"
        self.assertEqual(self.kinds(source), [])
        source = ("import os\nclass API:\n"
                  "    def setup(self): self.api = os.environ['API_BASE_URL']\n"
                  "    def request(self, path): return self.api + path\n"
                  "class Other:\n"
                  "    def request(self, path): return 'http://example.invalid' + path\n"
                  "    def test_read(self): return self.request('/api/items')\n")
        self.assertEqual(self.kinds(source), [])

    def test_explicit_frontend_server_absence_and_unknown_capabilities(self):
        source = "import os\nurl = os.environ.get('FRONTEND_BASE_URL')\n"
        self.assertEqual(self.kinds(source), ["frontend_server_unavailable"])
        self.assertEqual(self.kinds(source, frontend_http_server=True), [])
        self.assertEqual(self.kinds(source, frontend_http_server=None), [])
        validate_suite_environment(suite("import not_provisioned\nraise RuntimeError('not executed')\n"), None)

    def test_supplied_source_is_never_executed_and_dynamic_assumptions_are_not_guessed(self):
        self.assertEqual(self.kinds("raise RuntimeError('not executed')\n"), [])
        self.assertEqual(self.kinds("import subprocess\nsubprocess.run(command)\n"), [])


class EnvironmentWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.models, self.executor = Models(), Executor()
        self.runtime = Runtime(Path(temp.name), Config(build={"offline_project_enabled": True,
            "project_environment": environment()}), self.models, self.executor)
        self.addCleanup(self.runtime.close)

    def prepare(self):
        return self.runtime.prepare({"mode": "build", "prompt": BRIEF, "build_profile": "offline-project-v1"})

    def execute(self):
        row = self.prepare()
        self.runtime.approve(row["id"], row["digest"])
        self.runtime.queue.join()
        return self.runtime.store.get(row["id"])

    def test_canonical_environment_is_frozen_and_supplied_to_every_stage(self):
        row = self.execute()
        self.assertEqual(row["status"], "completed", row.get("result"))
        frozen = row["bundle"]["project_environment"]
        self.assertEqual(frozen, row["bundle"]["build"]["project_environment"])
        self.assertEqual(len(self.models.seen), 7)
        for stage, _, _, payload in self.models.seen:
            with self.subTest(stage=stage):
                data = json.loads(payload["messages"][1]["content"])
                self.assertEqual(data["project_environment"], frozen)
                self.assertEqual(data["prompt"], BRIEF)
                self.assertIn("api_root", data["environment_instructions"])

    def test_environment_change_invalidates_prepared_approval_without_model_call(self):
        row = self.prepare()
        self.runtime.config.build.project_environment.frontend_http_server = True
        with self.assertRaisesRegex(ValueError, "Configuration changed"):
            self.runtime.approve(row["id"], row["digest"])
        self.assertEqual(self.models.seen, [])

    def test_unsupported_test_dependency_stops_before_grouping_or_implementation(self):
        self.models.test_file = {**TEST, "content": "import requests\n" + TEST["content"]}
        row = self.execute()
        self.assertEqual(row["status"], "failed")
        self.assertEqual([call["stage"] for call in row["calls"]], ["build_plan", "build_tests"])
        self.assertIn("requests", row["result"]["output"]["error"]["message"])
        self.assertNotIn("build_frozen", row["result"]["stages"])
        self.assertEqual(self.executor.tests, [])

    def test_embedded_javascript_fixture_is_repaired_before_freeze(self):
        from test_project_fixture_checks import UI, FIXED_UI

        def test_file(source):
            return {**TEST, "content": source.replace("class UI(", "class API(").replace("test_view", "test_api")}

        self.runtime.config.build.project_environment.python_version = "3.10"
        self.models.test_file = test_file(UI)
        self.models.fixture_repairs = [test_file(FIXED_UI)]
        row = self.execute()
        self.assertEqual(row["status"], "completed", row.get("result"))
        self.assertEqual([call["stage"] for call in row["calls"]][:4],
                         ["build_plan", "build_tests", "build_tests_fixture_1", "build_project_plan"])
        self.assertEqual(row["result"]["output"]["suite"]["files"], [test_file(FIXED_UI)])

    def test_missing_required_file_stops_before_implementation(self):
        self.runtime.config.build.project_environment.required_application_files.append("api/bootstrap.gradle")
        row = self.execute()
        self.assertEqual(row["status"], "failed")
        self.assertIn("api/bootstrap.gradle", row["result"]["output"]["error"]["message"])
        self.assertEqual([call["stage"] for call in row["calls"]], ["build_plan", "build_tests"])

    def test_old_approval_compatibility_only_when_new_metadata_is_absent(self):
        self.runtime.config.build.project_environment = None
        row = self.prepare()
        old = self.runtime.config.model_dump()
        old["build"].pop("project_environment")
        self.assertTrue(self.runtime._config_matches({**row["bundle"], "config_digest": digest(old)}))
        self.runtime.config.build.project_environment = ProjectEnvironment()
        self.assertFalse(self.runtime._config_matches({**row["bundle"], "config_digest": digest(old)}))


if __name__ == "__main__":
    unittest.main()
