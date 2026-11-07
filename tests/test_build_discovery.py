"""Coverage metadata must identify tests that unittest actually executed."""
import io
import unittest
from types import SimpleNamespace

from orchestrator.build_discovery import covered_cases_ran, validate_discovery_suite


class DiscoveryTests(unittest.TestCase):
    def suite(self, *refs, command="python3 -m unittest discover -s tests -v"):
        coverage = [SimpleNamespace(test_file=path, test_name=name) for path, name in refs]
        return SimpleNamespace(commands=[command], coverage=coverage)

    def receipt(self, stderr, stdout=""):
        return {"status": "passed", "commands": [{"stderr": stderr, "stdout": stdout, "exit_code": 0,
                "timed_out": False, "cancelled": False, "cleanup_confirmed": True}]}

    def test_real_unittest_ignores_plain_class_and_records_docstrings(self):
        class Examples(unittest.TestCase):
            def test_actual(self):
                """A real test with a docstring on its own output line."""
                self.assertEqual(4, 2 + 2)

        class Plain:
            def test_missing(self):
                raise AssertionError("unittest never runs this")

        Examples.__module__ = "test_example"
        Examples.__qualname__ = "Examples"
        module = SimpleNamespace(Examples=Examples, Plain=Plain)
        stream = io.StringIO()
        unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(module))
        receipt = self.receipt(stream.getvalue())
        found = ("tests/test_example.py", "Examples.test_actual")
        missed = ("tests/test_example.py", "Plain.test_missing")
        self.assertTrue(covered_cases_ran(receipt, self.suite(found, found)))
        self.assertFalse(covered_cases_ran(receipt, self.suite(found, missed)))

    def test_module_identity_and_older_unittest_format(self):
        expected = self.suite(("tests/pkg/test_mod.py", "Examples.test_case"))
        self.assertTrue(covered_cases_ran(self.receipt("test_case (pkg.test_mod.Examples) ... ok\nRan 1 test in 0s\nOK"), expected))
        wrong = self.receipt("test_case (other.test_mod.Examples) ... ok\nRan 1 test in 0s\nOK")
        self.assertFalse(covered_cases_ran(wrong, expected))

    def test_disallow_skips_errors_and_truncated_or_spoofed_output(self):
        expected = self.suite(("tests/test_mod.py", "Examples.test_case"))
        good = "test_case (test_mod.Examples.test_case) ... ok\n"
        for status in ("skipped 'placeholder'", "ERROR", "expected failure", "FAIL"):
            with self.subTest(status=status):
                log = good.replace(" ... ok", " ... " + status) + "Ran 1 test in 0s\n"
                self.assertFalse(covered_cases_ran(self.receipt(log, stdout=good), expected))
        self.assertFalse(covered_cases_ran(self.receipt(good + "Ran 2 tests in 0s\nOK"), expected))
        self.assertFalse(covered_cases_ran(self.receipt(good * 2 + "Ran 2 tests in 0s\nOK"), expected))
        self.assertFalse(covered_cases_ran(self.receipt("Ran 1 test in 0s\nOK", stdout=good), expected))

    def test_reject_invalid_test_references_and_shell_commands_before_execution(self):
        for args in [
            ("tests/test_mod.py", "test_module_function", "python -m unittest discover -s tests -v"),
            ("tests/test_mod.sh", "Suite.test_case", "python -m unittest discover -s tests -v"),
            ("tests/test_mod.py", "Suite.test_case", "true; python -m unittest discover -s tests -v"),
        ]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                validate_discovery_suite(self.suite(args[:2], command=args[2]))
        validate_discovery_suite(self.suite(("tests/test_mod.py", "Suite.test_case")))
