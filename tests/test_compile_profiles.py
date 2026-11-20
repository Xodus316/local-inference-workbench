"""Checkpoint tooling is frozen and must return settled source-bound evidence."""
from copy import deepcopy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from orchestrator.project_build import REVIEWED_POLICY, compile_checkpoint_status, sandbox_stage_results
from orchestrator.project_environment import ProjectEnvironment
from orchestrator.runtime import Runtime
from orchestrator.sandbox_profiles import normalize_verification_profile, verification_profile_identity
from orchestrator.whole_project_selection import compiler_diagnostic_selection
from test_project_build import BRIEF, DIAGNOSIS, manifest
from test_project_compile import (CHECKPOINTS, POLICY, VIEW, CompileExecutor, CompileModels, compile_config)


def profile(tool):
    return normalize_verification_profile({
        "name": "frontend-parser", "phase": "candidate", "entrypoint": "/usr/bin/tini",
        "readonly_files": [{"source": str(tool.resolve()), "target": "/opt/evaluation/parser.py",
                            "sha256": hashlib.sha256(tool.read_bytes()).hexdigest()}],
        "commands": ["python3 -I /opt/evaluation/parser.py"],
        "verification": {"policy": POLICY, "required_checks": ["frontend-typecheck", "frontend-parse"]}})


class ParserModels(CompileModels):
    def complete(self, *args):
        if self.stage.startswith("build_compile_repair_2_"):
            value = ({"diagnosis": DIAGNOSIS, "files": [VIEW]} if self.stage.endswith("_select") else
                     {"diagnosis": DIAGNOSIS, "artifact": {"summary": "Close tag", "files": [manifest(VIEW, "valid vue")]}})
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}
        return super().complete(*args)


class ParserExecutor(CompileExecutor):
    def __init__(self, events, problem):
        super().__init__(events)
        self.problem, self.profiles = problem, []

    def test(self, *args, verification_profile=None, **kwargs):
        receipt = super().test(*args, **kwargs)
        if verification_profile is None:
            return receipt
        self.profiles.append(verification_profile)
        image = kwargs["image"]
        source = {f.path: f.content for f in args[2].files}
        row = receipt["commands"][0]
        summary = sandbox_stage_results(row)
        parsed = source[VIEW] == "valid vue"
        summary["checks"]["frontend-parse"] = {"status": "passed" if parsed else "failed"}
        if not parsed:
            summary.update(status="failed", exit_code=1)
            summary["failures"] = [{"stage": "frontend-parse", "status": "failed", "exit_code": 1,
                                    "diagnostic_excerpt": f"{VIEW}:2:1: Element is missing end tag."}]
            row["exit_code"] = 1
            receipt["status"] = "failed"
        row["stderr"] = self.marker(summary)
        effective = verification_profile_identity(verification_profile, image)
        receipt["sandbox"].update(network="disabled", read_only_root=True, read_only_project=True,
                                  effective_runtime=effective)
        row.update(source_unchanged=True, source_sha256={p: hashlib.sha256(t.encode()).hexdigest() for p, t in source.items()},
                   profile_sources_unchanged=True, effective_runtime=effective)
        if self.problem == "source":
            row["source_sha256"][VIEW] = "0" * 64
        elif self.problem == "tool":
            row["profile_sources_unchanged"] = False
        elif self.problem == "identity":
            receipt["sandbox"].pop("effective_runtime")
        elif self.problem == "cleanup":
            row["cleanup_confirmed"] = False
        return receipt


