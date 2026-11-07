"""Exercise Exa's keyless protocol and search receipts with an in-memory HTTPS peer."""
import io
import json
import tempfile
import unittest
import uuid
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from orchestrator.exa_search import ExaResearch, _MAX_BYTES
from orchestrator.store import Store


class Response(io.BytesIO):
    def __init__(self, body=b"", *, mime="application/json", status=200, session=None):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = mime
        if session:
            self.headers["Mcp-Session-Id"] = session

    def getheader(self, name):
        return self.headers.get(name)


def rpc(message_id, result, *, sse=False, session=None):
    content = json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}, ensure_ascii=False).encode()
    if sse:
        return Response(b": heartbeat\r\n\r\nevent: message\r\ndata: " + content + b"\r\n\r\n",
                        mime="text/event-stream", session=session)
    return Response(content, session=session)


def records():
    return "\n\n---\n\n".join([
        f"Title: Page {i}\nURL: https://example.org/{i}\nPublished: N/A\nAuthor: N/A\nHighlights:\nCafé {i}."
        for i in (1, 2)])


class ExaSearchTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = Store(Path(folder.name))
        self.addCleanup(self.store.close)
        self.rid = str(uuid.uuid4())
        self.searcher = ExaResearch(SimpleNamespace(enabled=True, timeout_seconds=5, results_per_query=2))
        self.connection = self.enterContext(patch("orchestrator.exa_search.HTTPSConnection"))
        self.connections = []
        self.remote = []
        self.connection.side_effect = self.connect

    def connect(self, host, timeout):
        self.assertEqual(host, "mcp.exa.ai")
        self.assertTrue(0 < timeout <= 5)
        peer = Mock()
        peer.getresponse.return_value = self.remote.pop(0)
        self.connections.append(peer)
        return peer

    def exchange(self, final):
        schema = {"properties": {name: {} for name in ("query", "numResults", "objective")},
                  "required": ["query", "objective"]}
        return [rpc(1, {"protocolVersion": "2025-06-18"}, session="scoped-token", sse=True),
                Response(status=202), rpc(2, {"tools": [{"name": "web_search_exa", "inputSchema": schema}]}),
                final]

    def fetch(self):
        return self.searcher.search(["public question"], self.store, self.rid)

    def test_mcp_handshake_ranked_excerpts_and_replay_with_no_key(self):
        self.remote = self.exchange(rpc(3, {"content": [{"type": "text", "text": records()}]}, sse=True))
        first = self.fetch()
        self.assertTrue(first["ok"], first)
        self.assertEqual([s["url"] for s in first["sources"]], ["https://example.org/1", "https://example.org/2"])
        self.assertTrue(first["sources"][0]["text"].endswith("Café 1."))
        requests = [c.request.call_args for c in self.connections]
        sent = [json.loads(c.kwargs["body"]) for c in requests]
        self.assertEqual([b["method"] for b in sent], ["initialize", "notifications/initialized", "tools/list", "tools/call"])
        for i, call in enumerate(requests):
            self.assertEqual(call.args, ("POST", "/mcp"))
            self.assertNotIn("Authorization", call.kwargs["headers"])
            if i:
                self.assertEqual(call.kwargs["headers"]["MCP-Protocol-Version"], "2025-06-18")
                self.assertEqual(call.kwargs["headers"]["Mcp-Session-Id"], "scoped-token")
        self.assertEqual(sent[-1]["params"]["name"], "web_search_exa")
        self.assertEqual(sent[-1]["params"]["arguments"]["numResults"], 2)
        self.assertEqual(sent[-1]["params"]["arguments"]["query"], "public question")
        self.assertTrue(sent[-1]["params"]["arguments"]["objective"])
        self.assertEqual(first, self.fetch())
        self.assertEqual(len(self.connections), 4)
        self.assertNotIn("scoped-token", self.store.artifact(self.rid, "web_search_1", "raw").read_text(encoding="utf-8"))

    def test_error_responses_do_not_fetch_links_or_retry(self):
        for final, expected in [
            (Response(status=429), "provider_rate_limited"),
            (Response(status=302), "web_search_failed"),
            (rpc(3, {"isError": True, "content": [{"type": "text", "text": "tool rejected request"}]}), "provider_tool_error"),
            (rpc(1, {"content": []}), "invalid_provider_response"),
            (Response(b"x" * (_MAX_BYTES + 1)), "response_too_large"),
            (rpc(3, {"content": [{"type": "text", "text": "No results found."}]}), "no_usable_results"),
            (rpc(3, {"content": [{"type": "text", "text": "provider changed format"}]}), "invalid_provider_response"),
        ]:
            with self.subTest(expected=expected):
                self.rid = str(uuid.uuid4())
                self.remote = self.exchange(final)
                result = self.fetch()
                self.assertFalse(result["ok"])
                self.assertEqual(result["errors"], [expected])
                count = len(self.connections)
                self.assertEqual(self.fetch(), result)
                self.assertEqual(len(self.connections), count)

    def test_version_and_tool_are_negotiated_before_search(self):
        self.remote = [rpc(1, {"protocolVersion": "2030-01-01"})]
        self.assertEqual(self.fetch()["errors"], ["unsupported_search_protocol"])
        self.assertEqual(len(self.connections), 1)
        self.rid = str(uuid.uuid4())
        self.remote = self.exchange(None)[:2] + [rpc(2, {"tools": [{"name": "another_tool"}]})]
        self.assertEqual(self.fetch()["errors"], ["search_tool_unavailable"])
        self.assertEqual(len(self.connections), 4)

    def test_unresolved_intent_is_never_replayed(self):
        self.store.save_artifact(self.rid, "web_search_1", "request", {"provider": "exa", "query": "public question"})
        self.assertIn("unknown", self.fetch()["errors"][0])
        self.assertEqual(self.connections, [])
