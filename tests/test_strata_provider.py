"""Offline protocol tests for the opt-in Strata backend."""

import hashlib
import base64
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from orchestrator.config import Config, StrataBackend, StrataIdentity
from orchestrator.provider import NativeProvider
from orchestrator.runtime import Runtime
from orchestrator.strata import StrataError


MODEL = "strata-flash"
TEMPLATE = "pinned-template"
MODEL_PATH = "C:/models/pinned/model.gguf"


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def configuration(url):
    identity = StrataIdentity(
        model=MODEL, engine="0.1.58", context=32768, runtime_sha256="1" * 64,
        model_manifest_sha256="2" * 64, template_sha256=sha(TEMPLATE),
        model_path_sha256=sha(MODEL_PATH), config_sha256="3" * 64,
        memory_mode="resident_complement", native_attempts_observable=False,
        approximation_switches={"route_tail_skip": 0, "drafting": "off"},
    )
    roles = {name: MODEL for name in Config().roles}
    generation = {name: {"context": 32768, "output_tokens": None, "think": "high",
                         "temperature": 1.0, "top_p": .95, "top_k": 20,
                         "min_p": 0, "presence_penalty": 0,
                         "repetition_penalty": 1}
                  for name in roles}
    return Config(backend="strata", strata=StrataBackend(url=url, identity=identity),
                  roles=roles, generation=generation)


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def json_reply(self, value, status=200):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/v1/status":
            self.json_reply({
                "service": "strata", "model": MODEL, "loaded": True,
                "engine": "0.1.58", "context": {"native": 32768},
                "concurrency": {"serving": 1},
                "structured_output": {"method": "prompt_and_validate"},
                **self.server.attempt_contract,
            })
        elif self.path == "/props":
            self.json_reply({"chat_template": self.server.template, "model_path": MODEL_PATH})
        elif self.path == "/v1/models":
            self.json_reply({"data": [{"id": MODEL}]})
        else:
            self.send_error(404)

    def do_POST(self):
        self.server.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        if self.server.close_early:
            self.close_connection = True
            return
        if self.server.http_error:
            self.json_reply({"error": {"message": "Synthetic native HTTP error"},
                             "strata_native_attempts": self.server.counter}, status=502)
            return
        answer = json.dumps({"answer": "42", "key_claims": [], "assumptions": [],
                             "uncertainties": [], "evidence": [], "verification_needed": []})
        chunks = [
            {"choices": [{"delta": {"reasoning_content": "private reasoning"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": answer}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 17, "completion_tokens": 23}},
        ]
        if self.server.terminal_content:
            chunks[1]["choices"][0]["delta"]["content"] = ""
            chunks[-1]["choices"][0]["delta"]["content"] = answer
        if self.server.has_counter:
            chunks[-1]["strata_native_attempts"] = self.server.counter
        if self.server.stream_error:
            chunks = [{"error": {"message": "Synthetic native error"},
                       "strata_native_attempts": self.server.counter}]
        if self.server.close_after_content:
            chunks = chunks[:2]
        data = b"".join(b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks)
        if not self.server.close_after_content:
            data += b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class StrataProviderTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        self.server.requests = []
        self.server.template = TEMPLATE
        self.server.close_early = False
        self.server.attempt_contract = {}
        self.server.has_counter = False
        self.server.counter = None
        self.server.stream_error = False
        self.server.close_after_content = False
        self.server.terminal_content = False
        self.server.http_error = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.config = configuration(f"http://127.0.0.1:{self.server.server_address[1]}")

    def test_uncapped_mapping_preserves_reasoning_final_and_attempt_unknown(self):
        provider = NativeProvider(self.config)
        settings = self.config.generation_for("fast_primary").model_dump()
        raw = provider.complete(
            MODEL, settings,
            {"messages": [{"role": "user", "content": "seven times six"}],
             "schema": {"type": "object", "properties": {"answer": {"type": "string"}}}},
            threading.Event(), time.monotonic() + 5,
        )
        self.assertEqual(raw["choices"][0]["finish_reason"], "stop")
        self.assertEqual(raw["choices"][0]["message"]["reasoning_content"], "private reasoning")
        self.assertEqual(raw["native"], {"backend": "strata", "attempts": None, "terminal_state": "stop"})
        sent = self.server.requests[0]
        self.assertNotIn("max_tokens", sent)
        self.assertNotIn("response_format", sent)
        self.assertEqual(sent["reasoning_budget_tokens"], 0)
        self.assertEqual(sent["reasoning_effort"], "high")
        self.assertEqual((sent["temperature"], sent["top_p"], sent["top_k"]), (1.0, .95, 20))
        self.assertIsNone(sent["tools"])
        self.assertIn("JSON Schema", sent["messages"][0]["content"])

    def test_identity_mismatch_and_xhigh_stop_before_dispatch(self):
        provider = NativeProvider(self.config)
        self.server.template = "changed-template"
        with self.assertRaises(StrataError):
            provider.identity()
        self.assertEqual(self.server.requests, [])
        self.server.template = TEMPLATE
        settings = {**self.config.generation_for("fast_primary").model_dump(), "think": "xhigh"}
        with self.assertRaises(StrataError):
            provider.complete(MODEL, settings, {"messages": [], "schema": {}},
                              threading.Event(), time.monotonic() + 5)
        self.assertEqual(self.server.requests, [])

    def test_changed_identity_invalidates_prepared_approval(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        runtime = Runtime(Path(directory.name), self.config)
        self.addCleanup(runtime.close)
        row = runtime.prepare({"prompt": "answer", "mode": "fast"})
        self.assertEqual(row["bundle"]["backend"]["name"], "strata")
        self.server.template = "changed-template"
        with self.assertRaises(StrataError):
            runtime.approve(row["id"], row["digest"])
        self.assertEqual(self.server.requests, [])

    def test_connection_loss_is_unknown_and_never_retried(self):
        self.server.close_early = True
        provider = NativeProvider(self.config)
        with self.assertRaises(StrataError) as caught:
            provider.complete(
                MODEL, self.config.generation_for("fast_primary").model_dump(),
                {"messages": [{"role": "user", "content": "x"}], "schema": {}},
                threading.Event(), time.monotonic() + 5,
            )
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(caught.exception.diagnostics.get("native_completion"), "unknown")

    def complete(self):
        return NativeProvider(self.config).complete(
            MODEL, self.config.generation_for("fast_primary").model_dump(),
            {"messages": [{"role": "user", "content": "Synthetic request"}], "schema": {}},
            threading.Event(), time.monotonic() + 5)

    def observable_attempts(self):
        self.config.strata.identity.native_attempts_observable = True
        self.config.strata.identity.approximation_switches["native_attempts_policy"] = "serial-gen-v1"
        self.server.attempt_contract = {"native_single_attempt": True, "native_attempts_policy": "serial-gen-v1"}

    def test_reviewed_serial_patch_reports_actual_one_native_attempt(self):
        self.observable_attempts()
        self.server.has_counter = True
        self.server.counter = 1
        raw = self.complete()
        self.assertEqual(raw["native"]["attempts"], 1)
        self.assertTrue(raw["native_transport"]["complete"])
        self.assertIn(b'"strata_native_attempts": 1', base64.b64decode(raw["native_transport"]["bytes"]))
        self.assertEqual(len(self.server.requests), 1)

    def test_unconfigured_counter_is_never_promoted_to_attempt_evidence(self):
        self.server.has_counter = True
        self.server.counter = 1
        self.server.attempt_contract = {"native_single_attempt": True, "native_attempts_policy": "serial-gen-v1"}
        raw = self.complete()
        self.assertIsNone(raw["native"]["attempts"])

    def test_observable_attempts_require_explicit_policy_and_server_metadata(self):
        self.config.strata.identity.native_attempts_observable = True
        with self.assertRaises(StrataError):
            self.complete()
        self.config.strata.identity.approximation_switches["native_attempts_policy"] = "serial-gen-v1"
        with self.assertRaises(StrataError):
            self.complete()
        self.server.attempt_contract = {"native_single_attempt": 1, "native_attempts_policy": "serial-gen-v1"}
        with self.assertRaises(StrataError):
            self.complete()
        self.assertEqual(self.server.requests, [])

    def test_missing_invalid_or_multiple_attempt_counter_fails_without_retry(self):
        self.observable_attempts()
        for present, count in ((False, None), (True, None), (True, 0), (True, True),
                               (True, "1"), (True, 1.0), (True, -1), (True, 2)):
            self.server.has_counter, self.server.counter = present, count
            before = len(self.server.requests)
            with self.subTest(present=present, count=count), self.assertRaises(StrataError) as caught:
                self.complete()
            self.assertEqual(len(self.server.requests), before + 1)
            self.assertEqual(caught.exception.diagnostics["native_completion"], "unknown")
            self.assertIsNotNone(caught.exception.raw_response)
            self.assertFalse(caught.exception.raw_response["native_transport"]["complete"])
            if count == 2:
                self.assertEqual(caught.exception.raw_response["native"]["attempts"], 2)

    def test_native_error_counter_and_abrupt_partial_bytes_are_retained(self):
        self.observable_attempts()
        self.server.stream_error = True
        self.server.counter = 1
        with self.assertRaises(StrataError) as caught:
            self.complete()
        self.assertEqual(caught.exception.raw_response["native"]["attempts"], 1)
        self.assertIn(b'Synthetic native error', base64.b64decode(caught.exception.raw_response["native_transport"]["bytes"]))
        self.server.stream_error = False
        self.server.http_error = True
        with self.assertRaises(StrataError) as caught:
            self.complete()
        self.assertEqual(caught.exception.raw_response["native"]["attempts"], 1)
        self.assertEqual(caught.exception.diagnostics["backend_http_status"], 502)
        self.server.http_error = False
        self.server.close_after_content = True
        with self.assertRaises(StrataError) as caught:
            self.complete()
        message = caught.exception.raw_response["choices"][0]["message"]
        self.assertEqual(message["reasoning_content"], "private reasoning")
        self.assertIn('"answer": "42"', message["content"])
        self.assertIsNone(caught.exception.raw_response["native"]["attempts"])

    def test_terminal_content_is_retained_before_counter_rejection(self):
        self.observable_attempts()
        self.server.terminal_content = True
        with self.assertRaises(StrataError) as caught:
            self.complete()
        self.assertIn('"answer": "42"', caught.exception.raw_response["choices"][0]["message"]["content"])
        self.assertFalse(caught.exception.raw_response["native_transport"]["complete"])

    def test_cancellation_before_native_dispatch_has_strong_not_started_evidence(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(StrataError) as caught:
            NativeProvider(self.config).complete(
                MODEL, self.config.generation_for("fast_primary").model_dump(),
                {"messages": [], "schema": {}}, cancel, time.monotonic() + 5)
        self.assertEqual(caught.exception.diagnostics["native_completion"], "not_started")
        self.assertEqual(self.server.requests, [])


if __name__ == "__main__":
    unittest.main()
