"""Compare the installed search adapters on three fixed public documentation questions."""
import argparse
from contextlib import closing
from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.config import Research
from orchestrator.research import create_researcher
from orchestrator.store import Store


PROVIDERS = ("ollama", "brave", "exa")

# Exact query text is shared with the independent hosted-search pilot.
CASES = (
    ("ollama", "What HTTPS endpoint and authentication does Ollama's web search API use?",
     "Official Ollama web-search documentation or announcement.",
     "Does the excerpt identify both the HTTPS endpoint and how the caller authenticates?"),
    ("python", "In Python 3.13, what does asyncio.wait_for do to its awaitable on timeout, and which exception is raised?",
     "Versioned 3.13 asyncio documentation or the asyncio library in the official CPython 3.13 branch.",
     "Does the excerpt show the timeout action and the exception for the requested Python version?"),
    ("sqlite", "In SQLite WAL mode, can readers run at the same time as a writer, and how many writers can run at once?",
     "The SQLite WAL or isolation documentation.",
     "Does the excerpt establish both reader/writer overlap and the limit on simultaneous writers?"),
)


def is_reference(case, url):
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    host = parts.hostname
    if case == "ollama":
        return ((host == "docs.ollama.com" and path == "/capabilities/web-search")
                or (host == "ollama.com" and path == "/blog/web-search"))
    if case == "python":
        return ((host == "docs.python.org" and path.startswith("/3.13/library/asyncio")
                 and path.endswith(".html"))
                or (host == "github.com" and path.startswith(("/python/cpython/tree/3.13/Lib/asyncio/",
                                                              "/python/cpython/blob/3.13/Lib/asyncio/")))
                or (host == "raw.githubusercontent.com" and path.startswith("/python/cpython/3.13/Lib/asyncio/")))
    return host in {"sqlite.org", "www.sqlite.org"} and path in {"/wal.html", "/isolation.html"}


def compare(providers):
    adapters = {name: create_researcher(Research(provider=name, enabled=True, results_per_query=3))
                for name in PROVIDERS}
    readiness = {name: adapter.readiness() for name, adapter in adapters.items()}
    report = {"captured_at_utc": datetime.now(UTC).isoformat(), "selected": providers,
              "readiness": readiness, "max_results": 3, "inference_calls": 0,
              "ranking": "A hit requires a usable excerpt from a listed primary reference in the first three results. "
                         "Rank is the original one-based position within that provider response. "
                         "Check both requested facts in the excerpt manually.",
              "cases": [{"id": name, "query": query, "url_rule": rule, "manual_excerpt_check": check}
                        for name, query, rule, check in CASES],
              "observations": [], "summary": {}}
    with tempfile.TemporaryDirectory() as directory, closing(Store(Path(directory))) as store:
        for i, (case, query, _, _) in enumerate(CASES):
            # Rotate the starting provider across queries to vary request order.
            offset = i % len(providers) if providers else 0
            for name in (providers[offset:] + providers[:offset]):
                observation = {"case": case, "provider": name, "dispatched": False,
                               "reference_rank": None, "results": []}
                report["observations"].append(observation)
                if not readiness[name]["available"]:
                    observation["status"] = readiness[name]["reason"]
                    continue
                run_id = store.create({"provider": name, "query": query})
                start = time.perf_counter()
                result = adapters[name].search([query], store, run_id)
                observation.update(dispatched=True, status=result["status"], errors=result.get("errors", []),
                                   elapsed_seconds=round(time.perf_counter() - start, 3))
                for source in result["sources"]:
                    rank = source["provider_rank"]
                    url = source["url"]
                    excerpt = "\n".join(source["text"].splitlines()[source["excerpt_start_line"] - 1:])
                    reference = is_reference(case, url)
                    observation["results"].append({"rank": rank, "url": url, "title": source["title"],
                                                   "excerpt": excerpt, "matches_reference": reference})
                    if reference and observation["reference_rank"] is None:
                        observation["reference_rank"] = rank
    for name in providers:
        rows = [r for r in report["observations"] if r["provider"] == name and r["dispatched"]]
        report["summary"][name] = {"planned_queries": len(CASES), "dispatched_queries": len(rows),
                                   "successful_searches": sum(r["status"] == "completed" for r in rows),
                                   "reference_page_in_top_three": sum(r["reference_rank"] is not None for r in rows),
                                   "total_elapsed_seconds": round(sum(r["elapsed_seconds"] for r in rows), 3)}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--providers", nargs="+", choices=PROVIDERS, default=[],
                        help="Enable live calls to the selected providers. Omit to inspect the comparison offline.")
    providers = parser.parse_args().providers
    if len(set(providers)) != len(providers):
        parser.error("Select each provider only once")
    print(json.dumps(compare(providers), indent=2))


if __name__ == "__main__":
    main()
