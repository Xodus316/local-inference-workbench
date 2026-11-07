import threading
import time
import unittest
from types import SimpleNamespace

from orchestrator import build_strength
from orchestrator.build_models import BuildProject


def result(stderr, code=0, *, timeout=False, cleaned=True):
    row = dict(stderr=stderr, stdout="", exit_code=code, timed_out=timeout,
               cancelled=False, cleanup_confirmed=cleaned)
    return {"status": "passed" if code == 0 else "failed", "commands": [row]}


PASS = result("Ran 1 test in 0.01s\n\nOK\n")
FAIL = result("Ran 1 test in 0.01s\n\nFAILED (failures=1)\n", 1)
HUNG = result("test_control (example.Case.test_control) ... ok\n"
              "test_result (example.Case.test_result) ... ", None, timeout=True)


class Executor:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.attempts = []

    def materialize(self, run_id, attempt, project):
        self.attempts.append((attempt, project))
        return "workspace"

    def test(self, *args, read_only_project):
        assert read_only_project
        return next(self.outputs)


class MutationGate(unittest.TestCase):
    def setUp(self):
        self.project = BuildProject(summary="Example", files=[
            {"path": "solution.py", "content": "def f(x):\n    return x + 1\n"},
            {"path": "tests/test_f.py", "content": "def test_f():\n    assert 2 + 2 == 4\n"}])
        self.suite = SimpleNamespace(commands=[build_strength.COMMANDS[0]], model_dump=lambda: {})
        self.bundle = {"sandbox": {"image_id": "example"}, "build": {"max_test_mutations": 1}}

    def run_gate(self, *outputs):
        self.executor = Executor(outputs)
        runtime = SimpleNamespace(_guard=lambda _: None, bundle=lambda _: self.bundle,
                                  deadline=time.monotonic()+20, events={"id": threading.Event()},
                                  build_executor=self.executor)
        return build_strength.check(runtime, {"run_id": "id"}, self.project, self.suite, PASS)

    def test_mutates_only_implementation_and_requires_assertion(self):
        self.assertEqual(self.run_gate(FAIL)["status"], "passed")
        _, edited = self.executor.attempts[0]
        self.assertEqual(edited.files[1], self.project.files[1])
        self.assertNotEqual(edited.files[0], self.project.files[0])
        report = self.run_gate(result("Ran 1 test in 0.01s\nFAILED (errors=1)\n", 1))
        self.assertEqual(report["summary"]["inconclusive"], 1)
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(self.run_gate(PASS)["summary"]["survived"], 1)

    def test_timeout_needs_matching_test_and_passing_controls(self):
        report = self.run_gate(HUNG, PASS, HUNG, PASS)
        self.assertEqual(report["summary"]["detected_timeout"], 1)
        self.assertEqual(len(set(i for i, _ in self.executor.attempts)), 4)
        other = result("test_another (example.Case.test_another) ... ", None, timeout=True)
        self.assertEqual(self.run_gate(HUNG, PASS, other, PASS)["status"], "review_required")
        self.assertEqual(self.run_gate(HUNG, PASS, HUNG, FAIL)["status"], "review_required")

    def test_timeout_can_identify_verbose_case_with_docstring(self):
        hung = result("test_result (example.Case.test_result)\nCompute a result. ... ", None, timeout=True)
        self.assertEqual(build_strength.timeout_signature(hung), "test_result (example.Case.test_result)")

    def test_cleanup_and_empty_discovery(self):
        for stderr, cleaned in (("Ran 0 tests in 0.01s\nOK\n", True),
                                ("Ran 1 test in 0.01s\nOK\n", False)):
            self.assertEqual(build_strength.outcome(result(stderr, cleaned=cleaned)), "inconclusive")
