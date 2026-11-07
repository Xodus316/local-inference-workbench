import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "compare-search.py"
spec = importlib.util.spec_from_file_location("compare_search", SCRIPT)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class ComparisonTests(unittest.TestCase):
    def test_offline_default_and_skipped_credentials(self):
        with patch.dict("os.environ", {"OLLAMA_API_KEY": "", "BRAVE_SEARCH_API_KEY": ""}), patch(
            "orchestrator.research.build_opener", side_effect=AssertionError("Unexpected HTTP")
        ):
            self.assertFalse(bench.compare([])["observations"])
            report = bench.compare(["ollama", "brave"])
            self.assertEqual(len(report["observations"]), 6)
            self.assertTrue(all(r["status"] == "missing_api_key" and not r["dispatched"]
                                for r in report["observations"]))

    def test_rotating_order_native_ranks_and_excerpt_evidence(self):
        class Provider:
            def readiness(self):
                return {"available": True, "reason": None}

            def search(self, queries, store, run):
                urls = {bench.CASES[0][1]: "https://docs.ollama.com/capabilities/web-search",
                        bench.CASES[1][1]: "https://docs.python.org/3.13/library/asyncio.html",
                        bench.CASES[2][1]: "https://sqlite.org/wal.html"}
                return {"status": "completed", "sources": [{"url": urls[queries[0]], "title": "Example",
                        "provider_rank": 2, "text": "metadata\nExcerpt.", "excerpt_start_line": 2}]}

        with patch.object(bench, "create_researcher", return_value=Provider()):
            report = bench.compare(["ollama", "brave", "exa"])
        rows = report["observations"]
        self.assertEqual([r["provider"] for r in rows],
                         ["ollama","brave","exa", "brave","exa","ollama", "exa","ollama","brave"])
        self.assertEqual([r["reference_rank"] for r in rows], [2]*9)
        self.assertEqual(rows[0]["results"][0]["excerpt"], "Excerpt.")
        self.assertEqual(report["summary"]["exa"]["reference_page_in_top_three"], 3)
        self.assertFalse(bench.is_reference("python", "https://docs.python.org/3/library/asyncio.html"))
        self.assertFalse(bench.is_reference("sqlite", "https://sqlite.org.invalid/wal.html"))
