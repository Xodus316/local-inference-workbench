"""Exercise the real loopback API and native model transport with a local HTTP model fixture."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from orchestrator.config import Config
from orchestrator.runtime import Runtime
from orchestrator.server import Server


class NativeFixture(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def reply(self,value):
        data=json.dumps(value).encode()
        self.send_response(200);self.send_header("Content-Length",str(len(data)));self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.reply({"models":[{"name":"gemma4:12b"}]} if self.path=="/api/tags" else {"version":"local-fixture"})

    def do_POST(self):
        value=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path=="/api/show":
            self.reply({"capabilities":["completion","thinking"], "model_info":{"test.context_length":65536}})
            return
        assert self.path=="/api/chat" and value["stream"] is True
        self.server.requests.append(value)
        result={"answer":"A local answer","key_claims":[],"assumptions":[],"uncertainties":[],
                "verification_needed":[],"evidence":[]}
        frames=[{"done":False,"message":{"content":json.dumps(result)}},
                {"done":True,"done_reason":"stop","eval_count":23,"prompt_eval_count":13,"message":{"content":""}}]
        data=b"".join(json.dumps(x).encode()+b"\n" for x in frames)
        self.send_response(200);self.send_header("Content-Length",str(len(data)));self.end_headers()
        self.wfile.write(data)


class ServiceTests(unittest.TestCase):
    def start_server(self, server):
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(server.server_close);self.addCleanup(thread.join,5);self.addCleanup(server.shutdown)
        return server.server_address[1]

    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.backend=ThreadingHTTPServer(("127.0.0.1",0),NativeFixture)
        self.backend.requests=[]
        port=self.start_server(self.backend)
        runtime=Runtime(Path(directory.name),Config(ollama_url=f"http://127.0.0.1:{port}"))
        self.addCleanup(runtime.close)
        self.port=self.start_server(Server(0,runtime))

    def call(self,method,route,body=None,headers=None):
        connection=http.client.HTTPConnection("127.0.0.1",self.port,timeout=5)
        try:
            connection.request(method,route,json.dumps(body) if body is not None else None,
                               {"Content-Type":"application/json",**(headers or {})})
            response=connection.getresponse();data=response.read()
            return response.status,json.loads(data)
        finally:
            connection.close()

    def test_prepare_approve_and_read_final_native_response(self):
        status,row=self.call("POST","/api/runs",{"prompt":"Summarize","mode":"fast"})
        self.assertEqual(status,200,row)
        self.assertEqual(row["status"],"prepared");self.assertEqual(self.backend.requests,[])
        path="/api/runs/"+row["id"]
        self.assertEqual(self.call("POST",path+"/approve",{"digest":"mismatch"})[0],400)
        self.assertEqual(self.call("POST",path+"/approve",{"digest":row["digest"]})[0],200)
        for _ in range(100):
            _,result=self.call("GET",path)
            if result["status"] not in {"queued","running"}:break
            time.sleep(.02)
        self.assertEqual(result["status"],"completed",result["error"])
        self.assertEqual(result["result"]["output"]["answer"],"A local answer")
        self.assertEqual(len(self.backend.requests),1)
        self.assertEqual(self.backend.requests[0]["options"]["num_predict"],-1)
        self.assertEqual(result["calls"][0]["status"],"terminal")

    def test_reject_rebinding_and_cross_site_writes(self):
        self.assertEqual(self.call("GET","/api/status",headers={"Host":"bad.invalid"})[0],403)
        self.assertEqual(self.call("POST","/api/runs",{"prompt":"x"},{"Origin":"https://bad.invalid"})[0],403)
        self.assertEqual(self.call("POST","/api/runs",{"prompt":"x"},{"Content-Type":"text/plain"})[0],415)
