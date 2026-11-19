from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orchestrator.build_models import BuildFile
from orchestrator.build_workflow import build_model_payload
from orchestrator.project_build import ProjectArtifact, ProjectBuildRun
from orchestrator.project_opencode_review import _terminal_review
from orchestrator.project_review import numbered_review_source
from orchestrator.project_review_anchors import source_anchor_registry
from orchestrator.project_review_flow import SourceAnchorReviewMixin
from orchestrator.store import digest, STAGES


class MemoryStore:
    def __init__(self):
        self.saved = {}

    def save_artifact(self, run_id, stage, suffix, value):
        self.saved[(stage, suffix)] = deepcopy(value)


class ReviewFlow(SourceAnchorReviewMixin):
    def __init__(self, responses, *, worker=False):
        self.responses, self.calls = iter(responses), []
        self.bundle = {"project_review_policy": "source-anchors-v1", "roles": {"reviewer": "test-model"},
                       "generation": {"reviewer": {"context": 160000, "think": True}}}
        self.contract = {"review_output_reserve": 4096, "max_review_chunks": 16,
                         "review_blocking_severities": ["P0", "P1"], "code_review_policy": "fixture-review"}
        self.environment = None
        self.stages = {}
        self.output = {"verification": {"code_reviews": []}}
        self.state = {"run_id": "fixture-run"}
        self.runtime = SimpleNamespace(store=MemoryStore())
        self.opencode_reviewer = object() if worker else None

    def model_inputs(self, data):
        return {"prompt": "Implement the supplied public contract.", **data}

    def _next(self, stage, payload):
        self.calls.append((stage, deepcopy(payload)))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        self.runtime.store.save_artifact(self.state["run_id"], stage, "raw", {"value": response})
        self.stages[stage] = {"worker_receipt_digest": "fixture-receipt"}
        return deepcopy(response)

    def ask(self, stage, role, model_type, instruction, **data):
        return self._next(stage, build_model_payload(model_type, instruction, self.model_inputs(data)))

    def _review_projection(self, files, project, plan):
        return {file.path: file.content for file in files}

    def _worker_review(self, stage, settings, payload, visible):
        self.visible = visible
        return self._next(stage, payload)


