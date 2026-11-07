"""Bounded web search excerpts. Credentials and result URLs never become tools."""

from __future__ import annotations

import html
import json
import os
import re
from datetime import UTC, datetime
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class BraveResearch:
    def __init__(self, config):
        self.config = config

    def available(self):
        return self.config.enabled and bool(os.environ.get("BRAVE_SEARCH_API_KEY"))

    def search(self, queries, store, run_id, guard=None):
        sources, errors = [], []
        for index, query in enumerate(queries):
            if guard:
                guard()
            stage = f"web_search_{index + 1}"
            raw_path = store.artifact(run_id, stage, "raw")
            intent = store.artifact(run_id, stage, "request")
            if raw_path.exists():
                receipt = json.loads(raw_path.read_text())
            elif intent.exists():
                errors.append("search_completion_unknown; not retried")
                continue
            else:
                store.save_artifact(
                    run_id, stage, "request", {"provider": "brave", "query": query}
                )
                try:
                    request = Request(
                        "https://api.search.brave.com/res/v1/web/search?"
                        + urlencode(
                            {
                                "q": query,
                                "count": self.config.results_per_query,
                                "extra_snippets": "true",
                                "text_decorations": "false",
                            }
                        ),
                        headers={
                            "Accept": "application/json",
                            "X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"],
                        },
                    )
                    with build_opener(NoRedirect()).open(
                        request, timeout=self.config.timeout_seconds
                    ) as response:
                        body = response.read(512001)
                    receipt = {
                        "body": body.decode("utf-8") if len(body) <= 512000 else None,
                        "retrieved_at": datetime.now(UTC).isoformat(),
                    }
                except (OSError, URLError, ValueError, KeyError):
                    # Never retain transport exception strings containing credentials/queries.
                    receipt = {"error": "web_search_failed"}
                store.save_artifact(run_id, stage, "raw", receipt)
            try:
                results = json.loads(receipt["body"])["web"]["results"]
                if not isinstance(results, list):
                    raise TypeError("Invalid search results")
                for result in results[: self.config.results_per_query]:
                    url = result["url"]
                    parsed = urlsplit(url)
                    if (
                        parsed.scheme not in {"http", "https"}
                        or not parsed.hostname
                        or parsed.username
                        or parsed.password
                    ):
                        continue
                    # Only search excerpts are consumed; arbitrary result URLs are never fetched.
                    text = "\n".join(
                        [
                            result.get("description", ""),
                            *result.get("extra_snippets", []),
                        ]
                    )
                    text = html.unescape(re.sub(r"<[^>]*>", "", text))[:5000]
                    if not text.strip():
                        continue
                    sources.append(
                        {
                            "id": f"web-{run_id}-{len(sources) + 1}",
                            "kind": "source",
                            "title": str(result.get("title") or url)[:300],
                            "text": f"URL: {url[:2000]}\nRetrieved: {receipt['retrieved_at']}\nSearch excerpts (not a full-page read):\n{text}",
                        }
                    )
            except (ValueError, TypeError, KeyError):
                errors.append("web_search_failed_or_invalid_response")
        return {
            "ok": bool(sources) and not errors,
            "status": "completed"
            if sources and not errors
            else "partial"
            if sources
            else "failed",
            "provider": "brave",
            "queries": queries,
            "sources": sources,
            "errors": errors,
        }
