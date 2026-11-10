"""Direct source owners remain explicit through a diagnosed group-plan correction."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.config import Config
from orchestrator.project_build import POLICY, validate_group_plan
from orchestrator.runtime import Runtime
from test_project_build import BRIEF, COMMAND, COVERAGE, TEST, Executor, Models


PATHS = ["api/ApiException.java", "api/App.java", "frontend/App.vue"]


def plan():
    return {"contract": "Backend exceptions and catalog API serve the frontend", "groups": [
        {"id": "api_base", "files": PATHS[:1], "depends_on": [], "context_files": [],
         "interface": "Provide the shared exception type"},
        {"id": "api_catalog", "files": PATHS[1:2], "depends_on": ["api_base"],
         "context_files": PATHS[:1], "interface": "Provide the catalog API"},
        {"id": "api_tracking_reports", "files": PATHS[2:], "depends_on": ["api_catalog"],
         "context_files": PATHS[:2], "interface": "Present catalog and tracking reports"}]}


class ContextModels(Models):
    def __init__(self):
        super().__init__()
        bad = plan()
        good = copy.deepcopy(bad)
        good["groups"][2]["depends_on"] = ["api_base", "api_catalog"]
        self.group_plans = [bad, good]

    def complete(self, *args):
        if self.stage == "build_tests":
            value = {"implementation_files": PATHS, "files": [TEST], "commands": [COMMAND],
                     "coverage": [COVERAGE], "assumptions": []}
        elif self.stage.startswith("implement_group_"):
            index = int(self.stage.rsplit("_", 1)[1]) - 1
            value = {"summary": "Complete group source", "files": [{"path": PATHS[index],
                     "content": ["exception type", "correct backend", "frontend source"][index]}]}
        else:
            return super().complete(*args)
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}


class GroupContextTests(unittest.TestCase):
    def test_transitive_owner_is_named_and_still_requires_a_direct_dependency(self):
        value = plan()
        before = copy.deepcopy(value)
        with self.assertRaises(ValueError) as caught:
            validate_group_plan(value, PATHS)
        message = str(caught.exception)
        self.assertIn("Group api_tracking_reports:", message)
        self.assertIn("'api/ApiException.java': 'api_base'", message)
        self.assertIn("missing_direct_depends_on=['api_base']", message)
        self.assertIn("transitive dependency", message)
        self.assertEqual(value, before)
        value["groups"][2]["depends_on"].insert(0, "api_base")
        self.assertEqual(validate_group_plan(value, PATHS).groups[2].context_files, PATHS[:2])

    def test_duplicate_or_unknown_context_is_reported_without_inventing_an_owner(self):
        value = plan()
        value["groups"][2]["context_files"] = ["unknown.java", "unknown.java"]
        with self.assertRaises(ValueError) as caught:
            validate_group_plan(value, PATHS)
        self.assertIn("'unknown.java': 'no earlier owner'", str(caught.exception))
        self.assertIn("duplicate_context_files=['unknown.java']", str(caught.exception))
        self.assertIn("missing_direct_depends_on=[]", str(caught.exception))

    def test_terminal_bad_plan_receives_precise_feedback_and_correction_reaches_implementation(self):
        with tempfile.TemporaryDirectory() as directory:
            models, executor = ContextModels(), Executor()
            runtime = Runtime(Path(directory), Config(build={"offline_project_enabled": True}), models, executor)
            try:
                row = runtime.prepare({"mode": "build", "prompt": BRIEF, "build_profile": POLICY})
                runtime.approve(row["id"], row["digest"])
                runtime.queue.join()
                result = runtime.store.get(row["id"])
            finally:
                runtime.close()
        self.assertEqual(result["status"], "completed", result.get("result"))
        stages = [c["stage"] for c in result["calls"]]
        self.assertEqual(stages, ["build_plan", "build_tests", "build_project_plan", "build_project_plan_repair_1",
                                  "implement_group_1", "implement_group_2", "implement_group_3"])
        frozen = result["result"]["stages"]["build_project_frozen"]
        self.assertEqual(frozen["format_repairs"], 1)
        self.assertIn("'api/ApiException.java': 'api_base'", frozen["validation_failures"][0]["validation_error"])
        correction = next(payload for stage, _, _, payload in models.seen if stage == "build_project_plan_repair_1")
        self.assertIn("transitive dependency", correction["messages"][0]["content"])
        data = json.loads(correction["messages"][1]["content"])
        self.assertIn("missing_direct_depends_on=['api_base']", data["group_plan_feedback"]["validation_error"])
        last = next(payload for stage, _, _, payload in models.seen if stage == "implement_group_3")
        self.assertEqual([f["path"] for f in json.loads(last["messages"][1]["content"])["selected_source"]], PATHS[:2])
        self.assertEqual(executor.tests, [[TEST]])


if __name__ == "__main__":
    unittest.main()
