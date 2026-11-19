"""Only provable class-receiver unittest call errors trigger fixture correction."""

import ast
import unittest

from orchestrator.unittest_fixture_lint import class_assertion_problems


class ClassFixtureLintTests(unittest.TestCase):
    def problems(self, source):
        return class_assertion_problems("tests/test_smoke.py", ast.parse(source))

    def test_observed_setupclass_call_fails_before_all_five_tests(self):
        source = ("import unittest\nclass Smoke(unittest.TestCase):\n"
                  "    @classmethod\n    def setUpClass(cls):\n"
                  "        b = {'role': 'ROLE_USER'}\n        cls.assertEqual(b.get('role'), 'ROLE_USER')\n"
                  + "".join(f"    def test_{i}(self):\n        self.assertEqual(1 + 1, 2)\n" for i in range(5)))
        problem, = self.problems(source)
        self.assertEqual(problem["kind"], "unbound_class_assertion")
        self.assertEqual(problem["names"], ["test_0", "test_1", "test_2", "test_3", "test_4"])
        self.assertIn("missing a required argument: 'second'", problem["message"])

    def test_import_inheritance_receiver_and_assertion_aliases_resolve(self):
        source = ("from unittest import TestCase as Case\nfrom builtins import classmethod as cm\n"
                  "Parent = Case\nclass Base(Parent):\n    eq = Case.assertEqual\n"
                  "class Smoke(Base):\n    @cm\n    def setup(klass):\n"
                  "        owner = klass\n        equal = owner.eq\n        equal(1, 2)\n")
        problem, = self.problems(source)
        self.assertEqual((problem["class"], problem["assertion"]), ("Smoke", "assertEqual"))

    def test_custom_helpers_instance_calls_explicit_self_and_unknown_scope_are_not_flagged(self):
        sources = [
            "import unittest\nclass Smoke(unittest.TestCase):\n    @classmethod\n    def assertEqual(cls,a,b): pass\n    @classmethod\n    def setUpClass(cls): cls.assertEqual(1,2)\n",
            "import unittest\nclass Smoke(unittest.TestCase):\n    def test_a(self): self.assertEqual(1,2)\n",
            "import unittest\nclass Smoke(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls): cls.assertEqual(unittest.TestCase(),1,2)\n",
            "class Custom:\n    @classmethod\n    def setUpClass(cls): cls.assertEqual(1,2)\n",
            "import unittest\nclass Smoke(Unknown,unittest.TestCase):\n    @classmethod\n    def setUpClass(cls): cls.assertEqual(1,2)\n",
            "import unittest\nclass Smoke(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls):\n        cls=unittest.TestCase()\n        cls.assertEqual(1,2)\n",
            "import unittest\nclass Smoke(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls): cls.assertEqual(*values)\n",
            "import unittest\n@customize\nclass Smoke(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls): cls.assertEqual(1,2)\n",
            "import unittest\nclass Smoke(unittest.TestCase):\n    if True:\n        @classmethod\n        def assertEqual(cls,a,b): pass\n    @classmethod\n    def setUpClass(cls): cls.assertEqual(1,2)\n",
        ]
        for source in sources:
            with self.subTest(source=source):
                self.assertEqual(self.problems(source), [])


if __name__ == "__main__":
    unittest.main()
