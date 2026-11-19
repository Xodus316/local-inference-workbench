"""Repair selection receives current evidence once, without exploration tools."""

import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from orchestrator.project_build import (WHOLE_PROJECT_REPAIR_SELECTED_BYTES, ProjectArtifact, failure_feedback,
                                        scoped_repair_selection)
from orchestrator.store import STAGES
from orchestrator.whole_project import WholeProjectBuildRun
from orchestrator.whole_project_selection import (COMPILER_SELECTION_POLICY, INLINE_SELECTION_POLICY,
                                                 SELECTION_OUTPUT_TOKENS, compiler_diagnostic_selection,
                                                 gradle_kotlin_dsl_findings, projection_details, selection_event_guard,
                                                 selection_source)


def file(path, content):
    return SimpleNamespace(path=path, content=content)


def compile_feedback(diagnostic, check="gradle-compile"):
    summary = {"checkpoint": "backend", "status": "failed", "exit_code": 1, "cleanup_confirmed": True,
               "checks": {check: {"status": "failed"}}, "failures": [{"stage": check, "status": "failed",
                   "exit_code": 1, "message": "Compilation failed", "diagnostic_excerpt": diagnostic}]}
    receipt = {"commands": [{"exit_code": 1, "stderr": "OFFLINE_PROJECT_RESULT " + json.dumps(summary)}]}
    return {"compile_checkpoint": "backend", "failed_execution": failure_feedback(receipt, include_stage_results=True)}


