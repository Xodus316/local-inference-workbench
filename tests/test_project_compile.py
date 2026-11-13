"""Compile complete components before generating the next implementation group."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.config import Config
from orchestrator.project_build import REVIEWED_POLICY
from orchestrator.project_reuse import load_project_reuse, prepare_project_reuse
from orchestrator.runtime import Runtime
from orchestrator.store import encoded
from test_project_build import BRIEF, DIAGNOSIS, TEST, Executor, manifest
from test_project_group_context import ContextModels, group


PRODUCT = "api/Product.java"
APP = "api/App.java"
OTHER = "infra/README.md"
VIEW = "frontend/App.vue"
QWEN_27B = "qwen3.8:27b-mtp-q4_K_M"
DIAGNOSTIC = "api/App.java:3: error: cannot find symbol Product.price()"
POLICY = "commerce-quality-v1"
CHECKPOINTS = [
    {"name": "backend", "source_root": "api", "required_checks": ["gradle-compile"]},
    {"name": "frontend", "source_root": "frontend", "required_checks": ["frontend-typecheck"]},
]
CHECK = {item["name"]: item["required_checks"][0] for item in CHECKPOINTS}


def compile_config():
    return Config(build={"offline_project_enabled": True, "project_environment": {
        "compile_checkpoints": CHECKPOINTS,
        "verification": {"policy": POLICY, "required_checks": ["python-unittest"]},
    }})


class CompileModels(ContextModels):
    def __init__(self, events):
        groups = [
            group("model", [PRODUCT, OTHER]),
            group("api", [APP], depends_on=["model"], context_files=[PRODUCT]),
            group("views", [VIEW], depends_on=["model", "api"], context_files=[PRODUCT]),
        ]
        super().__init__([{"contract": "Views consume the catalog API", "groups": groups}],
                         {PRODUCT: "wrong product signature", OTHER: "unrelated",
                          APP: "correct backend", VIEW: "consumer view"})
        self.events = events
        self.compile_selection = PRODUCT
        self.compile_replacement = PRODUCT
        self.replacement_content = "correct product signature"

    def admit(self, model, settings, payload, stage):
        self.events.append(stage)
        super().admit(model, settings, payload, stage)

    def complete(self, *args):
        if not self.stage.startswith("build_compile_repair_"):
            return super().complete(*args)
        if self.stage.endswith("_select"):
            value = {"diagnosis": DIAGNOSIS, "files": [self.compile_selection]}
        else:
            value = {"diagnosis": DIAGNOSIS, "artifact": {"summary": "Fix product signature", "files": [
                manifest(self.compile_replacement, self.replacement_content),
            ]}}
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}


class CompileExecutor(Executor):
    def __init__(self, events):
        super().__init__()
        self.events = events
        self.compile_inputs = []
        self.receipt_problem = None

    def test(self, run_id, attempt, project, commands, image, cancel, deadline, read_only_project):
        assert read_only_project
        assert image == self.preflight()["image_id"]
        command, = commands
        if not command.startswith("workbench-compile-checkpoint "):
            self.events.append("acceptance")
            result = super().test(run_id, attempt, project, commands, image, cancel, deadline, read_only_project)
            result["commands"][0]["stderr"] += self.marker({
                "policy": POLICY, "status": "passed", "exit_code": 0, "cleanup_confirmed": True,
                "checks": {"python-unittest": {"status": "passed"}},
            })
            return result

        _, name = command.split(" ")
        self.events.append(f"compile {name}")
        sources = {file.path: file.content for file in project.files}
        self.compile_inputs.append((name, sources))
        if self.receipt_problem == "interrupted":
            raise OSError("Synthetic interruption before the compiler returned a receipt")
        status = "passed" if name == "frontend" or sources[PRODUCT] == "correct product signature" else "failed"
        exit_code = 0 if status == "passed" else 1
        result = {
            "checkpoint": name, "policy": POLICY, "status": status, "exit_code": exit_code,
            "cleanup_confirmed": True, "checks": {CHECK[name]: {"status": status}}, "failures": [],
        }
        if status == "failed":
            result["failures"].append({"stage": CHECK[name], "status": "failed", "exit_code": 1,
                "message": "Compile error", "diagnostic_excerpt": DIAGNOSTIC})
        if self.receipt_problem == "environment":
            exit_code = 125
            result.update(status="review_required", exit_code=125)
        if self.receipt_problem == "mismatched checkpoint":
            result["checkpoint"] = "frontend"
        if self.receipt_problem == "mismatched failed check":
            result["failures"][0]["stage"] = "image-setup"
        stderr = "" if self.receipt_problem == "missing marker" else self.marker(result)
        return {
            "status": "passed" if exit_code == 0 else "failed",
            "commands": [{"exit_code": exit_code, "timed_out": self.receipt_problem == "timeout",
                "cancelled": False, "cleanup_confirmed": self.receipt_problem != "unknown cleanup",
                "command_index": 1, "stdout": "", "stderr": stderr}],
            "sandbox": {"image_id": image},
        }

    @staticmethod
    def marker(result):
        return "OFFLINE_PROJECT_RESULT " + json.dumps(result) + "\n"


class CompileCheckpointTests(unittest.TestCase):
    def build(self, *, receipt_problem=None, selection=PRODUCT, replacement=PRODUCT,
              replacement_content="correct product signature"):
        events = []
        models, executor = CompileModels(events), CompileExecutor(events)
        models.compile_selection = selection
        models.compile_replacement = replacement
        models.replacement_content = replacement_content
        executor.receipt_problem = receipt_problem
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(Path(directory), compile_config(), models, executor)
            try:
                row = runtime.prepare({"mode": "build", "prompt": BRIEF, "build_profile": REVIEWED_POLICY})
                self.assertEqual(events, [])
                runtime.approve(row["id"], row["digest"])
                runtime.queue.join()
                row = runtime.store.get(row["id"])
                if receipt_problem == "interrupted":
                    intent = runtime.store.artifact(row["id"], "build_compile_checkpoint_1_0", "request")
                    receipt = runtime.store.artifact(row["id"], "build_compile_checkpoint_1_0", "raw")
                    self.assertTrue(intent.exists())
                    self.assertFalse(receipt.exists())
                    runtime.store.update(row["id"], "interrupted")
                    runtime.approve(row["id"], row["digest"], resume=True)
                    runtime.queue.join()
                    row = runtime.store.get(row["id"])
            finally:
                runtime.close()
        return row, models, executor, events

    def test_repair_before_next_group_and_validate_corrected_source(self):
        row, models, executor, events = self.build()
        self.assertEqual(row["status"], "completed", row.get("result"))
        self.assertEqual(events[events.index("implement_group_1"):events.index("acceptance") + 1], [
            "implement_group_1", "implement_group_2", "compile backend",
            "build_compile_repair_1_1_select", "build_compile_repair_1_1", "compile backend",
            "implement_group_3", "compile frontend", "acceptance",
        ])
        self.assertEqual(executor.compile_inputs, [
            ("backend", {PRODUCT: "wrong product signature", APP: "correct backend"}),
            ("backend", {PRODUCT: "correct product signature", APP: "correct backend"}),
            ("frontend", {VIEW: "consumer view"}),
        ])
        self.assertEqual(executor.tests, [[TEST]])
        for stage, model, settings, payload in models.seen:
            if stage.startswith("build_compile_repair_"):
                self.assertEqual(model, QWEN_27B)
                self.assertEqual(settings["context"], 98304)
                self.assertEqual(settings["think"], "xhigh")
                self.assertIn(DIAGNOSTIC, payload["messages"][1]["content"])
            if stage == "implement_group_3":
                data = json.loads(payload["messages"][1]["content"])
                self.assertEqual(data["selected_source"], [manifest(PRODUCT, "correct product signature")])
        output = row["result"]["output"]
        self.assertEqual(output["compile_repair_attempts"], 1)
        self.assertEqual(output["repair_attempts"], 0)
        checks = output["verification"]["compile_checkpoints"]
        self.assertEqual([(item["checkpoint"], item["group_index"], item["attempt"], item["status"], item["stage"])
                          for item in checks], [
            ("backend", 2, 0, "failed", "build_compile_checkpoint_1_0"),
            ("backend", 2, 1, "passed", "build_compile_checkpoint_1_1"),
            ("frontend", 3, 0, "passed", "build_compile_checkpoint_2_0"),
        ])
        files = {item["path"]: item["content"] for item in output["artifact"]["files"]}
        self.assertEqual(files[PRODUCT], "correct product signature")
        self.assertEqual(files[OTHER], "unrelated")
        self.assertEqual(files[TEST["path"]], TEST["content"])

    def test_untrusted_or_inconclusive_compile_receipt_blocks_repair_and_generation(self):
        for problem in ("environment", "mismatched checkpoint", "mismatched failed check",
                        "missing marker", "timeout", "unknown cleanup"):
            with self.subTest(problem=problem):
                row, _, executor, events = self.build(receipt_problem=problem)
                self.assertIn(row["status"], {"failed", "review_required"}, row.get("result"))
                self.assertFalse(any(stage.startswith("build_compile_repair_") for stage in events), events)
                self.assertNotIn("implement_group_3", events)
                self.assertEqual(len(executor.compile_inputs), 1)
                self.assertEqual(executor.tests, [])

    def test_compile_repair_cannot_select_or_replace_paths_outside_component(self):
        for selection, replacement in ((OTHER, PRODUCT), (PRODUCT, OTHER), (PRODUCT, TEST["path"])):
            with self.subTest(selection=selection, replacement=replacement):
                row, _, executor, events = self.build(selection=selection, replacement=replacement)
                self.assertEqual(row["status"], "failed", row.get("result"))
                self.assertIn("build_compile_repair_1_1_select", events)
                if selection != PRODUCT:
                    self.assertNotIn("build_compile_repair_1_1", events)
                self.assertNotIn("implement_group_3", events)
                self.assertEqual(len(executor.compile_inputs), 1)
                self.assertEqual(executor.tests, [])

    def test_compile_repair_exhaustion_stops_before_dependent_group(self):
        row, _, executor, events = self.build(replacement_content="still broken")
        self.assertIn(row["status"], {"failed", "review_required"}, row.get("result"))
        self.assertEqual(events.count("compile backend"), 3)
        self.assertNotIn("implement_group_3", events)
        self.assertEqual(executor.tests, [])
        self.assertEqual(row["result"]["output"]["compile_repair_attempts"], 2)

    def test_unknown_compile_completion_is_not_replayed_after_resume(self):
        row, _, executor, events = self.build(receipt_problem="interrupted")
        self.assertEqual(row["status"], "failed", row.get("result"))
        self.assertEqual(events.count("compile backend"), 1)
        self.assertNotIn("build_compile_repair_1_1_select", events)
        self.assertNotIn("implement_group_3", events)
        self.assertEqual(executor.tests, [])
        self.assertIn("unresolved", row["result"]["output"]["error"]["message"])

    def test_fresh_approval_reuses_last_group_at_unexecuted_compile_boundary(self):
        events = []
        models, executor = CompileModels(events), CompileExecutor(events)
        executor.receipt_problem = "interrupted"
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        runtime = Runtime(Path(temporary.name), compile_config(), models, executor)
        self.addCleanup(runtime.close)
        pending = runtime.prepare({"mode": "build", "prompt": BRIEF, "build_profile": REVIEWED_POLICY})
        runtime.approve(pending["id"], pending["digest"])
        runtime.queue.join()
        source = runtime.store.get(pending["id"])
        self.assertEqual(source["status"], "failed")

        def read(stage, suffix):
            return runtime.store.artifact(source["id"], stage, suffix).read_bytes()

        def reuse(reader=read, row=source):
            return prepare_project_reuse(row, reader, source["bundle"], source_sdk_revision="a" * 40,
                                         current_sdk_revision="b" * 40, through="implementation", completed_groups=2)

        stage = "build_compile_checkpoint_1_0"
        request = read(stage, "request")
        manifest = reuse()
        self.assertEqual(manifest["source"]["pre_compile_checkpoint"],
                         {"stage": stage, "request_sha256": hashlib.sha256(request).hexdigest()})
        self.assertEqual(len(manifest["checkpoint"]["groups"]), 2)
        self.assertFalse(runtime.store.artifact(source["id"], "project_after_2", "raw").exists())
        source_artifacts = runtime.store.directory / "artifacts" / source["id"]
        original_bytes = {path.name: path.read_bytes() for path in source_artifacts.iterdir()}

        for changed_stage, suffix, raw in (
            (stage, "request", encoded({**json.loads(request), "group_index": 1}).encode()),
            (stage, "raw", b"{}"),
            ("build_compile_checkpoint_1_1", "request", request),
            ("build_compile_checkpoint_2_0", "request", request),
        ):
            def changed(key, kind):
                return raw if (key, kind) == (changed_stage, suffix) else read(key, kind)
            with self.subTest(stage=changed_stage, suffix=suffix), self.assertRaises(ValueError):
                reuse(changed)
        with self.assertRaises(ValueError):
            reuse(row={**source, "status": "completed"})
        with self.assertRaises(ValueError):
            reuse(row={**source, "calls": list(reversed(source["calls"]))})
        tampered = copy.deepcopy(manifest)
        tampered["source"]["pre_compile_checkpoint"]["request_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "request provenance changed"):
            load_project_reuse({**source["bundle"], "project_reuse": tampered})

        executor.receipt_problem = None
        events.clear()
        fresh_id = runtime.store.create({**source["bundle"], "project_reuse": manifest})
        fresh = runtime.store.get(fresh_id)
        self.assertNotEqual(fresh_id, source["id"])
        self.assertFalse(fresh["approved"])
        self.assertEqual(events, [])
        runtime.approve(fresh_id, fresh["digest"])
        runtime.queue.join()
        fresh = runtime.store.get(fresh_id)
        self.assertEqual(fresh["status"], "completed", fresh.get("result"))
        self.assertEqual(events[:5], ["compile backend", "build_compile_repair_1_1_select",
                                     "build_compile_repair_1_1", "compile backend", "implement_group_3"])
        self.assertEqual(runtime.store.get(source["id"]), source)
        self.assertEqual({path.name: path.read_bytes() for path in source_artifacts.iterdir()}, original_bytes)


if __name__ == "__main__":
    unittest.main()
