"""Exercise focused reviewed repairs across skipped verification checks."""
import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.config import Config
from orchestrator.project_build import EXACT_REPAIR_FORMAT, REVIEWED_POLICY
from orchestrator.project_repair import VerificationFailures
from orchestrator.runtime import Runtime
from orchestrator.store import digest
from test_project_build import BRIEF, DIAGNOSIS, TEST, Executor, Models, manifest


POLICY = "fixture-quality-v1"
COMPILE = "gradle-build"
CHECKPOINT = "gradle-compile"
TRUSTED = "trusted-api"
CLICK = {"id": "concurrent-clicks", "summary": "Concurrent clicks lose updates", "status": "failed",
         "diagnostics": ["Expected 24 stored clicks, found 1"],
         "source_paths": ["api/App.java", TEST["path"]], "repair_guidance": "Use one atomic write.",
         "control_match": True}
MALFORMED = {"id": "malformed-request", "summary": "Malformed authenticated request has the wrong status",
             "status": "failed", "diagnostics": ["Authenticated malformed request expected 400, received 401"]}
COMPILER_ERROR = "api/App.java:1: error: incompatible return type"
UNRELATED_FINDING = {
    "severity": "P1", "category": "correctness", "path": "frontend/App.vue", "line": 1,
    "evidence": "frontend source", "title": "Unrelated frontend issue",
    "description": "The view loses its selection after a refresh.", "fix": "Preserve the selection.",
}


class SequenceExecutor(Executor):
    def __init__(self, *, unclean_attempt=None):
        super().__init__()
        self.unclean_attempt = unclean_attempt
        self.receipts = []
        self.events = []

    def test(self, run_id, attempt, project, commands, image, cancel, deadline, read_only_project):
        assert image == self.preflight()["image_id"]
        files = {item.path: item.content for item in project.files}
        backend = files["api/App.java"]
        if commands == ["workbench-compile-checkpoint backend"]:
            self.events.append(f"compile:{backend}")
            assert read_only_project and set(files) == {"api/App.java"}
            passed = backend != "uncompilable click repair"
            status, exit_code = ("passed", 0) if passed else ("failed", 1)
            summary = {"checkpoint": "backend", "policy": POLICY, "status": status,
                       "exit_code": exit_code, "cleanup_confirmed": True,
                       "checks": {CHECKPOINT: {"status": status}}, "failures": []}
            if not passed:
                summary["failures"].append({"stage": CHECKPOINT, "status": "failed", "exit_code": 1,
                                            "diagnostic_excerpt": COMPILER_ERROR})
            return {"status": status, "commands": [{
                "exit_code": exit_code, "timed_out": False, "cancelled": False, "cleanup_confirmed": True,
                "command_index": 1, "stdout": "", "stderr": "OFFLINE_PROJECT_RESULT " + json.dumps(summary) + "\n",
            }], "sandbox": {"image_id": image}}

        self.events.append(f"verify:{backend}")
        receipt = super().test(run_id, attempt, project, commands, image, cancel, deadline, read_only_project)
        compile_status, api_status, cases = {
            "broken backend": ("passed", "failed", [CLICK, MALFORMED]),
            "uncompilable click repair": ("failed", "not_run", []),
            "compiled click repair": ("passed", "failed", [MALFORMED]),
            "correct backend": ("passed", "passed", []),
        }[backend]
        status = "passed" if compile_status == api_status == "passed" else "failed"
        exit_code = 0 if status == "passed" else 1
        failed_check = COMPILE if compile_status == "failed" else TRUSTED
        diagnostics = COMPILER_ERROR if compile_status == "failed" else "\n".join(
            detail for case in cases for detail in case["diagnostics"])
        failures = [] if status == "passed" else [{
            "stage": failed_check, "status": "failed", "exit_code": 1,
            "message": "Offline check exited 1", "diagnostic_excerpt": diagnostics,
        }]
        summary = {"policy": POLICY, "status": status, "exit_code": exit_code, "cleanup_confirmed": True,
                   "checks": {COMPILE: {"status": compile_status}, TRUSTED: {"status": api_status}},
                   "failures": failures, "fixture_cases": cases}
        unittest_log = "test_api (test_api.API.test_api) ... ok\n\nRan 1 test in 0.01s\n\nOK\n"
        receipt["status"] = status
        receipt["sandbox"] = {"image_id": image}
        receipt["commands"][0].update(
            exit_code=exit_code,
            cleanup_confirmed=attempt != self.unclean_attempt,
            stderr=(unittest_log if compile_status == "passed" else "")
            + "OFFLINE_PROJECT_RESULT " + json.dumps(summary) + "\n",
        )
        self.receipts.append(receipt)
        return receipt


