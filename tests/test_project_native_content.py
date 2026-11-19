"""Pure streamed-frame fixtures; no model, subprocess, HTTP or application."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from orchestrator import project_native_content as module

RID = '11111111-1111-4111-8111-111111111111'
STAGE = 'build_tests_defect_1_file_1'
SCHEMA = {'type': 'json_schema', 'json_schema': {'strict': True, 'schema': {'type': 'object'}}}


def event(content=None, *, reason=None, reasoning=None):
    delta = {}
    if content is not None: delta['content'] = content
    if reasoning is not None: delta['reasoning_content'] = reasoning
    return b'data: ' + module.encode({'choices': [{'index': 0, 'delta': delta,
                                                  'finish_reason': reason}]}) + b'\n\n'


class FakeTransport:
    def __init__(self, directory, events):
        self.directory, self.events, self.closed = Path(directory), events, False
    def _forward(self, handler, body, result, started):
        try:
            for row in self.events: yield row
        finally:
            self.closed = True


class Capture(module.ResponseContentCaptureMixin, FakeTransport):
    pass


class FinalContentTests(unittest.TestCase):
    def capture(self, directory, events, finish='stop'):
        bridge = Capture(directory, events)
        native = {'request_id': RID, 'stage': STAGE,
                  'wire_response_format': {'content_digest': module.digest(SCHEMA)}}
        with patch('subprocess.run', side_effect=AssertionError('No execution')), \
                patch('subprocess.Popen', side_effect=AssertionError('No execution')):
            forwarded = list(bridge._forward(None, {'response_format': SCHEMA}, native, 0))
        self.assertEqual(forwarded, events)
        self.assertTrue(bridge.closed)
        native.update(status='completed', finish_reason=finish, done_event=True)
        return native

    def test_chunk_escape_boundaries_and_unicode_preserve_literal_value(self):
        value = {'changes': [{'replacement_source': 'def f():\n    return "quoted\\path"\r\nπ'}]}
        final = module.encode(value).decode('utf8')
        # Every character is a separate final-content delta, including backslash
        # and its following escape letter; concatenation is the sole operation.
        events = [event(c) for c in final] + [event(reason='stop'), b'data: [DONE]\n\n']
        with TemporaryDirectory() as name:
            native = self.capture(name, events)
            proof = module.validate_native_content(name, native, value)
            self.assertTrue(proof['strict_json_value_equal'])
            self.assertFalse(proof['source_normalized'])
            saved = module.strict_json((Path(name) / native['response_content']['path']).read_bytes())
            self.assertEqual(saved['ordered_content_chunks'], list(final))

    def test_reasoning_unknown_fields_and_headers_are_not_retained(self):
        sentinel = 'PRIVATE_REASONING_OR_CREDENTIAL_SENTINEL'
        events = [event(reasoning=sentinel), b'data: ' + module.encode({'choices': [], 'authorization': sentinel}) + b'\n\n',
                  event('{}'), event(reason='stop'), b'data: [DONE]\n\n']
        with TemporaryDirectory() as name:
            native = self.capture(name, events)
            module.validate_native_content(name, native, {})
            self.assertNotIn(sentinel, (Path(name) / native['response_content']['path']).read_text())
            self.assertNotIn(sentinel, json.dumps(native))

    def test_nonterminal_stream_is_saved_as_incomplete_and_rejected(self):
        with TemporaryDirectory() as name:
            bridge = Capture(name, [event('{}'), event(reason='stop')])
            result = {'request_id': RID, 'stage': STAGE}
            with self.assertRaisesRegex(module.ContentCaptureError, 'without DONE'):
                list(bridge._forward(None, {'response_format': SCHEMA}, result, 0))
            self.assertTrue(bridge.closed)
            saved = module.strict_json((Path(name) / result['response_content']['path']).read_bytes())
            self.assertFalse(saved['transport_complete']); self.assertFalse(saved['normal_stop'])
            self.assertEqual(saved['error_type'], 'ContentCaptureError')
            with self.assertRaises(module.ContentCaptureError):
                module.validate_native_content(name, result, {})

    def test_length_terminal_retains_content_without_normal_completion_credit(self):
        with TemporaryDirectory() as name:
            native = self.capture(name, [event('{}'), event(reason='length'), b'data: [DONE]\n\n'], 'length')
            self.assertTrue(native['response_content']['transport_complete'])
            self.assertFalse(native['response_content']['normal_stop'])
            with self.assertRaises(module.ContentCaptureError): module.validate_native_content(name, native, {})

    def test_missing_finish_and_post_done_content_are_rejected(self):
        capture = module.OrderedFinalContent(RID, STAGE, module.digest(SCHEMA))
        with self.assertRaisesRegex(module.ContentCaptureError, 'without a terminal'):
            capture.observe(b'data: [DONE]\n\n')
        capture.observe(event('{}', reason='stop')); capture.observe(b'data: [DONE]\n\n')
        with self.assertRaisesRegex(module.ContentCaptureError, 'followed terminal'):
            capture.observe(event('extra'))

    def test_overflow_stops_before_forwarding_the_overflowing_chunk(self):
        with TemporaryDirectory() as name, patch.object(module, 'MAX_CONTENT_UTF8_BYTES', 2):
            bridge = Capture(name, [event('{}'), event('extra'), event(reason='stop'), b'data: [DONE]\n\n'])
            result = {'request_id': RID, 'stage': STAGE}
            iterator = bridge._forward(None, {'response_format': SCHEMA}, result, 0)
            self.assertEqual(next(iterator), event('{}'))
            with self.assertRaisesRegex(module.ContentCaptureError, 'bounded capture'):
                next(iterator)
            self.assertTrue(bridge.closed)
            saved = module.strict_json((Path(name) / result['response_content']['path']).read_bytes())
            self.assertEqual(saved['ordered_content_chunks'], ['{}'])
            self.assertFalse(saved['transport_complete'])

    def test_parsed_value_mismatch_and_bool_number_alias_are_rejected(self):
        with TemporaryDirectory() as name:
            native = self.capture(name, [event('{"value":true}'), event(reason='stop'), b'data: [DONE]\n\n'])
            for wrong in ({'value': 1}, {'value': False}, {'other': True}):
                with self.subTest(wrong=wrong), self.assertRaises(module.ContentCaptureError):
                    module.validate_native_content(name, native, wrong)

    def test_duplicate_keys_are_never_salvaged(self):
        with TemporaryDirectory() as name:
            native = self.capture(name, [event('{"value":1,"value":2}'), event(reason='stop'), b'data: [DONE]\n\n'])
            with self.assertRaisesRegex(module.ContentCaptureError, 'Duplicate'):
                module.validate_native_content(name, native, {'value': 2})

    def test_resealed_order_and_identity_tampering_rejects(self):
        for kind in ('order', 'stage', 'schema', 'count_boolean'):
            with self.subTest(kind=kind), TemporaryDirectory() as name:
                native = self.capture(name, [event('{'), event('"a":1}'), event(reason='stop'), b'data: [DONE]\n\n'])
                path = Path(name) / native['response_content']['path']
                saved = module.strict_json(path.read_bytes())
                if kind == 'order': saved['ordered_content_chunks'].reverse()
                elif kind == 'stage': saved['stage'] = 'build_tests_defect_2_file_1'
                elif kind == 'schema': saved['wire_schema_digest'] = '0' * 64
                else: saved['chunk_count'] = True
                raw = module.encode(saved) + b'\n'; path.write_bytes(raw)
                native['response_content']['sha256'] = module.sha256(raw).hexdigest()
                with self.assertRaises(module.ContentCaptureError): module.validate_native_content(name, native, {'a': 1})

    def test_request_path_and_transport_status_mismatch_reject(self):
        with TemporaryDirectory() as name:
            original = self.capture(name, [event('{}'), event(reason='stop'), b'data: [DONE]\n\n'])
            for key, value in [('status', 'transport_incomplete'), ('request_id', '22222222-2222-4222-8222-222222222222'), ('done_event', False)]:
                native = deepcopy(original); native[key] = value
                with self.subTest(key=key), self.assertRaises(module.ContentCaptureError):
                    module.validate_native_content(name, native, {})


if __name__ == '__main__': unittest.main()
