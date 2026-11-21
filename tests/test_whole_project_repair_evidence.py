"""Recovery uses current source, prior effects and one proved settled selection restart."""
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
import unittest
from unittest.mock import Mock

from orchestrator.project_build import ProjectArtifact
from orchestrator.project_opencode import OpenCodeOutputLimitError
from orchestrator.project_opencode_whole import OpenCodeSettledResponseError
from orchestrator.whole_project import WholeProjectBuildRun
from orchestrator.whole_project_repair_evidence import repair_feedback, repair_record, failure_observation
from orchestrator.whole_project_selection import SELECTION_OUTPUT_TOKENS, selection_source
from orchestrator.store import Store


def artifact(operand="increment", mapped=False):
    return ProjectArtifact(summary="Candidate", files=[
        {"path": "api/TrackedUrl.java", "content": '@Document(collection="tracked_urls")\nclass TrackedUrl {\n'
            + ('@Field(targetType=FieldType.DECIMAL128)\n' if mapped else '')
            + 'private BigDecimal estimatedRevenue;\n}\n'},
        {"path": "api/TrackedUrlService.java", "content": 'import org.springframework.data.mongodb.core.MongoTemplate;\n'
            'class TrackedUrlService { void click() {\n'
            '// The stored estimatedRevenue field is BSON Decimal128.\n'
            'Update update = new Update().inc("estimatedRevenue", ' + operand + ');\n'
            'mongoTemplate.updateFirst(query, update, TrackedUrl.class);\n}}'},
        {"path": "frontend/A.ts", "content": "irrelevant " * 5000}])


def feedback(identifier="first"):
    return {"failed_execution": {"commands": [{"sandbox_stage_results": {
        "cleanup_confirmed": True, "checks": {"python-unittest": {"status": "failed"}}, "failures": [{
            "stage": "python-unittest", "test_failures_complete": True, "omitted_test_failures": 0,
            "test_failure_count": 1, "tests_run": 5, "test_failures": [{"test": "test_click (Smoke)",
                "assertion": "POST /tracked-urls/" + identifier + "/click returned 500"}]}]}}]}}


class RepairEvidenceTests(unittest.TestCase):
    def test_operand_only_repair_keeps_mapping_risk_and_failed_attempt_for_any_source_stage(self):
        before, after = artifact(), artifact("new Decimal128(increment)")
        record = repair_record("whole_self_repair", before, after, feedback())
        result = repair_feedback(after, feedback("different-id"), [record])["repair_evidence"]
        fact = result["source_facts"][0]
        self.assertFalse(fact["stored_bson_type_observed"])
        self.assertEqual(set(fact["source_paths"]), {"api/TrackedUrl.java", "api/TrackedUrlService.java"})
        self.assertIn("without an explicit BSON target", fact["observed"])
        self.assertTrue(result["prior_attempts"][0]["same_complete_failure_observation"])
        self.assertIn("new Decimal128", result["prior_attempts"][0]["changed_files"][0]["diff"])
        self.assertEqual(repair_feedback(artifact(mapped=True), feedback(), [])['repair_evidence']['source_facts'], [])
        projection = selection_source(after.files, result, byte_limit=4096)
        self.assertEqual({row['path'] for row in projection}, {"api/TrackedUrl.java", "api/TrackedUrlService.java"})

    def test_incomplete_diagnostics_or_different_candidate_never_claim_a_repeated_failure(self):
        before, after = artifact(), artifact("new Decimal128(increment)")
        record = repair_record("repair_1", before, after, feedback())
        incomplete = feedback()
        incomplete['failed_execution']['commands'][0]['sandbox_stage_results']['failures'][0]['test_failures_complete'] = False
        self.assertIsNone(failure_observation(incomplete))
        self.assertFalse(repair_feedback(after, incomplete, [record])['repair_evidence']['prior_attempts'][0]['same_complete_failure_observation'])
        self.assertEqual(repair_feedback(before, feedback(), [record])['repair_evidence']['prior_attempts'], [])


