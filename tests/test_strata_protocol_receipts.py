"""Offline request-bound Strata counting and cancellation contract tests."""

import base64
import copy
import hashlib
import json
import threading
import unittest
from unittest.mock import patch

from orchestrator.config import Config, StrataBackend, StrataIdentity
from orchestrator.provider import NativeProvider
from orchestrator.strata import StrataCancelled, StrataClient, StrataError


MODEL = "local-model"
TEMPLATE = "pinned-template"
MODEL_PATH = "/models/pinned/model.gguf"
TOKEN_POLICY = "strata-openai-native-token-count-v1"
CLEANUP_POLICY = "strata-serial-stop-done-v1"
REQUEST_ID = "29bc6920-f91c-498a-824b-a12c7ef27bc5"
OWNER = {"server_instance_id": "a" * 32, "engine_pid": 123, "engine_generation": 1}
OPTIONS = {"num_ctx": 32768, "num_predict": -1, "temperature": 1.0, "top_p": .95, "top_k": 20}
MESSAGES = [{"role": "user", "content": "Explain λ."}]
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}}


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def request_digest(body):
    return digest(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))


def configuration(**switches):
    return StrataBackend(url="http://127.0.0.1:18080", identity=StrataIdentity(
        model=MODEL, engine="0.1.58", context=32768, runtime_sha256="1" * 64,
        model_manifest_sha256="2" * 64, template_sha256=digest(TEMPLATE),
        model_path_sha256=digest(MODEL_PATH), config_sha256="3" * 64,
        memory_mode="resident_complement", native_attempts_observable=False,
        approximation_switches=switches,
    ))


def cancellation_receipt(body):
    return {"policy": CLEANUP_POLICY, "request_id": REQUEST_ID, **OWNER, "model": MODEL,
            "request_sha256": request_digest(body), "server_native_request_id": "b" * 12,
            "native_attempts": 1, "generation_observed": True, "created_monotonic": 9.0,
            "first_native_token_monotonic": 10.0, "cancellation_requested": True,
            "cancellation_requested_monotonic": 11.0, "stop_sent": True, "stop_sent_monotonic": 12.0,
            "native_done_ack": True, "native_done_reason": "cancel", "native_done_monotonic": 13.0,
            "native_completion": "terminal", "native_terminal_state": "cancelled", "cleanup_confirmed": True,
            "handler_finished": True, "admission_released": True, "handler_finished_monotonic": 14.0,
            "cleanup_elapsed_seconds": 3.0, "cleanup_deadline_seconds": 30, "state": "cancelled",
            "failure_category": None}


class JsonResponse:
    def __init__(self, value):
        self.data = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return self.data[:limit]


class FakeSocket:
    def settimeout(self, timeout):
        pass

    def shutdown(self, how):
        pass

    def close(self):
        pass


class FakeStream:
    status = 200

    def __init__(self, api):
        self.api = api
        frames = [{"choices": [{"delta": {"content": "partial λ", "reasoning_content": "reasoning"},
                                "finish_reason": None}]}]
        for _ in range(api.reasoning_frames):
            frames.append({"id": "chatcmpl-" + "d" * 24, "object": "chat.completion.chunk",
                           "created": 1, "model": MODEL, "choices": [{"index": 0,
                               "delta": {"reasoning_content": "r"}, "finish_reason": None}]})
        if api.mode == "normal":
            frames.append({"choices": [{"delta": {}, "finish_reason": api.finish_reason}],
                           "strata_native_attempts": 1,
                           "usage": {"prompt_tokens": 17, "completion_tokens": 23 + api.reasoning_frames}})
        self.lines = [b"data: " + json.dumps(frame).encode() + b"\n\n" for frame in frames]
        if api.mode == "normal":
            self.lines.append(b"data: [DONE]\n\n")

    def readline(self, limit):
        if self.lines:
            return self.lines.pop(0)
        if self.api.mode == "cancel":
            self.api.cancel.set()
        if self.api.mode == "timeout":
            raise TimeoutError("Synthetic streaming read timeout")
        return b""

    def close(self):
        self.api.stream_closed = True