class CompilerSelectionTests(unittest.TestCase):
    def test_exact_gradle_receipt_selects_only_current_build_file_without_inference(self):
        artifact = ProjectArtifact(summary="Current source", files=[
            {"path": "api/build.gradle.kts", "content": "bootJar { enabled = true }"},
            {"path": "api/settings.gradle.kts", "content": 'rootProject.name = "fixture"'},
            {"path": "frontend/main.ts", "content": "frontend"}])
        feedback = compile_feedback(
            "e: file:///tmp/project/api/build.gradle.kts:29:1: Expression 'bootJar' cannot be invoked as a function.\n"
            "e: file:///tmp/project/api/build.gradle.kts:29:1: Unresolved reference.")
        flow = object.__new__(WholeProjectBuildRun)
        flow.contract = {"repair_selected_bytes": 32768}
        flow.runtime = SimpleNamespace(store=Mock(), whole_project_event=Mock())
        flow.state = {"run_id": "run"}
        flow.stages = {}
        flow._generate = Mock(side_effect=AssertionError("Compiler path selection must not invoke a model"))
        paths, evidence = flow._select_repair_paths("build_compile_repair_1_1", artifact, feedback,
                                                   {"api/build.gradle.kts", "api/settings.gradle.kts"})
        self.assertEqual(paths, ["api/build.gradle.kts"])
        flow._generate.assert_not_called()
        flow.runtime.whole_project_event.assert_not_called()
        args = flow.runtime.store.save_artifact.call_args.args
        self.assertEqual(args[:3], ("run", "build_compile_repair_1_1_select", "raw"))
        proof = args[3]
        self.assertFalse(proof["model_call"])
        self.assertEqual(proof["policy"], COMPILER_SELECTION_POLICY)
        self.assertEqual(proof["selection"]["files"], paths)
        self.assertEqual(proof["matches"], [{"compiler_stage": "gradle-compile",
            "reported_path": "file:///tmp/project/api/build.gradle.kts", "path": paths[0], "line": 29, "column": 1}])
        self.assertFalse(flow.stages[args[1]]["model_call"])
        self.assertEqual(evidence["compiler_path_selection"]["evidence_digest"], flow.stages[args[1]]["evidence_digest"])

    def test_unmatched_ambiguous_and_malicious_paths_never_expand_scope(self):
        files = [file("api/build.gradle.kts", "current"), file("api/src/App.java", "code")]
        for path in ("file:///tmp/project/api/../build.gradle.kts", "file:///tmp/project/api/%2e%2e/build.gradle.kts",
                     "file:///etc/passwd", "file://host/api/build.gradle.kts", "https://host/api/build.gradle.kts",
                     "frontend/main.ts", "file:///tmp/project/not-current.java"):
            with self.subTest(path=path):
                self.assertIsNone(compiler_diagnostic_selection(files, compile_feedback(path + ":29:1: error"), byte_limit=32768))
        ambiguous = [file("first/src/App.java", "code"), file("second/src/App.java", "code")]
        self.assertIsNone(compiler_diagnostic_selection(ambiguous, compile_feedback("src/App.java:3: error"), byte_limit=32768))

    def test_compiler_scope_respects_byte_file_and_diagnostic_boundaries(self):
        files = [file("api/build.gradle.kts", "é" * 10)]
        valid = compile_feedback("api/build.gradle.kts:29:1: error")
        self.assertIsNone(compiler_diagnostic_selection(files, valid, byte_limit=19))
        self.assertIsNone(compiler_diagnostic_selection(files, valid, byte_limit=20, file_limit=0))
        self.assertIsNotNone(compiler_diagnostic_selection(files, valid, byte_limit=20))
        behavior = compile_feedback("api/build.gradle.kts:29:1: error", check="python-unittest")
        self.assertIsNone(compiler_diagnostic_selection(files, behavior, byte_limit=20))
        hints = compile_feedback("Build failed without a file location")
        hints["repair_target"] = {"source_paths": ["api/build.gradle.kts"]}
        self.assertIsNone(compiler_diagnostic_selection(files, hints, byte_limit=20))

    def test_typescript_locations_resolve_only_unique_current_relative_paths(self):
        files = [file("frontend/src/App.vue", "source"), file("frontend/src/Other.vue", "other")]
        result = compiler_diagnostic_selection(files, compile_feedback(
            "src/App.vue(4,8): error TS2322: Type mismatch", check="frontend-typecheck"), byte_limit=32768)
        self.assertEqual(result["paths"], ["frontend/src/App.vue"])
        self.assertEqual((result["matches"][0]["line"], result["matches"][0]["column"]), (4, 8))

    def test_oversized_compiler_scope_keeps_first_batch_and_pending_paths(self):
        files = [file("frontend/src/A.vue", "é" * 20000), file("frontend/src/B.vue", "é" * 20000)]
        first, second = files[1].path, files[0].path
        feedback = compile_feedback(f"{first}(4,8): error TS2322\n{second}(8,4): error TS2322",
                                    check="frontend-typecheck")
        result = compiler_diagnostic_selection(files, feedback, byte_limit=WHOLE_PROJECT_REPAIR_SELECTED_BYTES)
        self.assertEqual(result["paths"], [first])
        self.assertEqual(result["pending_paths"], [second])
        self.assertEqual([match["path"] for match in result["matches"]], [first, second])
        next_feedback = compile_feedback(f"{second}(8,4): error TS2322", check="frontend-typecheck")
        next_result = compiler_diagnostic_selection(files, next_feedback, byte_limit=WHOLE_PROJECT_REPAIR_SELECTED_BYTES)
        self.assertEqual(next_result["paths"], [second])
        self.assertEqual(next_result["pending_paths"], [])


