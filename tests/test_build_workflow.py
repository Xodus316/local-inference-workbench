"""Exercise the approved Build graph with scripted models and isolated executor receipts."""
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from time import monotonic

from orchestrator.config import Config, Generation
from orchestrator.runtime import Runtime
from orchestrator.store import digest


CONTROL = "# independent-reference-only\ndef positive(n):\n    return n >= 0\n"
CORRECT = "def positive(n):\n    return n >= 0\n"
BROKEN = "def positive(n):\n    return False\n"


def file(path, content):
    return {"path": path, "content": content}


def case(name, n, expected):
    path = f"tests/test_{name}.py"
    method = f"test_{name}"
    source = ("import unittest\nfrom solution import positive\n\n"
              f"class Signs(unittest.TestCase):\n    def {method}(self):\n"
              f"        self.assertEqual(positive({n}), {expected})\n")
    return file(path, source), {"criterion": 1, "test_file": path, "test_name": f"Signs.{method}",
                               "scenario": f"Sign of {n}", "expected_result": str(expected),
                               "catches": "Incorrect classification"}


positive_test, positive_cov = case("positive", 2, True)
negative_test, negative_cov = case("negative", -3, False)
negative_cov["criterion"] = 2
zero_test, zero_cov = case("zero", 0, True)


class Models:
    def __init__(self):
        self.seen = []
        self.stage = None
        self.tamper = False
        self.bad_command = False

    def admit(self, model, settings, payload, stage):
        self.stage = stage
        self.seen.append((stage, model, settings, payload))

    def complete(self, *args):
        if self.stage == "build_plan":
            value = {"goal": "Classify a sign", "acceptance_criteria": ["Accept nonnegative", "Reject negative"],
                     "implementation_steps": ["Define positive"], "test_strategy": ["Check each sign"],
                     "assumptions": [], "risks": []}
        elif self.stage == "build_tests":
            broken = {**positive_test, "content": positive_test["content"].replace(", True)", ", true")}
            value = {"implementation_files": ["solution.py"], "files": [broken],
                     "commands": ["exit 0" if self.bad_command else "python3 -m unittest discover -s tests -v"],
                     "coverage": [positive_cov], "assumptions": []}
        elif self.stage == "build_tests_syntax_1":
            value = {"files": [{**positive_test, "content": positive_test["content"].replace("True", "true")}]}
        elif self.stage == "build_tests_fixture_1":
            value = {"files": [positive_test]}
        elif self.stage == "build_tests_coverage_1":
            value = {"files": [negative_test], "coverage": [negative_cov]}
        elif self.stage == "implement":
            value = {"summary": "First implementation", "files": [file("solution.py", BROKEN)]}
        elif self.stage == "repair_1":
            value = {"diagnosis": {"diagnosis": ["All inputs return false"], "changes": ["Compare to zero"],
                                   "verification": ["Run frozen tests"]},
                     "artifact": {"summary": "Corrected sign", "files": [file("solution.py", CORRECT)]}}
            if self.tamper:
                value["artifact"]["files"].append(file(positive_test["path"], ""))
        elif self.stage == "build_strengthen_1":
            value = {"files": [zero_test], "coverage": [zero_cov]}
        else:
            raise AssertionError(self.stage)
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}


class Executor:
    """Model the sign predicate without evaluating any generated source on the host."""
    def __init__(self):
        self.projects = []

    def preflight(self):
        return {"engine": "docker", "image": "python:3.13-alpine", "image_id": "sha256:" + "a" * 64}

    def materialize(self, run_id, attempt, project):
        self.projects.append(project)
        return project

    def test(self, run_id, attempt, project, commands, image, cancel, deadline, read_only_project):
        assert read_only_project and image == self.preflight()["image_id"]
        source = next(f.content for f in project.files if f.path == "solution.py")
        results = []
        for i, label in [(2, "positive"), (-3, "negative"), (0, "zero")]:
            if not any(f.path == f"tests/test_{label}.py" for f in project.files):
                continue
            expected = i >= 0
            observed = (i > 0 if "n > 0" in source else i >= 0) if "False" not in source else False
            results.append((label, observed == expected))
        failures = sum(not success for _, success in results)
        log = "\n".join(f"test_{label} (test_{label}.Signs.test_{label}) ... " + ("ok" if passed else "FAIL")
                        for label, passed in results)
        log += f"\n----------------------------------------------------------------------\nRan {len(results)} tests in 0.01s\n\n"
        log += f"FAILED (failures={failures})\n" if failures else "OK\n"
        return {"status": "failed" if failures else "passed", "commands": [{
            "exit_code": int(bool(failures)), "timed_out": False, "cancelled": False,
            "cleanup_confirmed": True, "command_index": 1, "stdout": "", "stderr": log}]}


class BuildWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.models = Models()
        self.executor = Executor()
        self.runtime = Runtime(Path(temp.name), Config(), self.models, self.executor)
        self.addCleanup(self.runtime.close)

    def execute(self):
        row = self.runtime.prepare({"mode": "build", "prompt": "Implement a nonnegative predicate in solution.py",
                                    "control_files": [file("solution.py", CONTROL)]})
        self.assertEqual(self.models.seen, [])
        self.runtime.approve(row["id"], row["digest"])
        self.runtime.queue.join()
        return self.runtime.store.get(row["id"])

    def test_independent_build_frozen_tests_and_strengthening(self):
        row = self.execute()
        self.assertEqual(row["status"], "completed", row["result"])
        self.assertEqual([c["stage"] for c in row["calls"]], [
            "build_plan", "build_tests", "build_tests_syntax_1", "build_tests_coverage_1",
            "build_tests_fixture_1", "implement", "repair_1", "build_strengthen_1"])
        for stage, model, settings, payload in self.models.seen:
            self.assertNotIn("independent-reference-only", json.dumps(payload))
            self.assertIsNone(settings["output_tokens"])
            if stage in {"implement", "repair_1"}:
                self.assertEqual(model, "qwen3.8:27b-mtp-q4_K_M")
                self.assertEqual(settings["think"], "xhigh")
                self.assertEqual(settings["context"], 98304)
            if stage == "build_strengthen_1":
                self.assertNotIn('"execution"', payload["messages"][1]["content"])
        output = row["result"]["output"]
        self.assertEqual(output["suite_digest"], digest(output["suite"]))
        self.assertEqual(output["repair_attempts"], 1)
        self.assertEqual(output["suite"]["files"], [positive_test, negative_test, zero_test])
        self.assertEqual(output["artifact"]["files"], [file("solution.py", CORRECT), positive_test, negative_test, zero_test])
        self.assertEqual([x["status"] for x in output["verification"]["controls"]], ["passed", "passed"])
        self.assertEqual([x["status"] for x in output["verification"]["strength"]], ["review_required", "passed"])
        self.assertEqual([x["status"] for x in output["verification"]["baselines"]], ["failed", "passed", "passed"])

    def test_explicit_implementation_model_and_native_profile_remain_available(self):
        self.runtime.config.roles["implementer"] = "custom:implementer"
        self.runtime.config.generation["implementer"] = Generation(context=65536, think=None)
        row = self.execute()
        self.assertEqual(row["status"], "completed", row["result"])
        _, model, settings, _ = next(call for call in self.models.seen if call[0] == "implement")
        self.assertEqual(model, "custom:implementer")
        self.assertEqual(settings["context"], 65536)
        self.assertIsNone(settings["think"])
        self.assertIsNone(settings["output_tokens"])

    def test_repair_cannot_write_any_frozen_test(self):
        self.models.tamper = True
        row = self.execute()
        self.assertEqual(row["status"], "failed")
        self.assertIsNone(row["result"]["output"]["artifact"])
        self.assertIn("tests directory", row["result"]["output"]["error"]["message"])
        self.assertEqual(len(row["result"]["output"]["verification"]["baselines"]), 1)

    def test_deceptive_test_command_fails_before_implementation(self):
        self.models.bad_command = True
        row = self.execute()
        self.assertEqual(row["status"], "failed")
        self.assertNotIn("implement", [c["stage"] for c in row["calls"]])
        self.assertFalse(self.executor.projects)

    def test_ambiguous_prior_container_execution_is_not_replayed(self):
        row = self.runtime.prepare({"mode": "build", "prompt": "Make a CLI"})
        self.runtime.events[row["id"]] = Event()
        self.runtime.deadline = monotonic() + 60
        self.runtime.store.save_artifact(row["id"], "build_verify_0", "request", {"scope": "existing"})
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.runtime._build_receipt(row["id"], "build_verify_0", {"scope": "existing"},
                                        lambda: self.fail("replayed interrupted execution"))
        self.runtime.events.pop(row["id"])