class ReviewFlowTests(unittest.TestCase):
    def setUp(self):
        self.artifact = ProjectArtifact(summary="Fixture", files=[BuildFile(path="api/main.py", content="read()\nreturn None\n")])
        registry = source_anchor_registry(self.artifact.files)
        self.anchor = next(key for key, row in registry["anchors"].items() if row["line"] == 2)
        self.finding = {"anchor_id": self.anchor, "severity": "P1", "category": "correctness",
                        "title": "Required result missing", "description": "The result is required but absent.",
                        "fix": "Return the required result."}
        self.plan = SimpleNamespace(contract="Return the required result.")

    def run_review(self, flow, *, tests_passed=True):
        return flow.code_review(0, self.artifact, self.artifact, self.plan, {}, tests_passed=tests_passed)

    def test_normal_raw_and_distinct_expansion_preserve_native_prose(self):
        native = {"findings": [self.finding, deepcopy(self.finding)]}
        flow = ReviewFlow([native])
        result = self.run_review(flow)
        stage = result["chunks"][0]["stage"]
        expanded = flow.runtime.store.saved[(stage + "_expanded_review", "raw")]
        self.assertEqual(flow.runtime.store.saved[(stage, "raw")], {"value": native})
        self.assertFalse(expanded["expansion"]["canonical_is_literal_native_output"])
        self.assertEqual(expanded["expansion"]["native_value_digest"], digest(native))
        self.assertEqual(len(result["findings"]), 2)
        data = json.loads(flow.calls[0][1]["messages"][-1]["content"])
        self.assertEqual(data["implementation"], numbered_review_source(self.artifact.files))
        self.assertIn(self.anchor, json.dumps(flow.calls[0][1]["schema"]))
        self.assertEqual(data["review_output_contract"]["native_schema_digest"], digest(flow.calls[0][1]["schema"]))

    def test_one_shared_correction_for_schema_and_anchor_errors(self):
        invalid = {"findings": [{**self.finding, "description": "x" * 901}]}
        flow = ReviewFlow([invalid, {"findings": [{**self.finding, "anchor_id": "foreign"}]}])
        result = self.run_review(flow)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(len(flow.calls), 2)
        self.assertEqual([x["validation_details"]["kind"] for x in result["validation_attempts"]], ["schema", "source_anchor"])
        second = json.loads(flow.calls[1][1]["messages"][-1]["content"])
        first = json.loads(flow.calls[0][1]["messages"][-1]["content"])
        self.assertEqual(second["implementation"], first["implementation"])
        self.assertEqual(second["review_validation_feedback"]["original_review"], invalid)

    def test_correction_cannot_drop_or_downgrade_grounded_p1_or_p2(self):
        for severity in ("P1", "P2"):
            valid = {**self.finding, "severity": severity}
            original = {"findings": [valid, {**self.finding, "description": "x" * 901}]}
            flow = ReviewFlow([original, {"findings": [{**self.finding, "severity": "P3"}]}])
            result = self.run_review(flow)
            self.assertEqual([f["severity"] for f in result["findings"]], [severity, "P3"])
            self.assertEqual(result["chunks"][0]["findings_retained_from_initial_review"], 1)

    def test_invalid_correction_retains_initial_grounded_finding_without_pass(self):
        original = {"findings": [self.finding, {**self.finding, "anchor_id": "bad"}]}
        flow = ReviewFlow([original, {"findings": [{**self.finding, "fix": " "}]}])
        result = self.run_review(flow)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["blocking_findings"], 1)
        self.assertEqual(len(result["findings"]), 1)

    def test_transport_and_malformed_json_errors_never_retry(self):
        for error in (RuntimeError("native_completion_unknown"), ValueError("malformed JSON"),
                      ValueError("length terminal"), InterruptedError("cancelled")):
            flow = ReviewFlow([error])
            with self.subTest(error=error), self.assertRaises(type(error)):
                self.run_review(flow)
            self.assertEqual(len(flow.calls), 1)
            self.assertEqual(flow.output["verification"]["code_reviews"], [])

    def test_unknown_correction_propagates_and_large_feedback_never_trimmed(self):
        flow = ReviewFlow([{"findings": [{**self.finding, "anchor_id": "bad"}]}, RuntimeError("unknown")])
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            self.run_review(flow)
        self.assertEqual(len(flow.calls), 2)
        original = {"findings": [{**self.finding, "description": "x" * 100000}]}
        flow = ReviewFlow([original])
        result = self.run_review(flow)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(len(flow.calls), 1)
        self.assertEqual(flow.runtime.store.saved[(flow.calls[0][0], "raw")], {"value": original})

    def test_inconclusive_verification_does_not_grant_correction(self):
        flow = ReviewFlow([{"findings": [{**self.finding, "anchor_id": "bad"}]}])
        result = self.run_review(flow, tests_passed=False)
        self.assertEqual(len(flow.calls), 1)
        self.assertEqual(result["status"], "incomplete")

    def test_opencode_uses_same_anchor_schema_source_and_bounded_correction(self):
        flow = ReviewFlow([{"findings": [{**self.finding, "fix": "x" * 601}]}, {"findings": [self.finding]}], worker=True)
        result = self.run_review(flow)
        self.assertEqual(result["status"], "changes_required")
        self.assertEqual(flow.visible, {file.path: file.content for file in self.artifact.files})
        self.assertEqual(result["chunks"][0]["worker_receipt_digest"], "fixture-receipt")
        self.assertEqual(flow.calls[0][1]["schema"], flow.calls[1][1]["schema"])

    def test_full_source_and_schema_must_fit_before_dispatch(self):
        flow = ReviewFlow([{"findings": []}])
        flow.bundle["generation"]["reviewer"]["context"] = 1000
        with self.assertRaisesRegex(ValueError, "Complete review source"):
            self.run_review(flow)
        self.assertFalse(flow.calls)

    def test_selected_public_entry_and_durable_sidecar_stage_registration(self):
        flow = ReviewFlow([{"findings": []}])
        result = ProjectBuildRun.code_review(flow, 0, self.artifact, self.artifact, self.plan, {}, tests_passed=True)
        self.assertEqual(result["validation_policy"], "bounded-native-source-anchor-review-v1")
        for suffix in ("", "_citation_retry"):
            stage = "build_code_review_0_chunk_1" + suffix
            self.assertIn(stage + "_source_anchors", STAGES)
            self.assertIn(stage + "_expanded_review", STAGES)

    def test_opencode_terminal_requires_normal_stop_and_strict_json(self):
        def events(text, reason="stop"):
            return [{"type": "step_start", "part": {"messageID": "one"}},
                    {"type": "text", "part": {"messageID": "one", "text": text}},
                    {"type": "step_finish", "part": {"messageID": "one", "reason": reason}}]
        self.assertEqual(_terminal_review(events('{"findings":[]}')), {"findings": []})
        for value in (events('{"findings":[]}', "length"), events('{"findings":[],"findings":[]}'),
                      events("not JSON"), events('[]')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _terminal_review(value)


if __name__ == "__main__":
    unittest.main()
