"""Exercise approved Internet Research with confident readers and controlled search results."""
import json
import tempfile
import time
import unittest
from unittest.mock import patch
from io import BytesIO
from pathlib import Path

from orchestrator.config import Config, Research
from orchestrator.ollama import _context_window
from orchestrator.research import create_researcher
from orchestrator.runtime import Runtime
from orchestrator.store import digest


class ConfidentReaders:
    def __init__(self):
        self.requests = []
        self.citation = "web-example:L4"
        self.fail_reader = False
        self.queries = ["example official release date"]

    def admit(self, model, settings, payload, stage):
        _context_window(payload["messages"], settings["output_tokens"] or 1, settings["context"])

    def complete(self, model, settings, payload, cancel, deadline):
        body = json.loads(payload["messages"][1]["content"])
        self.requests.append((model, body))
        if self.fail_reader:
            return {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
        if "queries" in payload["schema"]["properties"]:
            content = {"queries": self.queries}
        else:
            evidence = [self.citation] if self.citation and "web_research" in body else []
            content = {"answer": "The release is on June 4.", "key_claims": [], "evidence": evidence,
                       "assumptions": [], "uncertainties": [], "verification_needed": []}
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}]}


class SearchFixture:
    def __init__(self):
        self.queries = []
        self.sources = [{"id": "web-example", "kind": "source", "title": "Release notes",
                         "url": "https://example.org/releases", "excerpt_start_line": 4,
                         "text": "URL: https://example.org/releases\nRetrieved: 2026-06-04\nExcerpts:\nThe release is on June 4."}]

    def available(self):
        return True

    def search(self, queries, store, run_id, guard):
        guard()
        self.queries.extend(queries)
        return {"provider": "fixture", "status": "completed" if self.sources else "failed",
                "ok": bool(self.sources), "sources": self.sources, "queries": queries}


class InternetResearchTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.provider = ConfidentReaders()
        self.runtime = Runtime(Path(directory.name), Config(), self.provider)
        self.addCleanup(self.runtime.close)

    def prepare(self, mode="research", **options):
        return self.runtime.prepare({"mode": mode, "prompt": "When is the release?",
                                     "sources": [{"id": "note", "text": "Unverified estimate"}], **options})

    def execute(self, prepared):
        self.runtime.approve(prepared["id"], prepared["digest"])
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            row = self.runtime.store.get(prepared["id"])
            if row["status"] not in {"queued", "running"}:
                return row
            time.sleep(.01)
        self.fail("Research did not finish")

    def test_explicit_research_searches_for_confident_readers_and_cites_actual_excerpt(self):
        search = self.runtime.researcher = SearchFixture()
        prepared = self.prepare()
        self.assertTrue(prepared["bundle"]["web_research"])
        self.assertEqual(self.provider.requests, [])
        self.assertEqual(search.queries, [])
        run = self.execute(prepared)
        self.assertEqual(run["status"], "completed", run.get("error"))
        self.assertEqual([call["stage"] for call in run["calls"]],
                         ["primary", "secondary", "research_queries", "synthesis"])
        self.assertEqual(search.queries, ["example official release date"])
        self.assertEqual(self.provider.requests[2][0], self.runtime.config.roles["fast_secondary"])
        stages = run["result"]["stages"]
        self.assertEqual(stages["research"]["sources"][0]["url"], "https://example.org/releases")
        self.assertEqual(run["result"]["output"]["evidence"][0]["quote"], "The release is on June 4.")
        # Reopening the research stage uses the saved provider and model receipts.
        self.assertEqual(self.runtime.research({"run_id": run["id"]}), stages["research"])
        self.assertEqual(len(self.provider.requests), 4)
        self.assertEqual(len(search.queries), 1)

    def test_first_fetch_ollama_partial_results_retain_receipts_and_grounding(self):
        self.runtime.config = Config(research=Research(enabled=True, provider="ollama"))
        self.runtime.researcher = create_researcher(self.runtime.config.research)
        self.provider.queries = ["official release", "release calendar"]
        with patch.dict("os.environ", {"OLLAMA_API_KEY": "example"}), patch(
            "orchestrator.research.build_opener"
        ) as opener:
            prepared = self.prepare()
            self.provider.citation = f"web-{prepared['id']}-1:L4"
            results = [{"title": "Calendar", "url": "https://example.org/releases",
                        "content": "Release day is June 4."}]
            opener.return_value.open.side_effect = [
                BytesIO(json.dumps({"results": results}).encode()), BytesIO(b'{"results":[]}')]
            row = self.execute(prepared)
            self.assertEqual(row["status"], "degraded", row)
            report = row["result"]["stages"]["research"]
            self.assertEqual(report["status"], "partial")
            self.assertEqual(report["provider"], "ollama")
            self.assertEqual(row["result"]["output"]["evidence"][0]["quote"], "Release day is June 4.")
            self.assertEqual(report["sources"][0]["url"], results[0]["url"])
            for i, query in enumerate(self.provider.queries, 1):
                req = opener.return_value.open.call_args_list[i-1].args[0]
                self.assertEqual(req.full_url, "https://ollama.com/api/web_search")
                self.assertEqual(json.loads(req.data)["query"], query)
                path = self.runtime.store.artifact(row["id"], f"web_search_{i}", "request")
                self.assertEqual(json.loads(path.read_bytes()), {"provider": "ollama", "query": query})
            self.assertEqual(self.runtime.research({"run_id": row["id"]}), report)
            self.assertEqual(opener.return_value.open.call_count, 2)

    def test_two_full_result_sets_fit_the_32k_synthesis_window(self):
        self.runtime.config = Config(research=Research(enabled=True, provider="ollama", results_per_query=5))
        self.runtime.researcher = create_researcher(self.runtime.config.research)
        self.provider.queries = ["release bulletin", "release notes"]
        content = "Updates 東京.\n" * 1100
        hits = [{"title": "Release notes", "url": f"https://example.org/release/{index}",
                 "content": content} for index in range(5)]
        with patch.dict("os.environ", {"OLLAMA_API_KEY": "example"}), patch.object(
            self.runtime.researcher, "_fetch", return_value={
                "retrieved_at": "2026-06-04T00:00:00Z", "body": json.dumps({"results": hits})}
        ):
            prepared = self.prepare()
            self.provider.citation = f"web-{prepared['id']}-1:L4"
            row = self.execute(prepared)
        self.assertEqual(row["status"], "completed", row.get("error"))
        sources = row["result"]["stages"]["research"]["sources"]
        self.assertEqual(len(sources), 10)
        for item in sources:
            excerpt = item["text"].splitlines()[3:]
            self.assertEqual(len(excerpt), 1)
            self.assertLessEqual(len(excerpt[0].encode("utf-8")), 1200)

    def test_invalid_reader_returns_no_unverified_answer(self):
        search = self.runtime.researcher = SearchFixture()
        self.provider.fail_reader = True
        row = self.execute(self.prepare())
        self.assertEqual(row["status"], "failed")
        self.assertIsNone(row["result"]["output"])
        self.assertEqual(search.queries, [])
        self.assertEqual(len(self.provider.requests), 1)

    def test_optional_balanced_still_skips_search_on_confident_readers(self):
        search = self.runtime.researcher = SearchFixture()
        run = self.execute(self.prepare("balanced", research=True))
        self.assertEqual(run["status"], "completed")
        self.assertEqual([call["stage"] for call in run["calls"]], ["primary", "secondary", "synthesis"])
        self.assertEqual(search.queries, [])

    def test_research_without_usable_excerpts_ends_before_synthesis(self):
        search = self.runtime.researcher = SearchFixture()
        search.sources = []
        run = self.execute(self.prepare())
        self.assertEqual(run["status"], "degraded")
        self.assertIsNone(run["result"]["output"])
        self.assertIn("no usable excerpts", run["result"]["error"])
        self.assertEqual(len(self.provider.requests), 3)

    def test_research_requires_excerpt_evidence_and_preserves_receipt(self):
        self.runtime.researcher = SearchFixture()
        for ref in ("", "note:L1", "web-example:L1", "web-example:L2"):
            with self.subTest(ref=ref):
                self.provider.citation = ref
                run = self.execute(self.prepare())
                self.assertEqual(run["status"], "degraded")
                self.assertIsNone(run["result"]["output"])
                self.assertIn("cited no retrieved excerpt", run["result"]["error"])
                self.assertTrue(run["result"]["stages"]["research"]["sources"])

    def test_unavailable_search_never_prepares_or_dispatches_explicit_research(self):
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(self.runtime.store.list(), [])
        self.assertEqual(self.provider.requests, [])

    def test_configured_ollama_search_with_missing_key_cannot_start_explicit_run(self):
        self.runtime.config = Config(research=Research(enabled=True, provider="ollama"))
        self.runtime.researcher = create_researcher(self.runtime.config.research)
        with patch.dict("os.environ", {"OLLAMA_API_KEY": ""}):
            self.assertEqual(self.runtime.search_readiness(),
                             {"available": False, "provider": "ollama", "reason": "missing_api_key"})
            with self.assertRaisesRegex(ValueError, "missing_api_key"):
                self.prepare()
        self.assertEqual(self.runtime.store.list(), [])
        self.assertEqual(self.provider.requests, [])

    def test_prior_config_digest_preserves_old_brave_grant(self):
        current = Config(research=Research(enabled=True, provider="brave"))
        self.runtime.config = current
        previous = current.model_dump()
        del previous["research"]["provider"]
        old = {"mode": "balanced", "web_research": True, "config_digest": digest(previous)}
        self.assertTrue(self.runtime._config_matches(old))
        self.runtime.config = Config(research=Research(enabled=True, provider="ollama"))
        self.assertFalse(self.runtime._config_matches(old))