class SequenceModels(Models):
    def __init__(self, executor, *, compile_gates=False, escape_scope=False, stale_anchor=False):
        super().__init__()
        self.executor = executor
        self.compile_gates = compile_gates
        self.escape_scope = escape_scope
        self.stale_anchor = stale_anchor

    def admit(self, model, settings, payload, stage):
        self.executor.events.append(stage)
        super().admit(model, settings, payload, stage)

    def complete(self, *args):
        repair_outputs = {
            "repair_1": "uncompilable click repair",
            "repair_2": "correct backend" if self.compile_gates else "compiled click repair",
            "repair_3": "correct backend",
            "build_repair_compile_repair_1_1_1": "compiled click repair",
        }
        if self.stage.startswith("build_code_review_"):
            findings = [] if self.executor.receipts[-1]["status"] == "passed" else [UNRELATED_FINDING]
            result = {"findings": findings}
        elif (repair_stage := self.stage.removesuffix("_edit_retry")) in repair_outputs:
            path = "frontend/App.vue" if self.escape_scope and repair_stage == "repair_1" else "api/App.java"
            request = json.loads(self.seen[-1][3]["messages"][1]["content"])
            if request.get("patch_format") == EXACT_REPAIR_FORMAT:
                original = request["selected_source"][0]["content"]
                before = "stale anchor" if self.stale_anchor and self.stage == "repair_1" else original
                result = {"summary": "Scoped repair", "edits": [
                    {"path": path, "before": before, "after": repair_outputs[repair_stage]},
                ]}
            else:
                result = {"diagnosis": DIAGNOSIS, "artifact": {"summary": "Scoped repair", "files": [
                    manifest(path, repair_outputs[repair_stage]),
                ]}}
        else:
            return super().complete(*args)
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]}


class RepairRuntime(Runtime):
    @staticmethod
    def verification_repair_cases(summary):
        return {TRUSTED: summary["fixture_cases"]} if summary["fixture_cases"] else {}


class ControlRepairRuntime(RepairRuntime):
    @staticmethod
    def verification_repair_cases(summary):
        return {TRUSTED: [{**case, "source_paths": ["api/App.java", "frontend/App.vue"]}
                          for case in summary["fixture_cases"]]}

    @staticmethod
    def verification_repair_scope(target, files):
        return ["api/App.java"] if target["id"] == CLICK["id"] else None


