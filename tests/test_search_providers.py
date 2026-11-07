import io
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from orchestrator.config import Research
from orchestrator.research import create_researcher
from orchestrator.store import Store


class SearchProviderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name))
        self.addCleanup(self.store.close)
        self.rid = str(uuid.uuid4())
        self.open = self.enterContext(patch("orchestrator.research.build_opener")).return_value.open
        self.enterContext(patch.dict(os.environ, {
            "BRAVE_SEARCH_API_KEY": "brave-test-secret", "OLLAMA_API_KEY": "ollama-test-secret"}))

    def adapter(self, provider):
        return create_researcher(Research(enabled=True, provider=provider, results_per_query=2))

    def response(self, data):
        return io.BytesIO(json.dumps(data).encode("utf-8"))

    def test_ollama_posts_only_query_and_limit_and_replays_locally(self):
        search = self.adapter("ollama")
        self.open.return_value = self.response({"results": [
            {"url": "https://example.org/doc", "title": "A guide", "content": "café\n" * 1800},
            {"url": "javascript:alert(1)", "content": "ignore"}]})
        report = search.search(["public question"], self.store, self.rid)
        req = self.open.call_args.args[0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.full_url, "https://ollama.com/api/web_search")
        self.assertEqual(json.loads(req.data), {"query": "public question", "max_results": 2})
        self.assertEqual(req.get_header("Authorization"), "Bearer ollama-test-secret")
        self.assertEqual(len(report["sources"]), 1)
        source = report["sources"][0]
        self.assertEqual(source["url"], "https://example.org/doc")
        self.assertEqual(source["excerpt_start_line"], 4)
        self.assertLessEqual(len(source["text"].splitlines()[3].encode()), 1200)
        self.assertNotIn("ollama-test-secret", self.store.artifact(self.rid, "web_search_1", "request").read_text())
        self.assertEqual(search.search(["public question"], self.store, self.rid), report)
        self.assertEqual(self.adapter("brave").search(["public question"], self.store, self.rid)["errors"],
                         ["search_request_mismatch"])
        self.assertEqual(search.search(["changed question"], self.store, self.rid)["errors"], ["search_request_mismatch"])
        self.assertEqual(self.open.call_count, 1)

    def test_brave_keeps_legacy_format(self):
        self.assertEqual(Research().provider, "brave")
        self.open.return_value = self.response({"web": {"results": [
            {"title": "Guide", "url": "https://example.org", "description": "<b>Hello</b>", "extra_snippets": ["world"]}]}})
        result = self.adapter("brave").search(["how & why"], self.store, self.rid)
        req = self.open.call_args.args[0]
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(parse_qs(urlsplit(req.full_url).query)["q"], ["how & why"])
        self.assertEqual(req.get_header("X-subscription-token"), "brave-test-secret")
        self.assertNotIn("Authorization", req.headers)
        self.assertTrue(result["ok"])
        self.assertTrue(result["sources"][0]["text"].endswith("Hello\nworld"))

    def test_unknown_dispatch_preserves_intent(self):
        self.store.save_artifact(self.rid, "web_search_1", "request", {"provider": "ollama", "query": "public"})
        result = self.adapter("ollama").search(["public"], self.store, self.rid)
        self.assertFalse(result["ok"])
        self.assertIn("unknown", result["errors"][0])
        self.open.assert_not_called()

    def test_access_denied_or_throttled_is_retained_without_credentials(self):
        for code, reason in ((401, "provider_access_denied"), (429, "provider_rate_limited")):
            run_id = str(uuid.uuid4())
            self.open.side_effect = HTTPError("https://ollama.com/", code, "sensitive provider error", {}, None)
            report = self.adapter("ollama").search(["public"], self.store, run_id)
            self.assertEqual(report["errors"], [reason])
            self.assertEqual(json.loads(self.store.artifact(run_id, "web_search_1", "raw").read_bytes()), {"error": reason})
        self.assertEqual(self.open.call_count, 2)

    def test_readiness_only_considers_the_selected_provider(self):
        os.environ.pop("OLLAMA_API_KEY")
        self.assertEqual(self.adapter("ollama").readiness(), {"available":False,"provider":"ollama","reason":"missing_api_key"})
        self.assertTrue(self.adapter("brave").available())
        self.assertEqual(create_researcher(Research(provider="ollama")).readiness()["reason"], "disabled")
        self.open.assert_not_called()
