import threading
import unittest

from orchestrator.graph import build


class Readers:
    def __init__(self, *, barrier=None, primary_ok=True):
        self.barrier = barrier
        self.primary_ok = primary_ok
        self.calls = []

    def bundle(self, state):
        return {"mode": "balanced"}

    def needs_research(self, state):
        return False

    def assess(self, state, stage, role, *, compare=False):
        self.calls.append(stage)
        if compare:
            assert {"primary", "secondary"} <= state.keys()
            return {"ok": True}
        assert stage == "secondary" or "secondary" not in state
        if self.barrier:
            assert "primary" not in state and "secondary" not in state
            self.barrier.wait(5)
        return {"ok": stage != "primary" or self.primary_ok}


class EmbeddedGraphTests(unittest.TestCase):
    def test_parallel_readers_join_before_synthesis(self):
        host = Readers(barrier=threading.Barrier(2))
        graph = build(host, parallel_analysis=lambda _: True)
        result = graph.invoke({"run_id": "example"})
        self.assertEqual(sorted(host.calls), ["primary", "secondary", "synthesis"])
        self.assertTrue(result["final"]["ok"])

    def test_independent_secondary_can_finish_after_primary_failed(self):
        host = Readers(primary_ok=False)
        result = build(host, complete_pair_on_error=True).invoke({"run_id": "example"})
        self.assertEqual(host.calls, ["primary", "secondary"])
        self.assertNotIn("final", result)