class SettledSelectionRecoveryTests(unittest.TestCase):
    def test_rejected_selection_is_durable_and_never_redispatched_on_journal_reentry(self):
        flow = object.__new__(WholeProjectBuildRun)
        identity = {'model_digest': 'fixture'}
        settings = {'context': 98304, 'think': 'xhigh', 'output_tokens': 65536}
        flow.bundle = {'generation': {'repairer': settings}, 'roles': {'repairer': 'fixture'},
                       'build_contract': {'test_bytes': 65536}}
        flow._worker_identity = Mock(return_value=(identity, settings))
        flow.stages, flow._observed_seconds = {}, {}
        error = self.capped()
        worker = Mock()
        worker.generate.side_effect = error
        flow.workers = {'repairer': worker}
        with TemporaryDirectory() as directory:
            store = Store(Path(directory))
            try:
                run_id = store.create(flow.bundle)
                flow.state = {'run_id': run_id}
                flow.runtime = SimpleNamespace(store=store, _guard=Mock(), inference_lock=Lock(),
                    events={run_id: Event()}, deadline=float('inf'))
                for _ in range(2):
                    with self.assertRaises(OpenCodeSettledResponseError):
                        flow._opencode('whole_self_repair_2_select', 'repairer', 'generate', {}, payload={})
                self.assertEqual(worker.generate.call_count, 1)
                self.assertEqual(store.cached(run_id, 'whole_self_repair_2_select')['settled_response_proof'], error.proof)
                self.assertFalse(store.has_unknown_calls())
            finally:
                store.close()

    def run_selection(self, errors):
        flow = object.__new__(WholeProjectBuildRun)
        flow.contract, flow.stages = {"repair_selected_bytes": 65536}, {}
        proposal = {"files": ["api/TrackedUrl.java", "api/TrackedUrlService.java"],
                    "diagnosis": {"diagnosis": ["Mapping risk"], "changes": ["Inspect both"], "verification": ["Recheck"]}}
        replies = iter(errors + [proposal])
        def generate(stage, *args, **kwargs):
            flow.stages[stage] = {}
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply
        flow._generate = Mock(side_effect=generate)
        return flow

    def capped(self, **changes):
        return OpenCodeSettledResponseError('structured-output-limit', {
            'completed_requests': 1, 'scratch_cleanup_confirmed': True,
            'native_receipts_digest': 'a' * 64, 'session_id': 'bound', **changes})

    def test_settled_limit_gets_one_smaller_current_source_restart(self):
        flow = self.run_selection([self.capped()])
        paths, _ = flow._select_repair_paths('whole_self_repair_2', artifact(), feedback())
        self.assertEqual(paths, ['api/TrackedUrl.java', 'api/TrackedUrlService.java'])
        first, second = flow._generate.call_args_list
        self.assertEqual(second.args[0], 'whole_self_repair_2_retry_select')
        self.assertLessEqual(sum(len(row['content'].encode()) for row in second.args[4]['current_source']), 16384)
        self.assertIn('prior_attempts', second.args[4]['repair_evidence'])
        self.assertEqual(SELECTION_OUTPUT_TOKENS, 65536)

    def test_settled_compaction_proof_keeps_one_primary_decision_and_one_restart(self):
        flow = self.run_selection([self.capped(completed_requests=3, primary_requests=1, compaction_requests=2)])
        paths, _ = flow._select_repair_paths('whole_self_repair_2', artifact(), feedback())
        self.assertEqual(len(paths), 2)
        self.assertEqual(flow._generate.call_count, 2)

    def test_second_limit_unknown_completion_or_missing_cleanup_stops_without_extra_dispatch(self):
        for errors in ([self.capped(), self.capped()], [OpenCodeOutputLimitError(1)],
                       [self.capped(scratch_cleanup_confirmed=False)], [self.capped(completed_requests=2)]):
            flow = self.run_selection(errors)
            with self.assertRaises((OpenCodeSettledResponseError, OpenCodeOutputLimitError)):
                flow._select_repair_paths('repair_1', artifact(), feedback())
            self.assertEqual(flow._generate.call_count, min(len(errors), 2))


if __name__ == '__main__':
    unittest.main()
