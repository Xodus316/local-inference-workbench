"""Synthetic llama.cpp protocol tests; no model runtime or weights are used."""
import base64
import hashlib
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pydantic import ValidationError

from orchestrator.config import Config, LlamaCppBackend, LlamaCppIdentity
from orchestrator.llama_cpp import IDENTITY_MESSAGES, LlamaCppClient, LlamaCppError, LlamaCppCancelled
from orchestrator.provider import NativeProvider
from orchestrator.runtime import Runtime
from orchestrator.store import digest


MODEL = "example-coder"
TEMPLATE = "example-template"
MODEL_PATH = "C:/models/example.gguf"
COMMIT = "a" * 40
ANSWER = json.dumps({"answer": "42", "key_claims": [], "assumptions": [],
                     "uncertainties": [], "evidence": [], "verification_needed": []})


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def render(messages):
    return "example-chat:" + json.dumps(messages, ensure_ascii=False, sort_keys=True)


def configuration(url):
    identity = LlamaCppIdentity(
        model=MODEL, engine="example-version", source_commit=COMMIT, build_info="b1-aaaaaaa",
        context=32768, runtime_sha256="1" * 64, package_sha256="2" * 64,
        model_manifest_sha256="3" * 64, template_sha256=sha(TEMPLATE),
        rendered_template_sha256=sha(render(IDENTITY_MESSAGES)), model_path_sha256=sha(MODEL_PATH),
        config_sha256="4" * 64, launch_attestation_sha256="5" * 64,
        memory_mode="cpu_experts_ram", native_attempts_observable=False,
    )
    roles = {role: MODEL for role in Config().roles}
    generation = {role: {"think": None, "context": 32768, "output_tokens": None,
                         "temperature": 1.0, "top_p": .95, "top_k": 40, "min_p": 0,
                         "presence_penalty": 0, "frequency_penalty": 0, "repetition_penalty": 1}
                  for role in roles}
    return Config(backend="llama-cpp", llama_cpp=LlamaCppBackend(
        url=url, identity=identity, cleanup_timeout_seconds=.3), roles=roles, generation=generation)


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, status=200):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/props":
            props = {"model_alias": MODEL, "build_info": "b1-aaaaaaa", "total_slots": 1,
                     "model_path": MODEL_PATH, "chat_template": TEMPLATE,
                     "is_sleeping": False, "endpoint_slots": True,
                     "default_generation_settings": {"n_ctx": 32768, "params": {"n_predict": -1}}}
            props.update(self.server.props_changes)
            self.reply(props)
        elif self.path == "/v1/models":
            self.reply({"data": [{"id": self.server.model}]})
        elif self.path == "/slots":
            self.reply([{"id": 0, "n_ctx": 32768, "is_processing": self.server.processing}])
        else:
            self.send_error(404)

    def do_POST(self):
        value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/apply-template":
            prompt = render(value["messages"])
            if self.server.changed_render:
                prompt += "changed"
            self.reply({"prompt": prompt})
            return
        if self.path == "/tokenize":
            self.reply({"tokens": [1] * self.server.input_tokens})
            return
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        self.server.requests.append(value)
        self.server.generation_started.set()
        self.server.processing = True
        mode = self.server.mode
        if mode == "drop_before":
            self.close_connection = True
            self.server.processing = False
            return
        if mode == "http_error":
            self.reply({"error": {"message": "example failure"}}, 502)
            self.server.processing = False
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            if mode == "thinking_loop":
                repeated = (("Repeated example reasoning. " * 10) + "\n\n") * 12
                chunk = {"choices": [{"delta": {"reasoning_content": repeated}, "finish_reason": None}]}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                self.wfile.flush()
                return
            if mode == "malformed_stream":
                self.wfile.write(b"data: {broken}\n\n")
                return
            text = "{not json" if mode == "malformed_final" else ANSWER
            chunk = {"choices": [{"delta": {"content": text}, "finish_reason": None}]}
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.flush()
            if mode == "drop_partial":
                return
            if mode == "slow_cancel":
                for _ in range(100):
                    time.sleep(.02)
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                return
            terminal = {"choices": [{"delta": {}, "finish_reason": "length" if mode == "length" else "stop"}]}
            self.wfile.write(b"data: " + json.dumps(terminal).encode() + b"\n\n")
            if mode == "terminal_no_done":
                return
            self.wfile.write(b'data: {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 19}}\n\n')
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except OSError:
            pass
        finally:
            self.server.processing = mode == "busy_after_terminal"
            self.close_connection = True


class LlamaCppProviderTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        self.server.props_changes = {}
        self.server.requests = []
        self.server.model = MODEL
        self.server.changed_render = False
        self.server.input_tokens = 30
        self.server.processing = False
        self.server.mode = "stop"
        self.server.generation_started = threading.Event()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.config = configuration(f"http://127.0.0.1:{self.server.server_address[1]}")
        self.provider = NativeProvider(self.config)

    def complete(self, *, settings=None, cancel=None):
        return self.provider.complete(MODEL, settings or self.config.generation_for("fast_primary").model_dump(),
                                      {"messages": [{"role": "user", "content": "Example request"}],
                                       "schema": {"type": "object", "additionalProperties": True}},
                                      cancel or threading.Event(), time.monotonic() + 3)

    def test_loopback_and_config_binding(self):
        for url in ("https://127.0.0.1:1", "http://192.0.2.1:1", "http://user:secret@localhost:1",
                    "http://localhost:1/path", "http://localhost:bad"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                LlamaCppBackend(url=url, identity=self.config.llama_cpp.identity)
        with self.assertRaises(ValidationError):
            Config(backend="llama-cpp")

    def test_absent_optional_backend_preserves_historical_approval(self):
        runtime = object.__new__(Runtime)
        runtime.config = Config()
        old_config = runtime.config.model_dump()
        del old_config["llama_cpp"]
        bundle = {"config_digest": digest(old_config), "web_research": False}
        self.assertTrue(runtime._config_matches(bundle))
        runtime.config.ollama_url = "http://127.0.0.1:11435"
        self.assertFalse(runtime._config_matches(bundle))
        runtime.config = self.config
        edited = runtime.config.model_dump()
        del edited["llama_cpp"]
        self.assertFalse(runtime._config_matches({"config_digest": digest(edited), "web_research": False}))

    def test_identity_separates_observations_and_operator_proof(self):
        identity = self.provider.identity()
        self.assertEqual(identity["name"], "llama-cpp")
        self.assertIn("server_output_tokens", identity["verification"]["operator_verified"])
        self.assertNotIn("runtime_sha256", identity["verification"]["http"])
        self.assertEqual(identity["launch_attestation_sha256"], "5" * 64)

    def test_important_identity_mismatches_block_before_generation(self):
        changes = [
            {"chat_template": "changed"}, {"model_path": "C:/different.gguf"}, {"model_alias": "wrong"},
            {"build_info": "b2-bbbbbbb"}, {"total_slots": 2}, {"total_slots": True},
            {"is_sleeping": True}, {"endpoint_slots": False},
            {"default_generation_settings": {"n_ctx": 16384, "params": {"n_predict": -1}}},
            {"default_generation_settings": {"n_ctx": 32768, "params": {"n_predict": 100}}},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(LlamaCppError):
                self.server.props_changes = change
                self.complete()
        self.assertEqual(self.server.requests, [])

    def test_render_or_loaded_model_mismatch_blocks(self):
        self.server.changed_render = True
        with self.assertRaises(LlamaCppError):
            self.complete()
        self.server.changed_render = False
        self.server.model = "wrong-model"
        with self.assertRaises(LlamaCppError):
            self.complete()
        self.assertEqual(self.server.requests, [])

    def test_uncapped_sampler_schema_raw_and_cleanup(self):
        raw = self.complete()
        self.assertEqual(raw["choices"][0]["finish_reason"], "stop")
        self.assertEqual(raw["choices"][0]["message"]["content"], ANSWER)
        self.assertTrue(raw["native"]["cleanup_confirmed"])
        self.assertIsNone(raw["native"]["attempts"])
        self.assertIn(b"[DONE]", base64.b64decode(raw["native_transport"]["bytes"]))
        self.assertTrue(raw["native_transport"]["complete"])
        sent = self.server.requests[0]
        self.assertEqual(sent["max_tokens"], -1)
        self.assertEqual({k: sent[k] for k in ("temperature", "top_p", "top_k", "min_p", "repeat_penalty",
                                              "presence_penalty", "frequency_penalty")},
                         {"temperature": 1.0, "top_p": .95, "top_k": 40, "min_p": 0,
                          "repeat_penalty": 1, "presence_penalty": 0, "frequency_penalty": 0})
        self.assertIn("schema", sent["response_format"]["json_schema"])
        self.assertFalse(set(sent) & {"reasoning_effort", "answer_room", "reasoning_close_retry", "strata_mcp", "tools"})

    def test_native_length_is_retained(self):
        self.server.mode = "length"
        raw = self.complete()
        self.assertEqual(raw["choices"][0]["finish_reason"], "length")
        self.assertEqual(raw["native"]["terminal_state"], "length")

    def test_positive_cap_and_output_allowance_are_explicit(self):
        settings = {**self.config.generation_for("fast_primary").model_dump(), "output_tokens": 64}
        self.complete(settings=settings)
        self.assertEqual(self.server.requests[-1]["max_tokens"], 64)
        self.server.input_tokens = 32710
        with self.assertRaises(LlamaCppError):
            self.complete(settings=settings)
        self.assertEqual(len(self.server.requests), 1)
        identity = self.config.llama_cpp.identity.model_dump()
        with self.assertRaises(ValidationError):
            LlamaCppIdentity.model_validate({**identity, "server_output_tokens": 100})

    def test_malformed_final_text_is_preserved_before_schema_validation(self):
        self.server.mode = "malformed_final"
        raw = self.complete()
        self.assertEqual(raw["choices"][0]["message"]["content"], "{not json")
        self.assertEqual(raw["choices"][0]["finish_reason"], "stop")

    def test_thinking_context_and_busy_fail_before_generation(self):
        settings = self.config.generation_for("fast_primary").model_dump()
        for overrides in ({"think": True}, {"think": "high"}, {"context": 16384}):
            with self.subTest(overrides=overrides), self.assertRaises(LlamaCppError):
                self.complete(settings={**settings, **overrides})
        self.server.input_tokens = 32768
        with self.assertRaises(LlamaCppError) as caught:
            self.complete()
        self.assertEqual(caught.exception.diagnostics["backend_error_code"], "context_limit_exceeded")
        self.server.input_tokens = 30
        self.server.processing = True
        with self.assertRaises(LlamaCppError):
            self.complete()
        self.assertEqual(self.server.requests, [])

    def test_cancel_before_dispatch(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(LlamaCppCancelled) as caught:
            self.complete(cancel=cancel)
        self.assertEqual(caught.exception.diagnostics["native_completion"], "not_started")
        self.assertEqual(self.server.requests, [])

    def test_metadata_observation_does_not_admit_identity_or_generate(self):
        self.server.props_changes = {"chat_template": "different-template"}
        observed = self.provider.ollama.observe_identity()
        self.assertEqual(observed["chat_template"], "different-template")
        self.assertEqual(observed["observed"]["template_sha256"], sha("different-template"))
        self.assertEqual(observed["rendered_identity_prompt"], render(IDENTITY_MESSAGES))
        self.assertEqual(observed["model_ids"], [MODEL])
        self.assertEqual(self.server.requests, [])
        with self.assertRaises(LlamaCppError):
            self.provider.ollama.identity()

    def test_shared_reasoning_guard_stops_unsuccessfully_and_preserves_partial(self):
        self.server.mode = "thinking_loop"
        with self.assertRaises(LlamaCppError) as caught:
            self.complete()
        self.assertEqual(caught.exception.diagnostics["failure_category"], "repetition_detected")
        self.assertEqual(caught.exception.diagnostics["native_completion"], "unknown")
        self.assertIn("Repeated example reasoning", caught.exception.raw_response["choices"][0]["message"]["reasoning_content"])
        self.assertEqual(caught.exception.raw_response["choices"][0]["message"]["content"], "")

    def test_transport_failures_preserve_unknown_and_never_retry(self):
        for mode in ("drop_before", "drop_partial", "malformed_stream", "http_error"):
            self.server.mode = mode
            before = len(self.server.requests)
            with self.subTest(mode=mode), self.assertRaises(LlamaCppError) as caught:
                self.complete()
            self.assertEqual(len(self.server.requests), before + 1)
            self.assertEqual(caught.exception.diagnostics["native_completion"], "unknown")
            self.assertTrue(caught.exception.raw_response["native"]["cleanup_confirmed"])
            if mode == "drop_partial":
                self.assertEqual(caught.exception.raw_response["choices"][0]["message"]["content"], ANSWER)
            self.assertFalse(caught.exception.raw_response["native_transport"]["complete"])

    def test_cancel_in_flight_retains_partial_and_cleanup_separately(self):
        self.server.mode = "slow_cancel"
        cancel = threading.Event()

        def stop():
            self.server.generation_started.wait(2)
            time.sleep(.06)
            cancel.set()

        thread = threading.Thread(target=stop)
        thread.start()
        with self.assertRaises(LlamaCppCancelled) as caught:
            self.complete(cancel=cancel)
        thread.join(2)
        self.assertEqual(caught.exception.diagnostics["native_completion"], "unknown")
        self.assertTrue(caught.exception.raw_response["native"]["cancellation_requested"])
        self.assertTrue(caught.exception.raw_response["native"]["cleanup_confirmed"])
        self.assertEqual(len(self.server.requests), 1)

    def test_terminal_without_done_keeps_stream_incomplete(self):
        self.server.mode = "terminal_no_done"
        with self.assertRaises(LlamaCppError) as caught:
            self.complete()
        self.assertEqual(caught.exception.diagnostics["native_completion"], "terminal")
        self.assertFalse(caught.exception.raw_response["native_transport"]["complete"])

    def test_explicit_cancel_probe_after_generation_records_cancelled_terminal_cleanup(self):
        self.server.mode = "slow_cancel"
        cancel = threading.Event()
        stream = self.provider.ollama.stream_chat(
            MODEL, [{"role": "user", "content": "Example request"}],
            {"num_ctx": 32768, "num_predict": -1}, cancel=cancel,
            deadline=time.monotonic() + 3, cancel_cleanup_terminal=True)
        self.assertTrue(next(stream)["message"]["content"])
        cancel.set()
        with self.assertRaises(LlamaCppCancelled) as caught:
            next(stream)
        raw = caught.exception.raw_response
        self.assertEqual(caught.exception.diagnostics["native_completion"], "terminal")
        self.assertEqual(raw["native"]["terminal_state"], "cancelled")
        self.assertTrue(raw["native"]["cleanup_confirmed"])
        self.assertFalse(raw["native_transport"]["complete"])
        self.assertEqual(len(self.server.requests), 1)

    def test_explicit_cancel_probe_without_generation_remains_unknown(self):
        self.server.mode = "drop_before"
        cancel = threading.Event()
        client = self.provider.ollama
        original = client._wait_cleanup

        def cancelled_idle():
            cancel.set()
            return original()

        from unittest.mock import patch
        with patch.object(client, "_wait_cleanup", cancelled_idle):
            with self.assertRaises(LlamaCppError) as caught:
                list(client.stream_chat(MODEL, [{"role": "user", "content": "Example request"}],
                     {"num_ctx": 32768, "num_predict": -1}, cancel=cancel,
                     deadline=time.monotonic() + 3, cancel_cleanup_terminal=True))
        self.assertEqual(caught.exception.diagnostics["native_completion"], "unknown")
        self.assertEqual(caught.exception.raw_response["native"]["terminal_state"], "unknown")

    def test_explicit_cancel_probe_requires_confirmed_slot_cleanup(self):
        self.server.mode = "slow_cancel"
        cancel = threading.Event()
        client = self.provider.ollama
        stream = client.stream_chat(MODEL, [{"role": "user", "content": "Example request"}],
            {"num_ctx": 32768, "num_predict": -1}, cancel=cancel,
            deadline=time.monotonic() + 3, cancel_cleanup_terminal=True)
        next(stream)
        cancel.set()
        from unittest.mock import patch
        with patch.object(client, "_wait_cleanup", return_value=False):
            with self.assertRaises(LlamaCppCancelled) as caught:
                next(stream)
        self.assertEqual(caught.exception.diagnostics["native_completion"], "unknown")
        self.assertFalse(caught.exception.raw_response["native"]["cleanup_confirmed"])

    def test_unconfirmed_cleanup_blocks_terminal_success(self):
        self.server.mode = "busy_after_terminal"
        with self.assertRaises(LlamaCppError) as caught:
            self.complete()
        self.assertFalse(caught.exception.raw_response["native"]["cleanup_confirmed"])
        self.assertEqual(caught.exception.diagnostics["native_completion"], "unknown")

    def test_changed_identity_invalidates_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(Path(directory), self.config)
            try:
                row = runtime.prepare({"prompt": "answer", "mode": "fast"})
                self.server.props_changes = {"chat_template": "changed"}
                with self.assertRaises(LlamaCppError):
                    runtime.approve(row["id"], row["digest"])
                self.assertEqual(self.server.requests, [])
            finally:
                runtime.close()

    def test_operator_launch_attestation_drift_invalidates_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(Path(directory), self.config)
            try:
                row = runtime.prepare({"prompt": "answer", "mode": "fast"})
                self.config.llama_cpp.identity.launch_attestation_sha256 = "6" * 64
                with self.assertRaises(ValueError):
                    runtime.approve(row["id"], row["digest"])
                self.assertEqual(self.server.requests, [])
            finally:
                runtime.close()

    def test_partial_receipt_and_unknown_quarantine_survive_runtime_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(Path(directory), self.config)
            first = runtime.prepare({"prompt": "first", "mode": "fast"})
            runtime.events[first["id"]] = threading.Event()
            runtime.deadline = time.monotonic() + 5
            self.server.mode = "drop_partial"
            with self.assertRaises(LlamaCppError):
                runtime._call({"run_id": first["id"]}, "primary", "fast_primary",
                              {"messages": [{"role": "user", "content": "first"}], "schema": {}})
            saved = json.loads(runtime.store.artifact(first["id"], "primary", "transport").read_text())
            self.assertEqual(saved["choices"][0]["message"]["content"], ANSWER)
            self.assertTrue(runtime.store.has_unknown_calls())
            runtime.close()
            runtime = Runtime(Path(directory), self.config)
            try:
                second = runtime.prepare({"prompt": "second", "mode": "fast"})
                runtime.events[second["id"]] = threading.Event()
                runtime.deadline = time.monotonic() + 5
                with self.assertRaisesRegex(RuntimeError, "native_completion_unknown"):
                    runtime._call({"run_id": second["id"]}, "primary", "fast_primary",
                                  {"messages": [{"role": "user", "content": "second"}], "schema": {}})
                self.assertEqual(len(self.server.requests), 1)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
