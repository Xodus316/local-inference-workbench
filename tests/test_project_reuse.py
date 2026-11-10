"""Fresh-approval checkpoint imports with CPU-only fake providers and executors."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.config import Config
from orchestrator.project_reuse import prepare_project_reuse, load_project_reuse
from orchestrator.runtime import Runtime
from orchestrator.store import digest, encoded
from test_project_build import BRIEF, Models, Executor, POLICY, TEST

SOURCE_SDK = "a" * 40
CURRENT_SDK = "b" * 40


class CheckpointReuseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.models, self.executor = Models(), Executor()
        self.models.backend_source = "correct backend"
        self.runtime = Runtime(Path(temporary.name), Config(build={"offline_project_enabled": True}),
                               self.models, self.executor)
        self.addCleanup(self.runtime.close)
        row = self.runtime.prepare({"mode": "build", "prompt": BRIEF, "build_profile": POLICY})
        self.runtime.approve(row["id"], row["digest"])
        self.runtime.queue.join()
        self.source = self.runtime.store.get(row["id"])
        self.assertEqual(self.source["status"], "completed", self.source.get("result"))
        self.models.seen.clear()
        self.executor.projects.clear()
        self.executor.tests.clear()

    def bytes(self, stage, suffix):
        return self.runtime.store.artifact(self.source["id"], stage, suffix).read_bytes()

    def manifest(self, through="implementation", completed_groups=1, **overrides):
        return prepare_project_reuse(overrides.pop("source", self.source), overrides.pop("artifact_bytes", self.bytes),
            overrides.pop("bundle", self.source["bundle"]), source_sdk_revision=SOURCE_SDK,
            current_sdk_revision=CURRENT_SDK, through=through, completed_groups=completed_groups, **overrides)

    def run_fresh(self, manifest):
        bundle = {**copy.deepcopy(self.source["bundle"]), "project_reuse": manifest}
        row = self.runtime.store.get(self.runtime.store.create(bundle))
        self.assertNotEqual(row["id"], self.source["id"])
        self.assertFalse(row["approved"])
        self.assertEqual(self.models.seen, [])
        self.runtime.approve(row["id"], row["digest"])
        self.runtime.queue.join()
        return self.runtime.store.get(row["id"])

    def test_reuses_exact_prefix_under_new_approval_and_runs_full_tests(self):
        original = copy.deepcopy(self.source)
        artifacts = {p.name: p.read_bytes() for p in (self.runtime.store.directory / "artifacts" / self.source["id"]).iterdir()}
        manifest = self.manifest()
        self.assertEqual(self.models.seen, [])
        row = self.run_fresh(manifest)
        self.assertEqual(row["status"], "completed", row.get("result"))
        self.assertEqual([c["stage"] for c in row["calls"]], ["implement_group_2"])
        self.assertEqual(self.executor.tests, [[TEST]])
        self.assertEqual(row["result"]["output"]["suite"], self.source["result"]["output"]["suite"])
        self.assertEqual(self.runtime.store.get(self.source["id"]), original)
        self.assertEqual({p.name: p.read_bytes() for p in (self.runtime.store.directory / "artifacts" / self.source["id"]).iterdir()}, artifacts)
        inputs = json.loads(self.models.seen[0][3]["messages"][1]["content"])
        self.assertEqual(inputs["selected_source"][0]["content"], "correct backend")

    def test_all_groups_reused_creates_no_native_calls_and_reexecutes_acceptance(self):
        row = self.run_fresh(self.manifest(completed_groups=2))
        self.assertEqual(row["status"], "completed", row.get("result"))
        self.assertEqual(row["calls"], [])
        self.assertEqual(self.models.seen, [])
        self.assertEqual(self.executor.tests, [[TEST]])
        # The completed fresh run can itself supply an inherited checkpoint;
        # original component provenance is preserved, not fabricated native calls.
        second = prepare_project_reuse(row, lambda stage, suffix: self.runtime.store.artifact(row["id"], stage, suffix).read_bytes(),
            row["bundle"], source_sdk_revision=CURRENT_SDK, current_sdk_revision=CURRENT_SDK,
            through="implementation", completed_groups=2)
        group = next(p for p in second["artifact_provenance"] if p["component"] == "group_1")
        self.assertEqual(group["source_run_id"], self.source["id"])

    def test_cross_sdk_inherited_tests_without_original_transition_bytes_reject(self):
        row = self.run_fresh(self.manifest(completed_groups=2))
        retained = lambda stage, suffix: self.runtime.store.artifact(row["id"], stage, suffix).read_bytes()
        kwargs = {"source_sdk_revision": CURRENT_SDK, "current_sdk_revision": "c" * 40}
        for through, groups in (("tests", 0), ("group_plan", 0), ("implementation", 1)):
            with self.subTest(through=through), self.assertRaisesRegex(ValueError, "original fixture transition bytes"):
                prepare_project_reuse(row, retained, row["bundle"], through=through, completed_groups=groups, **kwargs)
        safe = prepare_project_reuse(row, retained, row["bundle"], through="plan", **kwargs)
        self.assertIsNone(safe["checkpoint"]["suite"])
        self.assertEqual(safe["artifact_provenance"][0]["source_run_id"], self.source["id"])

    def test_plan_only_does_not_require_frozen_suite_and_missing_suite_rejects(self):
        def missing(stage, suffix):
            if stage == "build_frozen": raise FileNotFoundError("No full frozen suite")
            return self.bytes(stage, suffix)
        manifest = self.manifest("plan", 0, artifact_bytes=missing)
        self.assertIsNone(manifest["checkpoint"]["suite"])
        with self.assertRaises(FileNotFoundError): self.manifest("tests", 0, artifact_bytes=missing)

    def test_each_explicit_tier_starts_at_its_first_missing_stage(self):
        for through, expected in (("plan", "build_tests"), ("tests", "build_project_plan"),
                                  ("group_plan", "implement_group_1")):
            with self.subTest(through=through):
                row = self.run_fresh(self.manifest(through, 0))
                self.assertEqual(row["status"], "completed", row.get("result"))
                self.assertEqual(row["calls"][0]["stage"], expected)
                self.assertEqual(self.executor.tests, [[TEST]])
                self.models.seen.clear(); self.executor.tests.clear()

    def test_unknown_blocked_interrupted_and_unfinished_any_call_reject(self):
        for status in ("blocked", "interrupted", "running", "stopping", "queued"):
            source = copy.deepcopy(self.source); source["status"] = status
            with self.subTest(status=status), self.assertRaises(ValueError): self.manifest(source=source)
        for changes in ({"status": "dispatched"}, {"finished": None}, {"finished": float("nan")},
                        {"backend_diagnostics": {"native_completion": "unknown"}}):
            source = copy.deepcopy(self.source); source["calls"][-1].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.manifest(source=source)
        source = copy.deepcopy(self.source); source["status"] = "stopped"
        self.assertEqual(self.manifest(source=source)["source"]["status"], "stopped")

    def test_missing_or_unknown_raw_even_unselected_later_call_reject(self):
        last = self.source["calls"][-1]["stage"]
        for mutation in ({"choices": [{"finish_reason": None}]}, {"choices": [{"finish_reason": "stop"}], "native": {"cleanup_confirmed": False}}):
            def corrupt(stage, suffix):
                return encoded(mutation).encode() if stage == last else self.bytes(stage, suffix)
            with self.assertRaises(ValueError): self.manifest("plan", 0, artifact_bytes=corrupt)

    def test_environment_brief_generation_image_contract_and_role_changes_reject(self):
        for mutate in (lambda b: b["request"].update(prompt="different"),
                       lambda b: b["sandbox"].update(image_id="sha256:" + "c" * 64),
                       lambda b: b["generation"]["implementer"].update(context=8192),
                       lambda b: b["roles"].update(planner="different"),
                       lambda b: b["build_contract"].update(group_bytes=24575),
                       lambda b: b["build"].update(max_repair_attempts=1),
                       lambda b: b.update(backend={"name": "different"})):
            bundle = copy.deepcopy(self.source["bundle"]); mutate(bundle)
            with self.assertRaises(ValueError): self.manifest(bundle=bundle)

    def test_original_approval_verified_before_private_projection(self):
        source = copy.deepcopy(self.source)
        original_public = source["bundle"]
        source["bundle"] = {"private_host": original_public}
        source["digest"] = digest(source["bundle"])
        source["approval"]["digest"] = source["digest"]
        result = self.manifest(source=source, project_bundle=lambda bundle: bundle["private_host"])
        self.assertEqual(result["source"]["approval_digest"], source["digest"])
        self.assertEqual(result["source"]["projection_digest"], digest(original_public))
        source["bundle"]["extra_authority"] = True
        called = []
        with self.assertRaises(ValueError):
            self.manifest(source=source, project_bundle=lambda bundle: called.append(True) or original_public)
        self.assertEqual(called, [])

    def test_projections_cannot_change_native_call_state(self):
        with self.assertRaises(ValueError):
            self.manifest(project_call=lambda call: {**call, "finished": 999})

    def test_trusted_private_role_stage_aliases_keep_original_artifact_provenance(self):
        source = copy.deepcopy(self.source)
        for call in source["calls"]:
            call["stage"] = "host_" + call["stage"]
            call["role"] = "host_" + call["role"]
        def project(call):
            return {**call, "stage": call["stage"].removeprefix("host_"), "role": call["role"].removeprefix("host_")}
        def original_bytes(stage, suffix):
            return self.bytes(stage.removeprefix("host_"), suffix)
        manifest = self.manifest(source=source, project_call=project, artifact_bytes=original_bytes)
        self.assertEqual(next(p for p in manifest["artifact_provenance"] if p["component"] == "plan")["stage"], "host_build_plan")
        self.assertEqual(manifest["source"]["native_receipts"][0]["stage"], "host_build_plan")

    def test_reconstructs_suite_after_a_completed_syntax_repair(self):
        self.models.test_file = {**TEST, "content": TEST["content"].replace("'observable')", "'observable'")}
        original_complete = self.models.complete
        def complete(*args):
            if self.models.stage.startswith("build_tests_syntax_"):
                return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"files": [TEST]})}}]}
            return original_complete(*args)
        self.models.complete = complete
        row = self.runtime.prepare({"mode": "build", "prompt": BRIEF, "build_profile": POLICY})
        self.runtime.approve(row["id"], row["digest"]); self.runtime.queue.join()
        repaired = self.runtime.store.get(row["id"])
        self.assertEqual(repaired["status"], "completed", repaired.get("result"))
        manifest = prepare_project_reuse(repaired,
            lambda stage, suffix: self.runtime.store.artifact(row["id"], stage, suffix).read_bytes(),
            repaired["bundle"], source_sdk_revision=SOURCE_SDK, current_sdk_revision=CURRENT_SDK,
            through="tests")
        self.assertEqual(manifest["checkpoint"]["suite"]["files"], [TEST])

    def test_current_fixture_transition_guard_rejects_changed_assertions(self):
        from test_project_fixture_checks import HTTP, FIXED_HTTP
        original = {**TEST, "content": HTTP.replace("test_delete", "test_api")}
        revised = {**TEST, "content": FIXED_HTTP.replace("test_delete", "test_api")}
        self.models.test_file, self.models.fixture_repairs = original, [revised]
        row = self.runtime.prepare({"mode": "build", "prompt": BRIEF, "build_profile": POLICY})
        self.runtime.approve(row["id"], row["digest"]); self.runtime.queue.join()
        repaired = self.runtime.store.get(row["id"])
        self.assertEqual(repaired["status"], "completed", repaired.get("result"))
        def retained(stage, suffix):
            return self.runtime.store.artifact(row["id"], stage, suffix).read_bytes()
        kwargs = dict(source_sdk_revision=SOURCE_SDK, current_sdk_revision=CURRENT_SDK, through="tests")
        manifest = prepare_project_reuse(repaired, retained, repaired["bundle"], **kwargs)
        self.assertEqual(manifest["checkpoint"]["suite"]["files"], [revised])
        # Simulate an older engine accepting a changed test method and storing a
        # self-consistent final suite; current transition checks must still fail.
        changed = {**revised, "content": revised["content"].replace("self.assert", "self.assertTrue(True)  # self.assert", 1)}
        def tampered(stage, suffix):
            value = json.loads(retained(stage, suffix))
            if stage == "build_tests_fixture_1":
                value["choices"][0]["message"]["content"] = json.dumps({"files": [changed]})
            elif stage == "build_frozen":
                value["suite"]["files"] = [changed]
                value["suite_digest"] = digest(value["suite"])
            return encoded(value).encode()
        with self.assertRaisesRegex(ValueError, "assertions|protected test"):
            prepare_project_reuse(repaired, tampered, repaired["bundle"], **kwargs)

    def test_manifest_payload_provenance_and_current_sdk_tampering_reject(self):
        manifest = self.manifest()
        for mutate in (lambda m: m["checkpoint"]["groups"][0]["files"][0].update(content="changed"),
                       lambda m: m["artifact_provenance"].pop(),
                       lambda m: m["source"].update(status="blocked"),
                       lambda m: m["selection"].update(completed_groups=2)):
            bad = copy.deepcopy(manifest); mutate(bad)
            with self.assertRaises(ValueError): load_project_reuse({**self.source["bundle"], "project_reuse": bad})
        with self.assertRaises(ValueError):
            load_project_reuse({**self.source["bundle"], "project_reuse": manifest}, current_sdk_revision="c" * 40)

    def test_noncontiguous_group_and_changed_frozen_suite_reject(self):
        def tampered(stage, suffix):
            value = json.loads(self.bytes(stage, suffix))
            if stage == "project_after_1": value["files"] = []
            return encoded(value).encode()
        with self.assertRaises(ValueError): self.manifest(artifact_bytes=tampered)
        def changed_suite(stage, suffix):
            value = json.loads(self.bytes(stage, suffix))
            if stage == "build_frozen": value["suite"]["files"][0]["content"] += "\n# changed"
            return encoded(value).encode()
        with self.assertRaises(ValueError): self.manifest(artifact_bytes=changed_suite)
        def recomputed_suite(stage, suffix):
            value = json.loads(self.bytes(stage, suffix))
            if stage == "build_frozen":
                value["suite"]["files"][0]["content"] += "\n# changed"
                value["suite_digest"] = digest(value["suite"])
            return encoded(value).encode()
        with self.assertRaises(ValueError): self.manifest(artifact_bytes=recomputed_suite)


if __name__ == "__main__": unittest.main()