class FakeConnection:
    def __init__(self, api):
        self.api = api
        self.sock = FakeSocket()

    def connect(self):
        pass

    def request(self, method, path, body, headers):
        self.api.posts.append((path, json.loads(body), headers))

    def getresponse(self):
        return FakeStream(self.api)

    def close(self):
        self.api.connection_closed = True


class FakeAPI:
    def __init__(self):
        self.posts, self.gets = [], []
        self.cancel = threading.Event()
        self.mode = "cancel"
        self.reasoning_frames = 0
        self.finish_reason = "stop"
        self.stream_closed = self.connection_closed = False
        self.status = {"service": "strata", "model": MODEL, "loaded": True, "engine": "0.1.58",
                       "context": {"native": 32768}, "concurrency": {"serving": 1},
                       "structured_output": {"method": "prompt_and_validate"},
                       "native_protocol_token_count": TOKEN_POLICY, "native_cleanup_policy": CLEANUP_POLICY,
                       "native_protocol_owner": copy.deepcopy(OWNER), "protocol_quarantined": False,
                       "protocol_request_id_required": True}
        self.edit_count = lambda value: value
        self.edit_receipt = lambda value: value
        self.poll_status = None
        self.receipt_reads = 0

    def open(self, request, timeout):
        path = request.full_url.removeprefix("http://127.0.0.1:18080")
        if request.get_method() == "POST":
            body = json.loads(request.data)
            self.posts.append((path, body, dict(request.header_items())))
            if path != "/v1/chat/completions/count_tokens":
                raise AssertionError("Counting must never call generation")
            return JsonResponse(self.edit_count({"policy": TOKEN_POLICY, "model": MODEL, "input_tokens": 17,
                                                "request_sha256": request_digest(body), "token_ids_sha256": "c" * 64,
                                                "native_dispatches": 0, "prefix_cache_written": False}))
        self.gets.append(path)
        if path == "/v1/status":
            status = self.poll_status if self.stream_closed and self.poll_status is not None else self.status
            return JsonResponse(status)
        if path == "/props":
            return JsonResponse({"chat_template": TEMPLATE, "model_path": MODEL_PATH})
        if path == "/v1/models":
            return JsonResponse({"data": [{"id": MODEL}]})
        if path == "/v1/protocol/requests/" + self.posts[-1][2].get("X-Strata-Protocol-Request-ID", REQUEST_ID):
            assert self.stream_closed and self.connection_closed, "Poll only after closing the stream"
            self.receipt_reads += 1
            value = cancellation_receipt(self.posts[-1][1])
            value["request_id"] = self.posts[-1][2]["X-Strata-Protocol-Request-ID"]
            if self.mode == "normal":
                value.update(native_done_reason=self.finish_reason, native_terminal_state=self.finish_reason,
                             state=self.finish_reason,
                             cancellation_requested=False, cancellation_requested_monotonic=None,
                             stop_sent=False, stop_sent_monotonic=None, cleanup_elapsed_seconds=None)
            return JsonResponse(self.edit_receipt(value))
        raise AssertionError("Unexpected metadata path: " + path)


class StrataProtocolTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.addCleanup(patch.stopall)
        patch("orchestrator.strata.urllib.request.build_opener", return_value=self.api).start()
        patch("orchestrator.strata.http.client.HTTPConnection",
              side_effect=lambda *args, **kwargs: FakeConnection(self.api)).start()
        self.client = StrataClient(configuration(native_protocol_token_count=TOKEN_POLICY,
                                                 native_cleanup_policy=CLEANUP_POLICY))

    def cancelled(self, client=None, request_id=REQUEST_ID):
        self.api.cancel.clear()
        self.api.stream_closed = self.api.connection_closed = False
        self.api.mode = "cancel"
        with self.assertRaises(StrataCancelled) as caught:
            list((client or self.client).stream_chat(MODEL, MESSAGES, OPTIONS, think="high", cancel=self.api.cancel,
                                                    format_schema=SCHEMA, protocol_request_id=request_id))
        return caught.exception

    def test_count_only_posts_count_and_uses_identical_ordinary_body(self):
        count = self.client.count_tokens(MODEL, MESSAGES, OPTIONS, think="high", format_schema=SCHEMA)
        self.assertEqual([row[0] for row in self.api.posts], ["/v1/chat/completions/count_tokens"])
        self.assertEqual(count["input_tokens"], 17)
        self.api.mode = "normal"
        list(self.client.stream_chat(MODEL, MESSAGES, OPTIONS, think="high", format_schema=SCHEMA))
        counted, generated = (row[1] for row in self.api.posts)
        self.assertEqual(counted, generated)
        self.assertEqual(count["request_sha256"], request_digest(generated))
        self.assertEqual(counted["reasoning_effort"], "high")
        self.assertEqual(counted["reasoning_budget_tokens"], 0)
        self.assertIn("JSON Schema", counted["messages"][0]["content"])
        self.assertNotIn("max_tokens", counted)
        self.assertNotIn("response_format", counted)
        self.assertTrue(counted["stream"])
        self.assertIsNone(counted["tools"])

    def test_count_response_must_bind_request_and_prove_no_generation(self):
        invalid = {"policy": "other", "model": "other", "input_tokens": True, "request_sha256": "0" * 64,
                   "token_ids_sha256": "invalid", "native_dispatches": True, "prefix_cache_written": 0}
        for key, item in invalid.items():
            with self.subTest(key=key):
                self.api.edit_count = lambda value, key=key, item=item: {**value, key: item}
                with self.assertRaises(StrataError):
                    self.client.count_tokens(MODEL, MESSAGES, OPTIONS)
        self.assertTrue(all(row[0].endswith("/count_tokens") for row in self.api.posts))

    def test_opt_in_policies_are_frozen_in_observed_identity(self):
        identity = self.client.identity()
        self.assertEqual(identity["observed"]["native_protocol_token_count"], TOKEN_POLICY)
        self.assertEqual(identity["observed"]["native_cleanup_policy"], CLEANUP_POLICY)
        self.assertEqual(identity["observed"]["native_protocol_owner"], OWNER)
        self.assertIs(identity["observed"]["protocol_quarantined"], False)
        for key, value in (("native_protocol_token_count", None), ("native_cleanup_policy", "other"),
                           ("protocol_quarantined", 0), ("native_protocol_owner", {**OWNER, "engine_pid": True})):
            original = self.api.status[key]
            self.api.status[key] = value
            with self.subTest(key=key), self.assertRaises(StrataError):
                self.client.identity()
            self.api.status[key] = original
        self.assertEqual(self.api.posts, [])

    def test_normal_completion_requires_bound_receipt_and_retains_automatic_uuid(self):
        self.api.mode = "normal"
        for reason in ("stop", "length"):
            self.api.finish_reason = reason
            frames = list(self.client.stream_chat(MODEL, MESSAGES, OPTIONS))
            final = frames[-1]
            request = final["strata_native_protocol_request"]
            self.assertEqual(request["request_id"], self.api.posts[-1][2]["X-Strata-Protocol-Request-ID"])
            self.assertEqual(final["strata_native_protocol"]["request_id"], request["request_id"])
            self.assertEqual(final["strata_native_attempts"], 1)
            self.assertEqual(final["done_reason"], reason)
        self.api.finish_reason = "stop"
        self.api.edit_receipt = lambda value: {**value, "native_done_ack": False}
        with self.assertRaises(StrataError) as caught:
            list(self.client.stream_chat(MODEL, MESSAGES, OPTIONS))
        self.assertEqual(caught.exception.diagnostics["native_completion"], "unknown")
        self.assertTrue(caught.exception.raw_response["native_transport"]["complete"])
        self.assertEqual(caught.exception.raw_response["choices"][0]["finish_reason"], "stop")

    def test_transport_eof_checks_cleanup_but_preserves_failure_and_partial_output(self):
        self.api.mode = "eof"
        with self.assertRaises(StrataError) as caught:
            list(self.client.stream_chat(MODEL, MESSAGES, OPTIONS))
        self.assertNotIsInstance(caught.exception, StrataCancelled)
        self.assertIn("ended before a terminal frame", str(caught.exception))
        self.assertEqual(caught.exception.diagnostics["failure_category"], "invalid_response")
        self.assertEqual(caught.exception.diagnostics["native_completion"], "terminal")
        self.assertTrue(caught.exception.diagnostics["cleanup_confirmed"])
        self.assertFalse(caught.exception.diagnostics["automatic_retry"])
        self.assertEqual(self.api.receipt_reads, 1)
        self.assertFalse(self.api.cancel.is_set())
        self.assertEqual(len(self.api.posts), 1)
        raw = caught.exception.raw_response
        self.assertEqual(raw["choices"][0]["message"]["content"], "partial λ")
        self.assertEqual(raw["native_protocol"]["native_done_reason"], "cancel")
        self.assertFalse(raw["native_transport"]["complete"])

    def test_transport_failure_with_forged_or_unconfirmed_cleanup_stays_unknown(self):
        self.api.mode = "eof"
        for change in ({"request_sha256": "0" * 64}, {"native_done_ack": False},
                       {"admission_released": False}):
            with self.subTest(change=change):
                self.api.edit_receipt = lambda value, change=change: {**value, **change}
                before = len(self.api.posts)
                with self.assertRaises(StrataError) as caught:
                    list(self.client.stream_chat(MODEL, MESSAGES, OPTIONS))
                self.assertEqual(caught.exception.diagnostics["native_completion"], "unknown")
                self.assertFalse(caught.exception.diagnostics["cleanup_confirmed"])
                self.assertFalse(caught.exception.diagnostics["automatic_retry"])
                self.assertFalse(caught.exception.raw_response["native_transport"]["complete"])
                self.assertEqual(len(self.api.posts), before + 1)

    def test_stream_timeout_checks_cleanup_without_replaying_or_returning_partial_success(self):
        self.api.mode = "timeout"
        with self.assertRaises(StrataError) as caught:
            list(self.client.stream_chat(MODEL, MESSAGES, OPTIONS))
        self.assertIn("streaming request failed", str(caught.exception))
        self.assertEqual(caught.exception.diagnostics["native_completion"], "terminal")
        self.assertTrue(caught.exception.diagnostics["cleanup_confirmed"])
        self.assertFalse(caught.exception.diagnostics["automatic_retry"])
        self.assertFalse(caught.exception.raw_response["native_transport"]["complete"])
        self.assertEqual(self.api.receipt_reads, 1)
        self.assertEqual(len(self.api.posts), 1)

    def test_transport_failure_without_cleanup_opt_in_does_not_poll(self):
        self.api.mode = "eof"
        client = StrataClient(configuration())
        with self.assertRaises(StrataError) as caught:
            list(client.stream_chat(MODEL, MESSAGES, OPTIONS))
        self.assertEqual(caught.exception.diagnostics["native_completion"], "unknown")
        self.assertEqual(self.api.receipt_reads, 0)
        self.assertFalse(any(path.startswith("/v1/protocol/") for path in self.api.gets))

    def test_normal_stream_over_four_mb_retains_small_decoded_output_and_terminal_proof(self):
        self.api.mode = "normal"
        self.api.reasoning_frames = 24_000
        provider = NativeProvider(Config(backend="strata", strata=self.client.config))
        result = provider.complete(MODEL, {"context": 32768, "output_tokens": None, "think": "high"},
            {"messages": MESSAGES, "schema": SCHEMA}, threading.Event(), None,
            protocol_request_id=REQUEST_ID)
        raw_wire = base64.b64decode(result["native_transport"]["bytes"])
        self.assertGreater(len(raw_wire), 4_000_000)
        self.assertLessEqual(len(raw_wire), 32_000_000)
        self.assertLess(result["usage"]["completion_tokens"] + result["usage"]["prompt_tokens"], 32768)
        self.assertEqual(result["choices"][0]["message"]["reasoning_content"], "reasoning" + "r" * 24_000)
        self.assertTrue(result["native_transport"]["complete"])
        self.assertTrue(result["native"]["cleanup_confirmed"])
        self.assertEqual(len(self.api.posts), 1)

    def test_stream_wire_bound_preserves_error_and_bounded_transport_after_cleanup(self):
        self.api.mode = "normal"
        with patch("orchestrator.strata._MAX_STREAM_RESPONSE", 200), self.assertRaises(StrataError) as caught:
            list(self.client.stream_chat(MODEL, MESSAGES, OPTIONS))
        self.assertIn("transport exceeded its response bound", str(caught.exception))
        self.assertEqual(caught.exception.diagnostics["native_completion"], "terminal")
        self.assertTrue(caught.exception.diagnostics["cleanup_confirmed"])
        self.assertFalse(caught.exception.diagnostics["automatic_retry"])
        self.assertEqual(len(base64.b64decode(caught.exception.raw_response["native_transport"]["bytes"])), 200)
        self.assertFalse(caught.exception.raw_response["native_transport"]["complete"])
        self.assertEqual(len(self.api.posts), 1)

    def test_normal_stop_drain_retains_terminal_proof_without_claiming_cancellation(self):
        self.api.mode = "normal"
        self.api.edit_receipt = lambda value: {**value, "stop_sent": True,
            "stop_sent_monotonic": 12.0, "cleanup_elapsed_seconds": 2.0}
        provider = NativeProvider(Config(backend="strata", strata=self.client.config))
        for reason in ("stop", "length"):
            with self.subTest(reason=reason):
                self.api.finish_reason = reason
                result = provider.complete(MODEL, {"context": 32768, "output_tokens": None, "think": "high"},
                    {"messages": MESSAGES, "schema": SCHEMA}, threading.Event(), None,
                    protocol_request_id=REQUEST_ID)
                self.assertEqual(result["native_protocol"]["native_done_reason"], reason)
                self.assertTrue(result["native_protocol"]["stop_sent"])
                self.assertEqual(result["native_protocol"]["cleanup_elapsed_seconds"], 2.0)
                self.assertTrue(result["native"]["cleanup_confirmed"])
                self.assertFalse(result["native"]["cancellation_requested"])
                self.assertFalse(result["native"]["cancellation_exercised"])
                self.assertTrue(result["native_transport"]["complete"])
        self.assertEqual(len(self.api.posts), 2)

    def test_normal_stop_drain_rejects_invalid_order_elapsed_and_missing_terminal_proof(self):
        self.api.mode = "normal"
        invalid = ({"stop_sent_monotonic": 9.5, "cleanup_elapsed_seconds": 4.5},
                   {"stop_sent_monotonic": 13.5, "cleanup_elapsed_seconds": .5},
                   {"cleanup_elapsed_seconds": None}, {"cleanup_elapsed_seconds": True},
                   {"cleanup_elapsed_seconds": 1.9},
                   {"handler_finished_monotonic": 43.0, "cleanup_elapsed_seconds": 31.0},
                   {"native_done_ack": False}, {"admission_released": False},
                   {"request_sha256": "0" * 64})
        for change in invalid:
            with self.subTest(change=change):
                self.api.edit_receipt = lambda value, change=change: {
                    **value, "stop_sent": True, "stop_sent_monotonic": 12.0,
                    "cleanup_elapsed_seconds": 2.0, **change}
                before = len(self.api.posts)
                with self.assertRaises(StrataError) as caught:
                    list(self.client.stream_chat(MODEL, MESSAGES, OPTIONS))
                self.assertEqual(caught.exception.diagnostics["native_completion"], "unknown")
                self.assertFalse(caught.exception.diagnostics["cleanup_confirmed"])
                self.assertEqual(len(self.api.posts), before + 1)

    def test_provider_passes_caller_uuid_and_retains_server_proof(self):
        self.api.mode = "normal"
        provider = NativeProvider(Config(backend="strata", strata=self.client.config))
        result = provider.complete(MODEL, {"context": 32768, "output_tokens": None, "think": "high"},
                                   {"messages": MESSAGES, "schema": SCHEMA}, threading.Event(), None,
                                   protocol_request_id=REQUEST_ID)
        self.assertEqual(result["native_protocol"]["request_id"], REQUEST_ID)
        self.assertEqual(result["native_protocol_request"]["request_id"], REQUEST_ID)
        self.assertEqual(self.api.posts[-1][2]["X-Strata-Protocol-Request-ID"], REQUEST_ID)
        self.assertEqual(result["native"], {
            "backend": "strata", "attempts": 1, "terminal_state": "stop",
            "cleanup_confirmed": True, "native_done_reason": "stop",
            "cancellation_exercised": False, "cancellation_requested": False,
            "handler_finished": True, "admission_released": True,
        })

    def test_known_cancel_preserves_partial_transport_and_actual_done_reason(self):
        error = self.cancelled()
        self.assertEqual(error.diagnostics["native_completion"], "terminal")
        self.assertTrue(error.diagnostics["cleanup_confirmed"])
        self.assertTrue(error.diagnostics["cancellation_exercised"])
        self.assertEqual(error.diagnostics["native_done_reason"], "cancel")
        self.assertEqual(error.diagnostics["native_attempts"], 1)
        self.assertFalse(error.diagnostics["automatic_retry"])
        receipt = error.raw_response
        self.assertEqual(receipt["choices"][0]["finish_reason"], "cancel")
        self.assertEqual(receipt["native"]["terminal_state"], "cancelled")
        self.assertEqual(receipt["choices"][0]["message"]["content"], "partial λ")
        self.assertEqual(receipt["choices"][0]["message"]["reasoning_content"], "reasoning")
        self.assertFalse(receipt["native_transport"]["complete"])
        self.assertIn(b'partial', base64.b64decode(receipt["native_transport"]["bytes"]))
        self.assertEqual(self.api.posts[0][2]["X-Strata-Protocol-Request-ID"], REQUEST_ID)
        self.assertEqual(len(self.api.posts), 1)

    def test_natural_stop_race_is_known_but_cancellation_is_unexercised(self):
        def natural(value):
            return {**value, "native_done_reason": "stop", "native_terminal_state": "stop", "state": "stop",
                    "cancellation_requested_monotonic": 13.5, "stop_sent": False, "stop_sent_monotonic": None,
                    "cleanup_elapsed_seconds": .5}
        self.api.edit_receipt = natural
        error = self.cancelled()
        self.assertEqual(error.diagnostics["native_completion"], "terminal")
        self.assertEqual(error.diagnostics["native_done_reason"], "stop")
        self.assertFalse(error.diagnostics["cancellation_exercised"])
        self.assertEqual(error.raw_response["native"]["terminal_state"], "stop")
        self.assertEqual(error.raw_response["choices"][0]["finish_reason"], "stop")
        self.assertFalse(error.raw_response["native_transport"]["complete"])

    def test_done_ack_before_handler_release_is_provisional(self):
        def pending(value):
            if self.api.receipt_reads == 1:
                return {**value, "state": "native_done", "cleanup_confirmed": False,
                        "handler_finished": False, "admission_released": False,
                        "handler_finished_monotonic": None, "cleanup_elapsed_seconds": None}
            return value
        self.api.edit_receipt = pending
        error = self.cancelled()
        self.assertEqual(self.api.receipt_reads, 2)
        self.assertEqual(error.diagnostics["native_completion"], "terminal")

    def test_wrong_binding_missing_ack_invalid_time_and_types_fail_closed(self):
        invalid = {"request_id": "63c861a9-7514-43ef-bd98-f2e928ba6117", "request_sha256": "0" * 64,
                   "model": "other", "server_instance_id": "d" * 32, "engine_pid": True,
                   "engine_generation": 2, "server_native_request_id": 123, "native_attempts": True,
                   "generation_observed": False, "native_done_ack": False, "native_done_reason": "nonfinite",
                   "native_completion": "unknown", "native_terminal_state": "error", "cleanup_confirmed": False,
                   "handler_finished": False, "admission_released": False, "cancellation_requested": 1,
                   "stop_sent": False, "first_native_token_monotonic": 11.5, "stop_sent_monotonic": 13.5,
                   "native_done_monotonic": 15.0, "handler_finished_monotonic": -1,
                   "cleanup_elapsed_seconds": 31, "cleanup_deadline_seconds": True, "failure_category": "native_error"}
        for key, item in invalid.items():
            with self.subTest(key=key):
                self.api.edit_receipt = lambda value, key=key, item=item: {**value, key: item}
                before = len(self.api.posts)
                error = self.cancelled()
                self.assertEqual(error.diagnostics["native_completion"], "unknown")
                self.assertFalse(error.diagnostics["cleanup_confirmed"])
                self.assertFalse(error.raw_response["native_transport"]["complete"])
                self.assertEqual(len(self.api.posts), before + 1)

    def test_missing_receipt_observability_fails_closed(self):
        required = set(cancellation_receipt({})) - {"created_monotonic"}
        for key in required:
            with self.subTest(key=key):
                self.api.edit_receipt = lambda value, key=key: {name: item for name, item in value.items() if name != key}
                error = self.cancelled()
                self.assertEqual(error.diagnostics["native_completion"], "unknown")

    def test_owner_drift_or_quarantine_never_promotes_old_receipt(self):
        for update in ({"native_protocol_owner": {**OWNER, "engine_generation": 2}}, {"protocol_quarantined": True}):
            self.api.poll_status = {**self.api.status, **update}
            with self.subTest(update=update):
                error = self.cancelled()
                self.assertEqual(error.diagnostics["native_completion"], "unknown")
        self.assertEqual(self.api.receipt_reads, 0)

    def test_owner_fields_cannot_override_request_binding(self):
        # The reviewed server emits only these three owner fields. In
        # particular, owner metadata must never overwrite the call's UUID or
        # digest when the receipt's expected binding is assembled.
        for key, item in (("request_id", "63c861a9-7514-43ef-bd98-f2e928ba6117"),
                          ("request_sha256", "0" * 64), ("policy", "other"),
                          ("model", "other")):
            owner = {**OWNER, key: item}
            self.api.status["native_protocol_owner"] = owner
            with self.subTest(key=key), self.assertRaises(StrataError):
                self.client.identity()
            protocol = {"request_id": REQUEST_ID, "request_sha256": request_digest({}), "owner": owner}
            forged = {**cancellation_receipt({}), key: item}
            self.assertFalse(self.client._confirmed_protocol_receipt(forged, protocol, MODEL))
        self.assertEqual(self.api.posts, [])

    def test_default_does_not_send_request_header_or_poll_receipts(self):
        client = StrataClient(configuration())
        identity = client.identity()
        self.assertNotIn("native_cleanup_policy", identity["observed"])
        with self.assertRaises(StrataError):
            client.count_tokens(MODEL, MESSAGES, OPTIONS)
        error = self.cancelled(client)
        self.assertEqual(error.diagnostics["native_completion"], "unknown")
        self.assertNotIn("X-Strata-Protocol-Request-ID", self.api.posts[-1][2])
        self.assertEqual(self.api.receipt_reads, 0)

    def test_request_id_must_be_canonical_and_invalid_json_is_not_dispatched(self):
        for request_id in ("invalid", REQUEST_ID.upper(), "{" + REQUEST_ID + "}"):
            with self.subTest(request_id=request_id), self.assertRaises(StrataError):
                list(self.client.stream_chat(MODEL, MESSAGES, OPTIONS, protocol_request_id=request_id))
        with self.assertRaises(StrataError):
            self.client.count_tokens(MODEL, MESSAGES, {**OPTIONS, "temperature": float("nan")})
        self.assertEqual(self.api.posts, [])

    def test_readonly_polling_has_a_thirty_second_deadline(self):
        clock = [0.0]
        timeouts = []
        def unavailable(path, *, timeout):
            timeouts.append(timeout)
            clock[0] += min(timeout, .5)
            raise StrataError("Unavailable")
        def advance(seconds):
            clock[0] += seconds
        protocol = {"request_id": REQUEST_ID, "request_sha256": "a" * 64, "owner": OWNER}
        with patch.object(self.client, "_json", side_effect=unavailable), \
                patch("orchestrator.strata.time.monotonic", side_effect=lambda: clock[0]), \
                patch("orchestrator.strata.time.sleep", side_effect=advance):
            self.assertEqual(self.client._wait_protocol_receipt(protocol, MODEL), (None, False))
        self.assertLessEqual(clock[0], 30.0)
        self.assertGreater(len(timeouts), 1)
        self.assertEqual(self.api.posts, [])


if __name__ == "__main__":
    unittest.main()