class FocusedRepairTests(unittest.TestCase):
    def test_changed_failure_keeps_prior_observation_with_its_receipt(self):
        failures = VerificationFailures([TRUSTED])
        summary = {"checks": {TRUSTED: {"status": "failed"}}}
        before = {"project": "initial"}
        after = {"project": "patched"}
        regressed = {**CLICK, "diagnostics": ["One of the 24 requests did not succeed"]}
        initial = failures.observe(before, summary, "build_verify_0", {TRUSTED: [CLICK, MALFORMED]})
        self.assertNotIn("previous_target_observation", initial)

        next_focus = failures.observe(after, summary, "build_verify_1", {TRUSTED: [regressed, MALFORMED]})
        self.assertEqual(next_focus["repair_target"]["diagnostics"], regressed["diagnostics"])
        self.assertEqual(next_focus["repair_target"]["receipt_digest"], digest(after))
        self.assertEqual(next_focus["previous_target_observation"], {
            "diagnostics": CLICK["diagnostics"],
            "receipt_digest": digest(before),
            "verification_stage": "build_verify_0",
        })
        self.assertEqual([case["id"] for case in next_focus["pending_verification_failures"]], [MALFORMED["id"]])

    def execute(self, *, unclean_attempt=None, compile_gates=False, use_control=False, escape_scope=False,
                stale_anchor=False):
        executor = SequenceExecutor(unclean_attempt=unclean_attempt)
        models = SequenceModels(executor, compile_gates=compile_gates, escape_scope=escape_scope,
                                stale_anchor=stale_anchor)
        environment = {
            "verification": {"policy": POLICY, "required_checks": [COMPILE, TRUSTED]},
        }
        if compile_gates:
            environment["compile_checkpoints"] = [
                {"name": "backend", "source_root": "api", "required_checks": [CHECKPOINT]},
            ]
        config = Config(build={"offline_project_enabled": True, "project_environment": environment})
        with tempfile.TemporaryDirectory() as directory:
            runtime_type = ControlRepairRuntime if use_control else RepairRuntime
            runtime = runtime_type(Path(directory), config, models, executor)
            try:
                row = runtime.prepare({"mode": "build", "prompt": BRIEF, "build_profile": REVIEWED_POLICY})
                self.assertEqual(models.seen, [])
                runtime.approve(row["id"], row["digest"])
                runtime.queue.join()
                row = runtime.store.get(row["id"])
                if use_control:
                    proof_path = runtime.store.artifact(row["id"], "repair_1_select", "raw")
                    proof = json.loads(proof_path.read_text(encoding="utf-8"))
                    self.assertEqual(proof["selection"]["files"], ["api/App.java"])
                    if row["status"] == "completed":
                        self.assertEqual(proof, row["result"]["stages"]["repair_1"]["selection_proof"])
            finally:
                runtime.close()
        calls = {stage: json.loads(payload["messages"][1]["content"])
                 for stage, _, _, payload in models.seen}
        return row, calls, executor

    def test_focus_restores_after_compile_failure_and_requires_fresh_acceptance(self):
        row, calls, executor = self.execute()
        self.assertEqual(row["status"], "completed", row.get("result"))
        output = row["result"]["output"]
        self.assertEqual(output["repair_attempts"], 3)
        self.assertEqual(executor.tests, [[TEST]] * 4)
        self.assertEqual([item["status"] for item in output["verification"]["baselines"]],
                         ["failed", "failed", "failed", "passed"])
        final_review = output["verification"]["code_reviews"][-1]
        self.assertEqual(final_review["status"], "passed")
        self.assertEqual(final_review["project_digest"], digest(output["artifact"]))
        self.assertEqual({f["path"]: f["content"] for f in output["artifact"]["files"]}, {
            "api/App.java": "correct backend", "frontend/App.vue": "frontend source", TEST["path"]: TEST["content"],
        })

        targets = [(TRUSTED, CLICK["id"], CLICK["diagnostics"][0]),
                   (COMPILE, None, COMPILER_ERROR),
                   (TRUSTED, MALFORMED["id"], MALFORMED["diagnostics"][0])]
        for attempt, (check, case, diagnostic) in enumerate(targets):
            for stage in (f"repair_{attempt + 1}_select", f"repair_{attempt + 1}"):
                with self.subTest(stage=stage):
                    request = calls[stage]
                    focus = request["repair_target"]
                    self.assertEqual(focus["check"], check)
                    self.assertEqual(focus.get("id"), case)
                    self.assertIn(diagnostic, json.dumps(focus["diagnostics"]))
                    self.assertEqual(focus["verification_stage"], f"build_verify_{attempt}")
                    self.assertEqual(focus["receipt_digest"], digest(executor.receipts[attempt]))
                    self.assertEqual(request["failed_execution"]["full_failure_receipt_digest"],
                                     digest(executor.receipts[attempt]))
                    self.assertNotIn("independent_code_review", request)
                    self.assertNotIn(UNRELATED_FINDING["title"], json.dumps(request))

        for stage in ("repair_2_select", "repair_2"):
            retained = {item["id"]: item for item in calls[stage]["pending_verification_failures"]
                        if item["check"] == TRUSTED}
            self.assertEqual(set(retained), {CLICK["id"], MALFORMED["id"]})
            for case in retained.values():
                self.assertEqual(case["status"], "awaiting_recheck")
                self.assertEqual(case["verification_stage"], "build_verify_0")
                self.assertEqual(case["receipt_digest"], digest(executor.receipts[0]))
                self.assertNotIn("repair_guidance", case)
                self.assertNotIn("source_paths", case)
        self.assertEqual(calls["repair_1_select"]["diagnostic_source"], [manifest("api/App.java", "broken backend")])
        self.assertEqual(calls["repair_1"]["repair_target"]["repair_guidance"], CLICK["repair_guidance"])
        self.assertNotIn("diagnostic_source", calls["repair_2_select"])

    def test_unclean_verification_cannot_spend_another_repair_on_old_evidence(self):
        row, calls, executor = self.execute(unclean_attempt=1)
        self.assertEqual(row["status"], "review_required", row.get("result"))
        output = row["result"]["output"]
        self.assertEqual(output["repair_attempts"], 1)
        self.assertIsNone(output["artifact"])
        self.assertEqual(len(executor.receipts), 2)
        self.assertNotIn("repair_2_select", calls)

    def test_post_patch_compile_fix_keeps_behavior_target_and_separate_budget(self):
        row, calls, executor = self.execute(compile_gates=True)
        self.assertEqual(row["status"], "completed", row.get("result"))
        output = row["result"]["output"]
        self.assertEqual(output["repair_attempts"], 2)
        self.assertEqual(output["compile_repair_attempts"], 1)
        self.assertEqual(executor.tests, [[TEST]] * 3)
        events = executor.events
        first_patch = events.index("repair_1")
        next_verification = events.index("verify:compiled click repair")
        self.assertEqual(events[first_patch:next_verification], [
            "repair_1", "compile:uncompilable click repair", "build_repair_compile_repair_1_1_1_select",
            "build_repair_compile_repair_1_1_1", "compile:compiled click repair",
        ])
        self.assertEqual(calls["repair_2"]["repair_target"]["id"], MALFORMED["id"])
        self.assertNotIn("repair_3_select", calls)
        for stage in ("build_repair_compile_repair_1_1_1_select", "build_repair_compile_repair_1_1_1"):
            intent = calls[stage]["repair_intent"]
            self.assertEqual(intent["repair_target"], calls["repair_1"]["repair_target"])
            self.assertEqual(intent["pending_verification_failures"], calls["repair_1"]["pending_verification_failures"])
            self.assertEqual(intent["changed_paths"], ["api/App.java"])
            self.assertIn(COMPILER_ERROR, calls[stage]["failed_execution"]["commands"][0]["stderr"])

    def test_verified_control_pins_editable_scope_through_compiler_retry(self):
        row, calls, executor = self.execute(compile_gates=True, use_control=True)
        self.assertEqual(row["status"], "completed", row.get("result"))
        self.assertNotIn("repair_1_select", calls)
        self.assertIn("repair_2_select", calls)
        request = calls["repair_1"]
        self.assertEqual(row["bundle"]["project_repair_format"], EXACT_REPAIR_FORMAT)
        self.assertEqual(request["patch_format"], EXACT_REPAIR_FORMAT)
        self.assertEqual(request["selected_source"], [manifest("api/App.java", "broken backend")])
        self.assertEqual(request["diagnostic_source"], [manifest("frontend/App.vue", "frontend source")])
        proof = row["result"]["stages"]["repair_1"]["selection_proof"]
        self.assertEqual(proof["source"], "trusted_verifier")
        self.assertEqual(proof["target_digest"], digest(request["repair_target"]))
        self.assertEqual(proof["selection"], {"diagnosis": request["diagnosis"], "files": ["api/App.java"]})
        compiler = calls["build_repair_compile_repair_1_1_1_select"]
        self.assertEqual([item["path"] for item in compiler["implementation_tree"]], ["api/App.java"])
        self.assertEqual(compiler["repair_intent"]["repair_target"], request["repair_target"])
        self.assertEqual(calls["build_repair_compile_repair_1_1_1"]["patch_format"], EXACT_REPAIR_FORMAT)
        self.assertIn("compile:compiled click repair", executor.events)

    def test_verified_control_rejects_edit_to_read_only_source(self):
        row, calls, executor = self.execute(compile_gates=True, use_control=True, escape_scope=True)
        self.assertEqual(row["status"], "failed", row.get("result"))
        self.assertEqual(row["result"]["output"]["error"], {
            "stage": "repair_1_edit_retry", "message": "Text repair returned a path outside its selection",
        })
        self.assertNotIn("repair_1_select", calls)
        self.assertEqual(len(executor.receipts), 1)

    def test_invalid_anchor_gets_one_correction_on_unchanged_source(self):
        row, calls, executor = self.execute(compile_gates=True, use_control=True, stale_anchor=True)
        self.assertEqual(row["status"], "completed", row.get("result"))
        retry = calls["repair_1_edit_retry"]
        self.assertEqual(retry, {**calls["repair_1"], "repair_format_error": "Text repair anchor is missing in api/App.java"})
        stage = row["result"]["stages"]["repair_1"]
        self.assertEqual(stage["patch_stage"], "repair_1_edit_retry")
        self.assertEqual(stage["patch_validation_error"], retry["repair_format_error"])
        first = executor.events.index("repair_1")
        self.assertEqual(executor.events[first:first + 3], ["repair_1", "repair_1_edit_retry",
                                                            "compile:uncompilable click repair"])