class CompileProfileTests(unittest.TestCase):
    def build(self, directory, problem=None):
        tool = directory / "parser.py"
        tool.write_text("# trusted tooling fixture\n")
        selected = profile(tool)
        config = compile_config()
        environment = config.build.project_environment.model_dump()
        environment["compile_checkpoints"][1].update(
            required_checks=selected["verification"]["required_checks"], verification_profile=selected)
        config.build.project_environment = ProjectEnvironment.model_validate(environment)
        events = []
        models, executor = ParserModels(events), ParserExecutor(events, problem)
        with closing(Runtime(directory / "state", config, models, executor)) as runtime:
            row = runtime.prepare({"mode": "build", "prompt": BRIEF, "build_profile": REVIEWED_POLICY})
            runtime.approve(row["id"], row["digest"])
            runtime.queue.join()
            result = runtime.store.get(row["id"])
        return result, executor, events

    def test_frozen_helper_is_dispatched_and_failed_parse_is_repaired_before_advancement(self):
        with tempfile.TemporaryDirectory() as directory:
            row, executor, events = self.build(Path(directory))
        self.assertEqual(row["status"], "completed", row.get("result"))
        self.assertEqual(len(executor.profiles), 2)
        self.assertEqual(events.count("compile frontend"), 2)
        self.assertIn("build_compile_repair_2_1", events)
        self.assertLess(events.index("build_compile_repair_2_1"), events.index("acceptance"))

    def test_unknown_cleanup_drift_or_missing_profile_identity_cannot_dispatch_repair(self):
        for problem in ("source", "tool", "identity", "cleanup"):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as directory:
                row, executor, events = self.build(Path(directory), problem)
                self.assertEqual(row["status"], "review_required", row.get("result"))
                self.assertEqual(len(executor.profiles), 1)
                self.assertNotIn("build_compile_repair_2_1", events)
                self.assertNotIn("acceptance", events)

    def test_compile_profiles_cannot_change_required_checks_marker_policy_or_tool_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            tool = Path(directory) / "parser.py"
            tool.write_text("trusted")
            selected = profile(tool)
            value = {"verification": {"policy": POLICY, "required_checks": ["python-unittest"]},
                     "compile_checkpoints": [{**CHECKPOINTS[1], "required_checks": selected["verification"]["required_checks"],
                                               "verification_profile": selected}]}
            self.assertEqual(ProjectEnvironment.model_validate(value).model_dump()["compile_checkpoints"][0]["verification_profile"], selected)
            for key, item in (("marker", "OTHER_RESULT"), ("policy", "other-policy"), ("required_checks", ["frontend-build"])):
                changed = deepcopy(value)
                changed["compile_checkpoints"][0]["verification_profile"]["verification"][key] = item
                with self.subTest(key=key), self.assertRaises(ValueError):
                    ProjectEnvironment.model_validate(changed)
            tool.write_text("drift")
            with self.assertRaises(ValueError):
                ProjectEnvironment.model_validate(value)

    def test_vue_locations_join_existing_typescript_scope_without_inventing_files(self):
        from types import SimpleNamespace
        from test_whole_project_selection import compile_feedback
        feedback = compile_feedback("frontend/Dashboard.vue:96:11: Element is missing end tag.", "frontend-parse")
        other = compile_feedback("src/List.vue(4,8): error TS2322: mismatch", "frontend-typecheck")
        feedback["failed_execution"]["commands"].extend(other["failed_execution"]["commands"])
        files = [SimpleNamespace(path=p, content="source") for p in ("frontend/Dashboard.vue", "frontend/src/List.vue")]
        selected = compiler_diagnostic_selection(files, feedback, byte_limit=65536)
        self.assertEqual(selected["paths"], [f.path for f in files])
        self.assertEqual({m["compiler_stage"] for m in selected["matches"]}, {"frontend-parse", "frontend-typecheck"})

    def test_node_stack_frames_do_not_discard_current_compiler_locations(self):
        from types import SimpleNamespace
        from test_whole_project_selection import compile_feedback
        diagnostic = ("frontend/Dashboard.vue:96:11: Element is missing end tag.\n"
                      "    at compile (/opt/frontend/node_modules/compiler.js:12:3)\n"
                      "    at moduleResolve (node:internal/modules/esm/resolve:946:18)\n")
        files = [SimpleNamespace(path="frontend/Dashboard.vue", content="source")]
        selected = compiler_diagnostic_selection(files, compile_feedback(diagnostic, "frontend-parse"), byte_limit=65536)
        self.assertEqual(selected["paths"], [files[0].path])
        self.assertEqual(len(selected["matches"]), 1)


if __name__ == "__main__":
    unittest.main()
