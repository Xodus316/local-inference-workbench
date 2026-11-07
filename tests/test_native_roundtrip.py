"""Exercise the HTTP approval path and the real native streaming adapter."""
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from orchestrator.config import Config
from orchestrator.runtime import Runtime
from orchestrator.server import Server


class NativeStandIn(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, body, mime="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        models = [] if self.path == "/api/ps" else [{"name": model} for model in (
            "gemma4:12b", "qwen3.5:9b-q4_K_M", "pplx-decider:review-32k")]
        self.reply(json.dumps({"models": models}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/v1/systemone":
            self.server.selections.append(body)
            self.reply(json.dumps({"answers": {"selection": {
                "type": "choice", "choice": "B", "confidence": 0.88,
                "probabilities": {"A": 0.10, "B": 0.88, "combine": 0.01, "neither": 0.01}}}}).encode())
            return
        if self.path == "/api/show" and body["model"] == "pplx-decider:review-32k":
            self.reply(b'{"capabilities":["decision"],"parameters":"num_ctx 32768"}')
            return
        if self.path == "/api/show":
            self.reply(b'{"capabilities":["completion","thinking"],"model_info":{"local.context_length":32768}}')
            return
        if self.path != "/api/chat":
            self.send_error(404)
            return
        self.server.generations.append(body)
        result = json.dumps({"answer": "16 boxes", "key_claims": ["16 boxes"], "assumptions": [],
                             "uncertainties": [], "evidence": ["ledger:L2"], "verification_needed": []})
        frames = [{"message": {"role": "assistant", "content": result[:18]}, "done": False},
                  {"message": {"role": "assistant", "content": result[18:]}, "done": True,
                   "done_reason": "stop", "eval_count": 45, "prompt_eval_count": 200}]
        self.reply(b"".join(json.dumps(f).encode()+b"\n" for f in frames), "application/x-ndjson")


class NativeRoundTrip(unittest.TestCase):
    def serve(self, server):
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 5)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_verified_native_choice_preserves_selected_citation(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        native = ThreadingHTTPServer(("127.0.0.1", 0), NativeStandIn)
        native.generations = []
        native.selections = []
        port = self.serve(native)
        runtime = Runtime(Path(tmp.name), Config(ollama_url=f"http://127.0.0.1:{port}"))
        self.addCleanup(runtime.close)
        service_port = self.serve(Server(0, runtime))

        def api(path, value=None):
            body = json.dumps(value).encode() if value is not None else None
            req = Request(f"http://127.0.0.1:{service_port}"+path, body,
                          headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=5) as response:
                return json.load(response)

        row = api("/api/runs", {"mode": "verified", "prompt": "How many boxes shipped?",
                                "sources": [{"id":"ledger", "text":"Estimate: 18\nShipped: 16"}]})
        self.assertEqual(row["bundle"]["mode"], "verified")
        self.assertEqual(native.generations, [])
        path = "/api/runs/" + row["id"]
        api(path + "/approve", {"digest": row["digest"]})
        limit = time.monotonic() + 10
        while time.monotonic() < limit:
            row = api(path)
            if row["status"] not in {"queued", "running"}:
                break
            time.sleep(.03)
        self.assertEqual(row["status"], "completed", row.get("error"))
        answer = row["result"]["output"]
        self.assertEqual(answer["answer"], "16 boxes")
        self.assertEqual(answer["evidence"][0]["quote"], "Shipped: 16")
        self.assertEqual(len(native.generations), 2)
        self.assertEqual(len(native.selections), 1)
        self.assertEqual(native.selections[0]["state"]["B"], answer)
        self.assertIn("ledger:L2", native.generations[0]["format"]["properties"]["evidence"]["items"]["enum"])
        self.assertEqual(native.generations[0]["options"]["num_ctx"], 32768)