class GradleDialectRecoveryTests(unittest.TestCase):
    SETTINGS = "api/settings.gradle.kts"
    BUILD = "api/build.gradle.kts"
    SOURCES = {
        SETTINGS: "rootProject.name = 'example'\n",
        BUILD: "plugins {\n    id 'java-library'\n}\ndependencies {\n    implementation 'g:a:1'\n}\n"
               "tasks.withType(JavaCompile) {\n    options.encoding = 'UTF-8'\n}\n",
    }

    def test_settings_error_includes_the_same_syntax_family_in_the_build_script(self):
        files = [file(path, content) for path, content in self.SOURCES.items()]
        feedback = compile_feedback(f"e: file:///tmp/project/{self.SETTINGS}:1:20: "
                                    "The character literal does not conform to the expected type String!")
        result = compiler_diagnostic_selection(files, feedback, byte_limit=65536)
        self.assertEqual(result["paths"], [self.BUILD, self.SETTINGS])
        self.assertEqual([item["path"] for item in result["matches"]], [self.SETTINGS])
        findings = result["gradle_kotlin_dsl_findings"]
        self.assertEqual({item["kind"] for item in findings}, {
            "plugin-call", "dependency-call", "string-assignment", "task-type"})

    def test_truncated_gradle_diagnostic_can_bind_a_reported_line_to_current_source(self):
        files = [file(path, content) for path, content in self.SOURCES.items()]
        feedback = compile_feedback("  Line 5:     implementation 'g:a:1'\n             ^ Unexpected tokens\n")
        result = compiler_diagnostic_selection(files, feedback, byte_limit=65536, file_limit=1)
        self.assertEqual(result["paths"], [self.BUILD])
        self.assertEqual(result["pending_paths"], [self.SETTINGS])
        self.assertEqual(result["matches"], [])
        feedback = compile_feedback("  Line 5:     implementation 'other:artifact:1'\n     ^ Unexpected tokens\n")
        self.assertIsNone(compiler_diagnostic_selection(files, feedback, byte_limit=65536))

    def test_valid_kotlin_comments_strings_and_character_literals_are_ignored(self):
        self.assertEqual(gradle_kotlin_dsl_findings({self.BUILD: '''/*
            id 'comment-only'
            */
            val letter = 'x'
            val escaped = '\\n'
            val documentation = """
                implementation 'example-only'
            """
            // tasks.withType(JavaCompile) {
            tasks.withType<JavaCompile>().configureEach { options.encoding = "UTF-8" }
        '''}), [])

    def test_partial_gradle_edit_gets_a_retry_before_recompilation(self):
        flow = object.__new__(WholeProjectBuildRun)
        flow.scoped_repairs = True
        flow._regression_feedback = None
        flow.dependency_reference = None
        flow.source_bytes = 131072
        flow.model_inputs = lambda data: {**data, "plan": {}}
        flow.stages = {}
        flow.artifact_type = ProjectArtifact
        artifact = ProjectArtifact(summary="Candidate", files=[
            {"path": path, "content": content} for path, content in self.SOURCES.items()])
        suite = SimpleNamespace(files=[], commands=[])
        partial = self.SOURCES[self.BUILD].replace("id 'java-library'", 'id("java-library")')
        corrected = (partial.replace("implementation 'g:a:1'", 'implementation("g:a:1")')
                     .replace("withType(JavaCompile)", "withType<JavaCompile>().configureEach")
                     .replace("'UTF-8'", '"UTF-8"'))

        def repair(stage, _role, _operation, visible, **arguments):
            flow.stages[stage] = {}
            if stage.endswith("_edit_retry"):
                self.assertEqual(visible[self.BUILD], partial)
                self.assertEqual(visible[self.SETTINGS], self.SOURCES[self.SETTINGS])
                self.assertEqual({row["kind"] for row in arguments["inputs"]["remaining_gradle_kotlin_dsl"]},
                                 {"dependency-call", "string-assignment", "task-type"})
                return {"files": {self.BUILD: corrected, self.SETTINGS: 'rootProject.name = "example"\n'}}
            return {"files": {self.BUILD: partial}, "receipt": {"scratch_cleanup_confirmed": True,
                "events": [{"type": "step_finish", "part": {"reason": "stop"}}]}}

        flow._opencode = Mock(side_effect=repair)
        result = flow._patch("build_compile_repair_1_1", "repairer", artifact, suite,
                             {"compiler_path_selection": {"gradle_kotlin_dsl_findings":
                                 gradle_kotlin_dsl_findings(self.SOURCES)}},
                             paths=list(self.SOURCES), compile_patch=False)
        self.assertEqual(flow._opencode.call_count, 2)
        self.assertEqual({entry.path: entry.content for entry in result.files},
                         {self.BUILD: corrected, self.SETTINGS: 'rootProject.name = "example"\n'})


class SelectionBudgetRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.artifact = ProjectArtifact(summary="Current source", files=[
            {"path": path, "content": "é" * 20000} for path in ("src/A.vue", "src/B.vue")])
        self.flow = object.__new__(WholeProjectBuildRun)
        self.flow.contract = {"repair_selected_bytes": WHOLE_PROJECT_REPAIR_SELECTED_BYTES}
        self.flow.stages = {}
        self.feedback = {"repair_target": {"summary": "Fix the failing behavior"}}

    def responses(self, *selections):
        proposals = iter(selections)

        def generate(stage, *_args, tool_policy):
            self.assertIn(stage, STAGES)
            self.assertEqual(tool_policy, INLINE_SELECTION_POLICY)
            self.flow.stages[stage] = {}
            return {"files": next(proposals), "diagnosis": {"diagnosis": ["Observed"],
                                                            "changes": ["Fix"], "verification": ["Rerun"]}}

        self.flow._generate = Mock(side_effect=generate)

    def test_over_budget_model_selection_restarts_with_exact_size_feedback(self):
        both = ["src/A.vue", "src/B.vue"]
        self.responses(both, [both[1]])
        paths, _ = self.flow._select_repair_paths("build_compile_repair_2_1", self.artifact, self.feedback)
        self.assertEqual(paths, [both[1]])
        first, retry = [call.args for call in self.flow._generate.call_args_list]
        self.assertEqual((first[0], retry[0]), ("build_compile_repair_2_1_select", "build_compile_repair_2_1_retry_select"))
        self.assertEqual(retry[4]["previous_selection_size_error"], {
            "paths": both, "selected_utf8_bytes": 80000, "max_selected_utf8_bytes": 65536})
        self.assertEqual({key: value for key, value in retry[4].items() if key != "previous_selection_size_error"}, first[4])
        self.assertEqual(self.flow.stages[retry[0]]["selected_utf8_bytes"], 40000)

    def test_second_over_budget_choice_is_rejected_after_one_restart(self):
        both = ["src/A.vue", "src/B.vue"]
        self.responses(both, both)
        with self.assertRaisesRegex(ValueError, "still exceeds 65536 UTF-8 bytes after one retry"):
            self.flow._select_repair_paths("repair_1", self.artifact, self.feedback)
        self.assertEqual(self.flow._generate.call_count, 2)


class SelectionSourceTests(unittest.TestCase):
    def test_compiler_absolute_path_prioritizes_exact_current_configuration(self):
        files = [file("api/A.java", "unrelated"), file("api/build.gradle.kts", "bootJar { enabled = true }"),
                 file("api/settings.gradle.kts", 'rootProject.name = "fixture"')]
        source = selection_source(files, {"compiler": "file:///app/project/api/build.gradle.kts:51:1 Unresolved reference bootJar"})
        self.assertEqual(source[0], {"path": "api/build.gradle.kts", "content": files[1].content})
        self.assertEqual({entry["path"] for entry in source}, {entry.path for entry in files})

    def test_semantic_projection_prioritizes_current_view_and_its_dependency(self):
        files = [file("frontend/aaa.ts", "unrelated" * 40),
                 file("frontend/views/Cart.vue", "import CartService from '../CartService';\nCartService.total()"),
                 file("frontend/CartService.ts", "export default { total: () => 1 }"),
                 file("old/CartService.ts", "unrelated implementation")]
        source = selection_source(files, {"review": [{"path": "frontend/views/Cart.vue", "message": "Wrong total"}]},
                                  byte_limit=120)
        self.assertEqual(source[0]["path"], "frontend/views/Cart.vue")
        self.assertEqual(source[1]["path"], "frontend/CartService.ts")
        self.assertNotIn("frontend/aaa.ts", [entry["path"] for entry in source])
        self.assertEqual(selection_source(files, {"review": [{"path": "frontend/views/Cart.vue", "message": "Wrong total"}]},
                                         byte_limit=120), source)

    def test_stale_paths_never_become_source_or_inventory(self):
        files = [file("current/Feature.java", "current source")]
        source = selection_source(files, {"source_paths": ["former/Service.java"]})
        details = projection_details([{"path": files[0].path}], source)
        self.assertEqual(details["included_paths"], ["current/Feature.java"])
        self.assertEqual(details["omitted_paths"], [])
        self.assertEqual(source[0]["content"], "current source")

    def test_behavior_warning_identifies_actual_source_before_unrelated_alphabetic_files(self):
        files = [file("api/A.java", "unrelated" * 40),
                 file("frontend/ProductForm.vue", "<VNumberInput v-model=\"price\" />")]
        source = selection_source(files, {"root_warnings": ["Failed to resolve component: VNumberInput"]}, byte_limit=60)
        self.assertEqual(source, [{"path": files[1].path, "content": files[1].content}])


