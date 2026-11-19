"""Public dependency signatures are image-bound and reach only source repairs."""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from orchestrator.project_build import ProjectArtifact
from orchestrator.project_dependency_reference import POLICY, validate_dependency_reference
from orchestrator.store import digest
from orchestrator.whole_project import WholeProjectBuildRun
from orchestrator.whole_project_checkpoint import _BINDINGS, binding_digest


IMAGE = "sha256:" + "a" * 64


def catalog():
    documents = [{"name": "example.PublicApi", "content": "public interface example.PublicApi { public int size(); }"}]
    return {"policy": POLICY, "image_id": IMAGE, "documents": documents, "digest": digest(documents)}


class DependencyReferenceTests(unittest.TestCase):
    def test_catalog_is_explicit_image_bound_and_hash_checked(self):
        value = catalog()
        self.assertIsNone(validate_dependency_reference(None, IMAGE))
        validated = validate_dependency_reference(value, IMAGE)
        self.assertEqual(validated, value)
        validated["documents"][0]["content"] = "mutated projection"
        self.assertNotEqual(validated, value)
        with self.assertRaisesRegex(ValueError, "pinned verifier image"):
            validate_dependency_reference(value, "sha256:" + "b" * 64)
        value["documents"][0]["content"] += " changed"
        with self.assertRaisesRegex(ValueError, "digest changed"):
            validate_dependency_reference(value, IMAGE)

    def test_document_budget_duplicate_and_extra_source_fields_are_rejected(self):
        cases = []
        extra = catalog()
        extra["files"] = [{"path": "reference/App.java", "content": "not public API evidence"}]
        cases.append(extra)
        duplicate = catalog()
        duplicate["documents"] *= 2
        duplicate["digest"] = digest(duplicate["documents"])
        cases.append(duplicate)
        oversized = catalog()
        oversized["documents"][0]["content"] = "é" * (16 * 1024 + 1)
        oversized["digest"] = digest(oversized["documents"])
        cases.append(oversized)
        too_many = catalog()
        too_many["documents"] = [{"name": str(number), "content": "public API"} for number in range(33)]
        too_many["digest"] = digest(too_many["documents"])
        cases.append(too_many)
        for value in cases:
            with self.subTest(keys=value.keys()), self.assertRaises(ValueError):
                validate_dependency_reference(value, IMAGE)

    def test_absent_catalog_preserves_historical_checkpoint_binding_and_present_catalog_is_bound(self):
        bundle = {"request": {"prompt": "Existing task"}, "sandbox": {"image_id": IMAGE}}
        old_keys = [key for key in _BINDINGS if key not in {
            "project_dependency_reference", "whole_project_regression_suite", "whole_project_test_author_policy",
            "whole_project_test_author_migration", "same_model_role_isolation",
            "project_review_policy", "whole_project_gate_policy", "fresh_project"}]
        legacy = {key: bundle.get(key) for key in old_keys if key != "run_timeout_seconds" or key in bundle}
        legacy.update(workers={}, reference_digest=None)
        self.assertEqual(binding_digest(bundle), digest(legacy))
        with_docs = {**bundle, "project_dependency_reference": catalog()}
        self.assertNotEqual(binding_digest(with_docs), binding_digest(bundle))
        changed = deepcopy(with_docs)
        changed["project_dependency_reference"]["documents"][0]["content"] += " modified"
        self.assertNotEqual(binding_digest(changed), binding_digest(with_docs))

    def test_repair_projection_never_reads_harness_reference_source(self):
        flow = object.__new__(WholeProjectBuildRun)
        flow.scoped_repairs = False
        flow.dependency_reference = validate_dependency_reference(catalog(), IMAGE)
        flow.source_bytes = 131072
        flow.bundle = {"project_reference": {"files": [{"path": "app.py", "content": "HARNESS_REFERENCE_SECRET"}]}}
        flow.brief = {"prompt": "Implement the public task"}
        flow.output = {"plan": {"acceptance_criteria": ["Respect the public contract"]}}
        flow.stages = {}
        flow.artifact_type = ProjectArtifact
        artifact = ProjectArtifact(summary="Candidate", files=[{"path": "app.py", "content": "candidate source"}])
        suite = SimpleNamespace(files=[], commands=["run frozen tests"])

        def repair(stage, role, operation, visible, **arguments):
            flow.stages[stage] = {}
            self.assertNotIn("HARNESS_REFERENCE_SECRET", repr((visible, arguments)))
            self.assertEqual(arguments["inputs"]["project_dependency_reference"], catalog())
            self.assertIn("method overloads instead of guessing an API", arguments["instruction"])
            return {"files": {"app.py": "repaired source"}}

        flow._opencode = Mock(side_effect=repair)
        repaired = flow._patch("repair_1", "repairer", artifact, suite, {}, paths=["app.py"], compile_patch=False)
        self.assertEqual(repaired.files[0].content, "repaired source")


if __name__ == "__main__":
    unittest.main()
