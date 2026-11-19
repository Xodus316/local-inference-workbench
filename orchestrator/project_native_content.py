"""Bounded, ordered final-content receipts for an opt-in structured SSE bridge.

Place ResponseContentCaptureMixin before the bridge implementation, then call
validate_native_content on its normal native receipt and literal parsed value.
The proof establishes transport equality only. The caller must still validate
that value against the original response model and all scope/semantic guards.
Reasoning and headers are never copied into these content receipts.
"""
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import re

POLICY = 'ordered-native-final-content-v1'
MAX_CONTENT_UTF8_BYTES = 2 * 1024 * 1024
MAX_CONTENT_CHUNKS = 65536
MAX_EVENT_BYTES = 8 * 1024 * 1024
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
FINISH_REASONS = {'stop', 'length', 'tool_calls', 'content_filter', 'function_call'}


class ContentCaptureError(ValueError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf8')


def digest(value): return sha256(encode(value)).hexdigest()


def strict_json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result: raise ContentCaptureError('Duplicate final-content JSON key')
            result[key] = item
        return result
    def constant(_): raise ContentCaptureError('Non-finite final-content JSON value')
    try:
        result = json.loads(value, object_pairs_hook=pairs, parse_constant=constant)
        encode(result)
        return result
    except (UnicodeError, RecursionError, json.JSONDecodeError) as error:
        raise ContentCaptureError('Final content is not strict UTF8 JSON') from error


class OrderedFinalContent:
    def __init__(self, request_id, stage, wire_schema_digest):
        if (not isinstance(request_id, str) or re.fullmatch(r'[a-f0-9-]{36}', request_id) is None
                or not isinstance(stage, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', stage) is None
                or not isinstance(wire_schema_digest, str)
                or re.fullmatch(r'[a-f0-9]{64}', wire_schema_digest) is None):
            raise ContentCaptureError('Require exact request, stage and native schema bindings')
        self.request_id, self.stage, self.wire_schema_digest = request_id, stage, wire_schema_digest
        self.chunks = []
        self.utf8_bytes = 0
        self.finish_reason = None
        self.done = False

    def observe(self, event):
        """Read one already bounded SSE event and retain only delta.content."""
        if self.done: raise ContentCaptureError('Native content followed terminal DONE')
        if not isinstance(event, bytes) or len(event) > MAX_EVENT_BYTES:
            raise ContentCaptureError('Native content event exceeds its bounded byte limit')
        lines = event.splitlines()
        data = b'\n'.join(line[5:].removeprefix(b' ') for line in lines if line.startswith(b'data:'))
        if not data: return
        if data == b'[DONE]':
            if self.finish_reason is None:
                raise ContentCaptureError('Native content reached DONE without a terminal choice')
            self.done = True
            return
        frame = strict_json(data)
        if not isinstance(frame, dict): raise ContentCaptureError('Malformed native content frame')
        choices = frame.get('choices')
        if choices == []: return  # Usage-only frame; no content or reasoning is retained.
        if (not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict)
                or type(choices[0].get('index')) is not int or choices[0]['index'] != 0):
            raise ContentCaptureError('Native content requires exactly choice zero')
        choice = choices[0]
        delta = choice.get('delta')
        if not isinstance(delta, dict) or self.finish_reason is not None:
            raise ContentCaptureError('Extra or malformed native content choice')
        content = delta.get('content')
        if content is not None:
            if not isinstance(content, str): raise ContentCaptureError('Native final content is not text')
            try: size = len(content.encode('utf8'))
            except UnicodeError as error: raise ContentCaptureError('Native final content is not UTF8') from error
            if (len(self.chunks) >= MAX_CONTENT_CHUNKS or self.utf8_bytes + size > MAX_CONTENT_UTF8_BYTES):
                raise ContentCaptureError('Native final content exceeds its bounded capture limit')
            self.chunks.append(content)
            self.utf8_bytes += size
        reason = choice.get('finish_reason')
        if reason is not None:
            if not isinstance(reason, str) or reason not in FINISH_REASONS:
                raise ContentCaptureError('Unknown native content finish reason')
            self.finish_reason = reason

    def record(self, *, error_type=None):
        text = ''.join(self.chunks)
        return {'policy': POLICY, 'request_id': self.request_id, 'stage': self.stage,
            'wire_schema_digest': self.wire_schema_digest, 'ordered_content_chunks': list(self.chunks),
            'chunks_digest': digest(self.chunks), 'content_sha256': sha256(text.encode('utf8')).hexdigest(),
            'content_utf8_bytes': self.utf8_bytes, 'chunk_count': len(self.chunks),
            'content_byte_limit': MAX_CONTENT_UTF8_BYTES, 'chunk_limit': MAX_CONTENT_CHUNKS,
            'finish_reason': self.finish_reason, 'done_event': self.done,
            'transport_complete': self.done, 'normal_stop': self.done and self.finish_reason == 'stop',
            'error_type': error_type, 'reasoning_retained': False, 'headers_retained': False,
            'source_normalized': False, 'native_value_acceptance_credit': False}


def _write(directory, capture, error_type=None):
    value = capture.record(error_type=error_type)
    content = encode(value) + b'\n'
    if len(content) > MAX_RECEIPT_BYTES:
        raise ContentCaptureError('Native content receipt exceeds its bounded storage limit')
    folder = Path(directory) / 'response-content'
    folder.mkdir(exist_ok=True)
    path = folder / (capture.request_id + '.json')
    with path.open('xb') as stream:
        stream.write(content); stream.flush(); os.fsync(stream.fileno())
    return {'policy': POLICY, 'path': 'response-content/' + path.name,
        'sha256': sha256(content).hexdigest(), 'content_sha256': value['content_sha256'],
        'chunks_digest': value['chunks_digest'], 'content_utf8_bytes': value['content_utf8_bytes'],
        'chunk_count': value['chunk_count'], 'transport_complete': value['transport_complete'],
        'normal_stop': value['normal_stop']}


class ResponseContentCaptureMixin:
    def _forward(self, handler, body, result, started):
        capture = OrderedFinalContent(result['request_id'], result['stage'], digest(body['response_format']))
        saved = False
        try:
            with closing(super()._forward(handler, body, result, started)) as events:
                for event in events:
                    capture.observe(event)
                    if capture.done:
                        result['response_content'] = _write(self.directory, capture)
                        saved = True
                    yield event  # Exact existing bytes: no rewrite or escaping operation.
            if not capture.done:
                raise ContentCaptureError('Native content stream ended without DONE')
        except BaseException as error:
            if not saved:
                result['response_content'] = _write(self.directory, capture, type(error).__name__)
            raise


def validate_native_content(directory, native_receipt, returned_value):
    """Prove strict final JSON equals the returned raw value, without coercion."""
    binding = native_receipt.get('response_content')
    request_id, stage = native_receipt.get('request_id'), native_receipt.get('stage')
    if (not isinstance(binding, dict) or binding.get('policy') != POLICY
            or native_receipt.get('status') != 'completed' or native_receipt.get('finish_reason') != 'stop'
            or native_receipt.get('done_event') is not True
            or binding.get('path') != f'response-content/{request_id}.json'):
        raise ContentCaptureError('Require a completed normal request and its exact content receipt')
    root = Path(directory).resolve()
    candidate = root / binding['path']
    path = candidate.resolve(strict=True)
    if (not path.is_relative_to(root) or candidate.is_symlink() or candidate.parent.is_symlink()
            or path.stat().st_size > MAX_RECEIPT_BYTES):
        raise ContentCaptureError('Native content receipt escaped its bounded owned directory')
    content = path.read_bytes()
    if sha256(content).hexdigest() != binding.get('sha256'):
        raise ContentCaptureError('Native content receipt hash changed')
    value = strict_json(content)
    capture = OrderedFinalContent(request_id, stage, native_receipt['wire_response_format']['content_digest'])
    chunks = value.get('ordered_content_chunks') if isinstance(value, dict) else None
    if (not isinstance(chunks, list) or len(chunks) > MAX_CONTENT_CHUNKS
            or any(not isinstance(chunk, str) for chunk in chunks)):
        raise ContentCaptureError('Malformed ordered final-content chunks')
    capture.chunks = list(chunks)
    capture.utf8_bytes = sum(len(chunk.encode('utf8')) for chunk in chunks)
    if capture.utf8_bytes > MAX_CONTENT_UTF8_BYTES:
        raise ContentCaptureError('Recorded final content exceeds its byte bound')
    capture.finish_reason = 'stop'; capture.done = True
    if encode(value) != encode(capture.record()):
        raise ContentCaptureError('Native content state, counts, order or identity changed')
    expected = {'policy': POLICY, 'path': binding['path'], 'sha256': sha256(content).hexdigest(),
        **{key: value[key] for key in ('content_sha256', 'chunks_digest', 'content_utf8_bytes',
                                     'chunk_count', 'transport_complete', 'normal_stop')}}
    if encode(binding) != encode(expected): raise ContentCaptureError('Native content metadata differs from its exact receipt')
    parsed = strict_json(''.join(chunks))
    if not isinstance(parsed, dict) or encode(parsed) != encode(returned_value):
        raise ContentCaptureError('Parsed native final content differs from the returned raw value')
    return {'policy': POLICY, 'request_id': request_id, 'stage': stage,
        'content_file_sha256': binding['sha256'], 'content_sha256': value['content_sha256'],
        'native_value_digest': digest(parsed), 'strict_json_value_equal': True,
        'reasoning_retained': False, 'source_normalized': False}
