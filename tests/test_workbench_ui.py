"""Exercise the local browser routes and panel lifecycle without model inference."""
import base64
import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from html.parser import HTMLParser
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

from orchestrator.config import Config
from orchestrator.desktop import LocalProcess, local_json, save_config
from orchestrator.panel import close_ui
from orchestrator.server import Server


class ChatBackend:
    def __init__(self):
        self.calls = []

    def models(self):
        return [{"name":"test-model"}, {"name":"external:cloud"}]

    def loaded_models(self):
        return []

    def show(self, model):
        return {"capabilities":["completion","thinking"], "model_info":{"model.context_length":32768},
                "thinking":{"values":[False,True],"default":True}}

    def stream_chat(self, **kwargs):
        self.calls.append(kwargs)
        yield {"done":False,"message":{"thinking":"Checking."}}
        yield {"done":True,"done_reason":"stop","message":{"content":"Completed."},"eval_count":3}


class ChatIntegration(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.backend = ChatBackend()
        self.runtime = SimpleNamespace(store=SimpleNamespace(directory=Path(directory.name),list=lambda:[]),
                                       provider=SimpleNamespace(ollama=self.backend),
                                       config=Config(), inference_lock=threading.Lock(), queue=Queue(),active_run_id=None)
        self.server = Server(0, self.runtime)
        worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval":.05}, daemon=True)
        worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(close_ui, self.server)
        self.addCleanup(self.server.shutdown)

    def request(self, path, body=None, headers=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            conn.request("GET" if body is None else "POST", path,
                         None if body is None else json.dumps(body),
                         {"Content-Type":"application/json", **(headers or {})})
            reply = conn.getresponse()
            text = reply.read().decode()
            return reply.status, json.loads(text) if reply.getheader("Content-Type","").startswith("application/json") else text
        finally:
            conn.close()

    def test_loopback_origin_and_panels(self):
        self.assertEqual(self.request("/chat/api/state", headers={"Origin":"https://foreign.invalid"})[0],403)
        self.assertEqual(self.request("/api/status", headers={"Host":"foreign.invalid"})[0],403)
        for path,expected in [("/panel","Prepare for review"),("/","Local Inference Workbench"),
                              ("/chat/","New conversation"),("/panel/app.js","apply_digest"),("/chat/app.js","regenerate")]:
            status, text = self.request(path)
            self.assertEqual(status,200)
            self.assertIn(expected,text)

    def test_panel_offers_build_and_starts_with_downloads_hidden(self):
        class Controls(HTMLParser):
            def __init__(self):
                super().__init__()
                self.by_id = {}
                self.modes = []

            def handle_starttag(self, tag, attrs):
                fields = dict(attrs)
                if "id" in fields:
                    self.by_id[fields["id"]] = fields
                if tag == "option":
                    self.modes.append(fields.get("value"))

        status, page = self.request("/panel")
        self.assertEqual(status, 200)
        controls = Controls()
        controls.feed(page)
        self.assertIn("build", controls.modes)
        self.assertIn("hidden", controls.by_id["build-files"])
        for element in ("build-path", "copy-file", "download-file", "download-manifest", "build-verification"):
            self.assertIn(element, controls.by_id)

    def test_stream_history_attachments_and_duplicate_submission(self):
        status,models = self.request("/chat/api/models")
        self.assertEqual(status,200,models)
        self.assertEqual([m["name"] for m in models["models"] if m["local"]],["test-model"])
        status,upload = self.request("/chat/api/upload", {"name":"notes.txt","content_base64":base64.b64encode(b"blue ink").decode()})
        self.assertEqual(status,200,upload)
        status,created = self.request("/chat/api/create", {"model":"test-model"})
        self.assertEqual(status,200,created)
        cid = created["conversation"]["id"]
        body = {"conversation_id":cid,"revision":created["conversation"]["revision"],"content":"Summarize",
                "attachment_ids":[upload["attachment"]["id"]],"request_id":"repeatable-one"}
        self.runtime.inference_lock.acquire()
        try:
            status,sent = self.request("/chat/api/send",body)
            self.assertEqual(status,200,sent)
            status,duplicate = self.request("/chat/api/send",body)
            self.assertEqual(status,200,duplicate)
            self.assertEqual(sent["job"]["id"],duplicate["job"]["id"])
            self.assertEqual(self.backend.calls,[])
        finally:
            self.runtime.inference_lock.release()
        status,stream = self.request("/chat/api/stream?job_id="+sent["job"]["id"])
        self.assertEqual(status,200)
        self.assertEqual(json.loads(stream.splitlines()[-1])["job"]["status"],"complete")
        self.assertEqual(len(self.backend.calls),1)
        self.assertIn("blue ink",json.dumps(self.backend.calls[0]["messages"]))
        status,history = self.request("/chat/api/state?conversation_id="+cid)
        self.assertEqual(status,200)
        messages = history["conversation"]["messages"]
        self.assertEqual(messages[-1]["content"],"Completed.")
        self.assertEqual(messages[-1]["thinking"],"Checking.")
        self.assertEqual(messages[0]["attachments"][0]["name"],"notes.txt")

    def test_stop_queued_chat(self):
        _,created=self.request("/chat/api/create",{"model":"test-model"})
        with self.runtime.inference_lock:
            _,sent=self.request("/chat/api/send",{"conversation_id":created["conversation"]["id"],"content":"Hello"})
            status,reply=self.request("/chat/api/stop",{"job_id":sent["job"]["id"]})
            self.assertEqual(status,200,reply)
        _,stream=self.request("/chat/api/stream?job_id="+sent["job"]["id"])
        self.assertEqual(json.loads(stream.splitlines()[-1])["job"]["status"],"stopped")
        self.assertEqual(self.backend.calls,[])


class DesktopLifecycle(unittest.TestCase):
    def test_owned_service_starts_and_exits_on_pipe_close(self):
        with tempfile.TemporaryDirectory() as d:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1",0))
                port=sock.getsockname()[1]
            child=LocalProcess(Path(d)/"config.json", Path(d)/"state", port)
            try:
                child.start()
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    try:
                        status=local_json(child.base+"/api/status")
                        break
                    except OSError:
                        if not child.owned:self.fail(child.diagnostics())
                        time.sleep(.1)
                else:self.fail("Service startup timed out")
                with self.assertRaises(ValueError):
                    child.request_stop({**status,"inference_active":True})
                self.assertTrue(child.owned)
                child.request_stop(status)
                self.assertEqual(child.process.wait(timeout=8),0)
            finally:
                if child.process and child.owned:
                    child.process.stdin.close()
                    child.process.wait(timeout=8)
                if child.output:child.output.close()

    def test_stale_edit_and_invalid_config_keep_disk_file(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'settings.json'
            save_config(p,None,Config().model_dump_json())
            original=p.read_bytes()
            with self.assertRaises(ValueError):save_config(p,None,'{}')
            with self.assertRaises(ValueError):save_config(p,original,'{"ollama_url":"https://foreign.invalid"}')
            self.assertEqual(p.read_bytes(),original)
