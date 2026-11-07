import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from orchestrator.config import Config, Research
from orchestrator.runtime import Runtime
from orchestrator.store import digest


class Fixture:
    def __init__(self):
        self.calls = []
        self.choice = "A"
        self.confidence = .95
        self.cite = []
        self.unknown = False
        self.limit = False

    def admit(self, *args):
        pass

    def complete(self, model, settings, payload, cancel, deadline):
        body = json.loads(payload["messages"][1]["content"])
        self.calls.append((model, body))
        if self.unknown:
            raise OSError("stream lost")
        keys = payload["schema"]["properties"]
        if "goal" in keys:
            value = {"goal":"Update the answer", "acceptance_criteria":["Output 42"],
                     "assumptions":[],"steps":[{"id":"edit","title":"Change value","capability":"propose_edit",
                     "files":["answer.py"],"depends_on":[],"evidence_ids":["repo-1"]}],
                     "proposed_tests":["Verify the printed value"],"risks":[]}
        elif "edits" in keys:
            value = {"summary":"Correct numeric output","edits":[{"path":"answer.py","content":"print(42)\n"}]}
        elif "approved" in keys:
            value = {"approved":True,"summary":"The change matches the requested output."}
        elif "queries" in keys:
            value = {"queries":["public library opening hours"]}
        else:
            value = {"answer":"42","key_claims":[],"assumptions":[],"uncertainties":["Check hours"],
                     "verification_needed":[],"evidence":self.cite}
        return {"choices":[{"message":{"content":json.dumps(value)},"finish_reason":"length" if self.limit else "stop"}]}

    def decide(self, model, payload, cancel, deadline):
        self.calls.append((model, payload))
        return {"answers":{"selection":{"type":"choice","choice":self.choice,"confidence":self.confidence,
                "probabilities":{"A":.25,"B":.25,"combine":.25,"neither":.25}}}}


class FakeSearch:
    def available(self):
        return True

    def search(self, queries, store, run_id, guard=None):
        return {"ok":True,"sources":[{"id":"web-1","kind":"source","title":"Public hours","text":"10 a.m."}],
                "queries":queries, "status":"completed"}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root/"repo"; self.repo.mkdir()
        self.provider = Fixture()
        self.config = Config(repositories=[str(self.repo)], research=Research(enabled=True),
                             test_commands={"check": [sys.executable,"-c",
                                "import subprocess, sys; assert subprocess.check_output([sys.executable, 'answer.py']).strip() == b'42'"]})
        self.runtime = Runtime(self.root/"data", self.config, self.provider)
        self.addCleanup(self.runtime.close)

    def execute(self, mode, **options):
        row = self.runtime.prepare({"mode":mode,"prompt":"Display the answer", **options})
        self.runtime.approve(row["id"], row["digest"])
        for _ in range(200):
            current = self.runtime.store.get(row["id"])
            if current["status"] not in {"queued","running"}:
                return current
            time.sleep(.01)
        self.fail("Workflow did not finish")

    def test_fast_and_auto_share_single_primary_and_validate_citations(self):
        self.provider.cite = ["note:L2"]
        for mode in ("auto","fast"):
            row = self.execute(mode,sources=[{"id":"note","text":"line one\nline two"}])
            self.assertEqual(row["status"],"completed",row)
            self.assertEqual(row["result"]["output"]["evidence"][0]["quote"],"line two")
            self.assertEqual([c["stage"] for c in row["calls"]], ["primary"])
        self.provider.cite = ["note:L7"]
        self.assertEqual(self.execute("fast")["status"],"failed")

    def test_balanced_keeps_analysts_independent_and_synthesizes_search_snippets(self):
        self.runtime.researcher = FakeSearch()
        row = self.execute("balanced",research=True)
        self.assertEqual(row["status"],"completed",row["error"])
        self.assertEqual([c["stage"] for c in row["calls"]],["primary","secondary","research_queries","synthesis"])
        self.assertEqual(self.provider.calls[0][1],self.provider.calls[1][1])
        self.assertIn("web-1",str(self.provider.calls[-1][1]["sources"]))
        self.assertEqual(self.provider.calls[2][0],self.config.roles["fast_secondary"])

    def test_verified_selects_one_combines_or_escalates(self):
        for choice, end in [("A","decider"),("B","decider"),("combine","synthesis"),("neither","deep")]:
            self.provider.choice = choice
            row = self.execute("verified")
            self.assertEqual(row["status"],"completed",row["error"])
            self.assertEqual(row["calls"][-1]["stage"],end)
        self.provider.confidence = .4
        self.assertEqual(self.execute("verified")["status"],"review_required")
        self.assertEqual(self.execute("deep")["calls"][0]["model"],self.config.roles["deep_reasoner"])

    def test_nonstop_and_unknown_completion(self):
        self.provider.limit = True
        row = self.execute("fast")
        self.assertEqual(row["status"],"failed")
        self.assertEqual(self.runtime.store.cached(row["id"],"primary")["choices"][0]["finish_reason"], "length")
        self.provider.unknown = True
        row = self.execute("fast");self.assertEqual(row["status"],"blocked")
        with self.assertRaises(ValueError):
            self.runtime.approve(row["id"],row["digest"],resume=True)

    def test_reviewed_edit_applies_only_in_new_worktree_and_runs_approved_test(self):
        def git(*args):
            subprocess.run(["git","-C",str(self.repo),*args],check=True,capture_output=True)
        git("init");(self.repo/"answer.py").write_text("print(0)\n")
        git("add","answer.py");git("-c","user.name=Example","-c","user.email=example@example.invalid","commit","-m","fixture")
        scope={"repository":str(self.repo),"files":["answer.py"]}
        self.assertEqual(self.execute("plan",**scope)["status"],"completed")
        row=self.execute("edit",tests=["check"],**scope)
        self.assertEqual(row["status"],"awaiting_apply",row["error"])
        self.assertIn("+print(42)",row["result"]["diff"])
        with self.assertRaises(ValueError):
            self.runtime.apply(row["id"],"wrong digest")
        (self.repo/"answer.py").write_text("print(1)\n")
        with self.assertRaises(ValueError):
            self.runtime.apply(row["id"],row["result"]["apply_digest"])
        (self.repo/"answer.py").write_text("print(0)\n")
        result=self.runtime.apply(row["id"],row["result"]["apply_digest"])
        self.assertEqual(result["status"],"completed",result)
        self.assertEqual(result["result"]["tests"][0]["exit_code"],0)
        self.assertEqual((self.repo/"answer.py").read_text(),"print(0)\n")
        self.assertEqual((Path(result["result"]["worktree"])/"answer.py").read_text(),"print(42)\n")


if __name__=="__main__":
    unittest.main()
