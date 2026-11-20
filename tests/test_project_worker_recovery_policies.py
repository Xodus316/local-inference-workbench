"""Portable failure and worker-policy boundaries; no backend is started."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from orchestrator.project_opencode import _AGENT, _PROVIDER, _read_candidate
from orchestrator.project_opencode_whole import OpenCodeProjectWorker, SUPPLEMENTAL_TEST_BYTES
from orchestrator.project_repair import VerificationFailures
from orchestrator.store import digest
from orchestrator.whole_project_selection import INLINE_SELECTION_POLICY, selection_event_guard
from orchestrator.whole_project_smoke import INLINE_SMOKE_POLICY


class StartupFailureTests(unittest.TestCase):
    def test_startup_failure_remains_a_repair_target_without_invented_test_results(self):
        tracker = VerificationFailures(["trusted-api"])
        receipt = {"status": "failed", "source": "current"}
        summary = {"checks": {"trusted-api": {"status": "not_run"},
                              "trusted-api-startup": {"status": "failed"}},
                   "failures": [{"stage": "trusted-api-startup", "diagnostic_excerpt": "Cannot start service"}]}
        result = tracker.observe(receipt, summary, "verify_1", {})
        target = result["repair_target"]
        self.assertEqual(target["blocked_by"], "trusted-api-startup")
        self.assertEqual(target["diagnostics"], ["Cannot start service"])
        self.assertEqual(target["receipt_digest"], digest(receipt))
        self.assertNotIn("id", target)
        self.assertIsNone(tracker.observe({}, {"checks": {"trusted-api": {"status": "not_run"}}}, "verify_2", {}))
        self.assertEqual(next(iter(tracker.open.values()))["status"], "awaiting_recheck")
        self.assertIsNone(tracker.observe({}, {"checks": {"trusted-api": {"status": "passed"}}}, "verify_3", {}))
        self.assertEqual(tracker.open, {})

    def test_skipped_check_without_failed_startup_cannot_authorize_repair(self):
        tracker = VerificationFailures(["trusted-api"])
        for status in ("not_run", "unavailable", "passed"):
            result = tracker.observe({}, {"checks": {"trusted-api": {"status": status}}}, "verify", {})
            self.assertIsNone(result)
        self.assertEqual(tracker.open, {})


class WorktreeRecoveryPolicyTests(unittest.TestCase):
    def test_partial_creation_retains_checked_files_and_rejects_readonly_edits(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "retained.py").write_text("original", encoding="utf-8")
            (root / "first.py").write_text("partial", encoding="utf-8")
            original = {"retained.py": "original"}
            editable = {"first.py", "second.py"}
            with self.assertRaisesRegex(ValueError, "approved file changes"):
                _read_candidate(root, original, editable, True)
            self.assertEqual(_read_candidate(root, original, editable, True, allow_incomplete=True),
                             {"first.py": "partial"})
            (root / "retained.py").write_text("overwritten", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "approved file changes"):
                _read_candidate(root, original, editable, True, allow_incomplete=True)


class InlineWorkerPolicyTests(unittest.TestCase):
    def test_supplemental_capacity_is_explicit_and_cannot_expand_author_capacity(self):
        with patch("orchestrator.project_opencode_whole.OpenCodeWorker.__init__", return_value=None):
            for role in ("implementer", "repairer"):
                worker = OpenCodeProjectWorker("fixture", "fixture", "fixture", role=role,
                    context=98304, thinking="medium", test_bytes=SUPPLEMENTAL_TEST_BYTES)
                self.assertEqual(worker.test_bytes, 49152)
            for role in ("planner", "test_author"):
                with self.assertRaisesRegex(ValueError, "test projection byte limit"):
                    OpenCodeProjectWorker("fixture", "fixture", "fixture", role=role,
                        context=98304, thinking="medium", test_bytes=SUPPLEMENTAL_TEST_BYTES)
            for role in ("planner", "implementer", "repairer", "test_author"):
                worker = OpenCodeProjectWorker("fixture", "fixture", "fixture", role=role,
                    context=98304, thinking="medium", test_bytes=65536, completion_token_ceiling=65536)
                self.assertEqual((worker.test_bytes, worker.completion_token_ceiling), (65536, 65536))
            for capacity in (True, 49153, 65537):
                with self.assertRaisesRegex(ValueError, "test projection byte limit"):
                    OpenCodeProjectWorker("fixture", "fixture", "fixture", role="repairer",
                        context=98304, thinking="medium", test_bytes=capacity)

    def test_smoke_and_selection_configure_real_output_limit_and_deny_tools(self):
        for role, policy in (("implementer", INLINE_SMOKE_POLICY), ("repairer", INLINE_SELECTION_POLICY)):
            with self.subTest(role=role), TemporaryDirectory() as directory:
                worker = object.__new__(OpenCodeProjectWorker)
                worker.role, worker.context, worker.model_alias = role, 98304, "fixture"
                worker.thinking = "medium"
                original = {"agent": {_AGENT: {}}, "provider": {_PROVIDER: {"models": {
                    "fixture": {"options": {}}}}}, "permission": {"read": "allow"}}
                worker._environment = Mock(side_effect=lambda *_: {"OPENCODE_CONFIG_CONTENT": json.dumps(original)})
                env = worker._project_environment(Path(directory), {}, (), "instruction", {"type": "object"},
                                                  tool_policy=policy)
                configured = json.loads((Path(directory) / "workbench-opencode.json").read_text())
                self.assertEqual(configured["permission"], {"*": "deny"})
                self.assertEqual(configured["compaction"], {"auto": False})
                self.assertEqual(configured["agent"][_AGENT]["steps"], 1)
                expected = 65536 if policy == INLINE_SELECTION_POLICY else 32768
                self.assertEqual(configured["provider"][_PROVIDER]["models"]["fixture"]["limit"]["output"], expected)
                self.assertEqual(env["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"], str(expected))
                self.assertEqual(original["permission"], {"read": "allow"})
                worker.completion_token_ceiling = 65536
                env = worker._project_environment(Path(directory), {"output_tokens": 65536}, (), "instruction",
                                                  {"type": "object"}, tool_policy=policy)
                configured = json.loads((Path(directory) / "workbench-opencode.json").read_text())
                self.assertEqual(configured["provider"][_PROVIDER]["models"]["fixture"]["limit"]["output"], 65536)
                self.assertEqual(env["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"], "65536")

    def test_live_guard_forbids_tool_calls_compaction_and_second_step(self):
        for forbidden in ({"type": "tool_use"}, {"type": "compaction"}, {"type": "step_start"}):
            guard = selection_event_guard(None)
            guard({"type": "step_start"})
            with self.assertRaisesRegex(ValueError, "one model step"):
                guard(forbidden)


if __name__ == "__main__":
    unittest.main()
