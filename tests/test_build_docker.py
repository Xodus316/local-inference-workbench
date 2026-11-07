"""Exercise the public Build API with deterministic model outputs and a real Docker daemon."""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from orchestrator.config import Config
from orchestrator.runtime import Runtime
from orchestrator.server import Server


class CodingResponses:
    def __init__(self):
        self.stages = []
        self.current = None

    def admit(self, model, generation, payload, stage):
        self.current = stage
        self.stages.append(stage)
        if "control-only marker" in json.dumps(payload):
            raise AssertionError("The model received the trusted reference")

    def complete(self, model, generation, payload, cancel, deadline):
        test_file = {"path": "tests/test_successor.py", "content":
                     "import unittest\nfrom solution import successor\n\n"
                     "class Numbers(unittest.TestCase):\n    def test_two(self):\n"
                     "        self.assertEqual(successor(2), 3)\n"}
        plan = {"goal": "Increment an integer.", "acceptance_criteria": ["Return n plus one."],
                "implementation_steps": ["Implement successor."], "test_strategy": ["Check 2."],
                "assumptions": [], "risks": []}
        suite = {"implementation_files": ["solution.py"], "commands": ["python3 -m unittest discover -s tests -v"],
                 "files": [test_file], "assumptions": [], "coverage": [
                     {"criterion": 1, "test_file": test_file["path"], "test_name": "Numbers.test_two",
                      "scenario": "Increment 2", "expected_result": "3", "catches": "Missing increment"}]}
        correct = {"summary": "Implement successor.", "files": [
            {"path": "solution.py", "content": "def successor(n):\n    return n + 1\n"}]}
        broken = {"summary": "Implement successor.", "files": [
            {"path": "solution.py", "content": "def successor(n):\n    return n\n"}]}
        repair = {"diagnosis": {"diagnosis": ["Missing increment"], "changes": ["Add one"],
                                "verification": ["Rerun frozen tests"]}, "artifact": correct}
        answer = {"build_plan": plan, "build_tests": suite, "implement": broken, "repair_1": repair}[self.current]
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}


@unittest.skipUnless(os.getenv("WORKBENCH_DOCKER_TESTS") == "1", "needs a preinstalled Docker image")
class BuildInDocker(unittest.TestCase):
    def test_prepare_control_implement_repair_verify_export(self):
        with tempfile.TemporaryDirectory() as root:
            responses = CodingResponses()
            config = Config(build={"image": os.getenv("WORKBENCH_TEST_IMAGE", "python:3.13-alpine")})
            runtime = Runtime(Path(root), config, provider=responses)
            server = Server(0, runtime)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def api(path, value=None):
                body = None if value is None else json.dumps(value).encode()
                req = Request(f"http://127.0.0.1:{server.server_address[1]}{path}", body,
                              headers={"Content-Type": "application/json"})
                with urlopen(req, timeout=30) as reply:
                    return json.load(reply)

            try:
                row = api("/api/runs", {"mode": "build", "prompt": "Implement solution.successor(n).",
                          "control_files": [{"path": "solution.py", "content":
                                            "# control-only marker\ndef successor(n):\n    return 1+n\n"}]})
                self.assertEqual(responses.stages, [])
                path = "/api/runs/" + row["id"]
                api(path + "/approve", {"digest": row["digest"]})
                stop_at = time.monotonic() + 120
                while time.monotonic() < stop_at:
                    row = api(path)
                    if row["status"] not in {"queued", "running"}:
                        break
                    time.sleep(0.15)
                self.assertEqual(row["status"], "completed", row.get("result") or row.get("error"))
                output = row["result"]["output"]
                self.assertEqual(responses.stages, ["build_plan", "build_tests", "implement", "repair_1"])
                reports = output["verification"]
                self.assertEqual([r["status"] for r in reports["controls"]], ["passed"])
                self.assertEqual([r["status"] for r in reports["baselines"]], ["failed", "passed"])
                self.assertTrue(all(r["suite_digest"] == output["suite_digest"] for r in reports["baselines"]))
                self.assertEqual(reports["strength"][-1]["summary"]["detected"], 1)
                files = {f["path"]: f["content"] for f in output["artifact"]["files"]}
                self.assertEqual(files["tests/test_successor.py"], output["suite"]["files"][0]["content"])
                self.assertIn("return n + 1", files["solution.py"])
            finally:
                server.shutdown()
                thread.join(5)
                server.server_close()
                runtime.close()