class SelectionAdmissionTests(unittest.TestCase):
    def workflow(self):
        flow = object.__new__(WholeProjectBuildRun)
        flow.runtime = SimpleNamespace(whole_project_prompt_tokenizers={})
        flow.bundle = {"generation": {"repairer": {"context": SELECTION_OUTPUT_TOKENS + 6808}}}
        flow.brief = {"sources": [{"url": "https://example.test/brief", "content": "Original supplied contract"}]}
        flow.output = {"plan": {"acceptance_criteria": ["Preserve the specified behavior"]}}
        flow.stages = {}

        def generate(stage, role, operation, visible, **arguments):
            flow.stages[stage] = {}
            return {"value": {"diagnosis": {"diagnosis": ["Observed"], "changes": ["Fix"], "verification": ["Rerun"]},
                              "files": ["api/build.gradle.kts"]}}

        flow._opencode = Mock(side_effect=generate)
        return flow

    def test_admission_trims_current_source_preserves_brief_and_complete_inventory(self):
        flow = self.workflow()
        flow.output["plan"].update(goal="Ship the required behavior", assumptions=["Public contract preserved"],
                                   implementation_steps=["Very long proposed implementation narrative" * 1000])
        paths = ("api/build.gradle.kts", "api/Other.java")
        source = [{"path": paths[0], "content": "bootJar {}"}, {"path": paths[1], "content": "x" * 9000}]
        flow._generate("repair_1_select", "repairer", scoped_repair_selection(paths), "Select the exact repair scope.",
                       {"implementation_tree": [{"path": path} for path in paths], "current_source": source}, {},
                       tool_policy=INLINE_SELECTION_POLICY)
        arguments = flow._opencode.call_args.kwargs
        payload = json.loads(arguments["payload"]["messages"][1]["content"])
        self.assertEqual(payload["sources"], flow.brief["sources"])
        self.assertEqual(payload["plan"]["acceptance_criteria"], flow.output["plan"]["acceptance_criteria"])
        self.assertEqual(payload["plan"]["assumptions"], flow.output["plan"]["assumptions"])
        self.assertNotIn("implementation_steps", payload["plan"])
        self.assertEqual(payload["plan_projection"]["omitted_fields"], ["implementation_steps"])
        self.assertIn("implementation_steps", flow.output["plan"])
        self.assertEqual(payload["current_source"], source[:1])
        self.assertEqual({entry["path"] for entry in payload["implementation_tree"]}, set(paths))
        self.assertEqual(payload["source_projection"]["omitted_paths"], [paths[1]])
        self.assertEqual(arguments["tool_policy"], INLINE_SELECTION_POLICY)
        self.assertEqual(flow._opencode.call_count, 1)

    def test_tokenizer_identity_failure_is_never_reinterpreted_as_context_pressure(self):
        flow = self.workflow()
        flow.bundle["whole_project_prompt_tokenizers"] = {"repairer": {"digest": "approved"}}
        with self.assertRaisesRegex(ValueError, "tokenizer differs"):
            flow._generate("repair_1_select", "repairer", scoped_repair_selection(("app.py",)), "Choose files.",
                           {"implementation_tree": [{"path": "app.py"}], "current_source": [{"path": "app.py", "content": "app"}]},
                           {}, tool_policy=INLINE_SELECTION_POLICY)
        flow._opencode.assert_not_called()

    def test_missing_context_room_cannot_dispatch_metadata_only_selection(self):
        flow = self.workflow()
        flow.bundle["generation"]["repairer"]["context"] = 3000
        with self.assertRaisesRegex(ValueError, "inference was not dispatched"):
            flow._generate("repair_1_select", "repairer", scoped_repair_selection(("app.py",)), "Choose files.",
                           {"implementation_tree": [{"path": "app.py"}], "current_source": [{"path": "app.py", "content": "app"}]},
                           {}, tool_policy=INLINE_SELECTION_POLICY)
        flow._opencode.assert_not_called()


class SelectionEventTests(unittest.TestCase):
    def test_live_guard_stops_tools_and_second_step_before_terminal_collection(self):
        for unexpected in ({"type": "tool_use", "part": {"tool": "read"}}, {"type": "step_start"}):
            observed = []
            guard = selection_event_guard(observed.append)
            guard({"type": "step_start"})
            with self.assertRaisesRegex(ValueError, "one model step and no tool calls"):
                guard(unexpected)
            self.assertEqual(observed[-1], unexpected)


if __name__ == "__main__":
    unittest.main()
