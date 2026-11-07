"""Bounded internet searches with durable receipts and server-side credentials."""

from __future__ import annotations

import html
import json
import os
import re
from datetime import UTC, datetime
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WebResearch:
    def __init__(self, config):
        self.config = config

    def readiness(self):
        reason = None
        if not self.config.enabled:
            reason = "disabled"
        elif not os.environ.get(self.key_variable, "").strip():
            reason = "missing_api_key"
        return {"provider": self.name, "available": reason is None, "reason": reason}

    def available(self):
        return self.readiness()["available"]

    def _fetch(self, query):
        key = os.environ.get(self.key_variable, "").strip()
        if not self.config.enabled or not key:
            return {"error": "disabled" if not self.config.enabled else "missing_api_key"}
        request = self.request(query, key)
        try:
            with build_opener(NoRedirect()).open(request, timeout=self.config.timeout_seconds) as response:
                data = response.read(512001)
            if len(data) > 512000:
                return {"error": "response_too_large"}
            return {"body": data.decode("utf-8"), "retrieved_at": datetime.now(UTC).isoformat()}
        except HTTPError as error:
            status = error.code
            error.close()
            if status in (401, 403):
                return {"error": "provider_access_denied"}
            if status == 429:
                return {"error": "provider_rate_limited"}
            return {"error": "web_search_failed"}
        except (OSError, ValueError):
            return {"error": "web_search_failed"}

    def _receipt(self, store, run_id, stage, query):
        intent = {"provider": self.name, "query": query}
        saved_request = store.artifact(run_id, stage, "request")
        saved_response = store.artifact(run_id, stage, "raw")
        if saved_request.exists():
            if json.loads(saved_request.read_bytes()) != intent:
                return {"error": "search_request_mismatch"}
            if saved_response.exists():
                return json.loads(saved_response.read_bytes())
            return {"error": "search_completion_unknown; not retried"}
        if saved_response.exists():
            return {"error": "search_request_mismatch"}
        store.save_artifact(run_id, stage, "request", intent)
        receipt = self._fetch(query)
        store.save_artifact(run_id, stage, "raw", receipt)
        return receipt

    def search(self, queries, store, run_id, guard=None):
        sources, errors = [], []
        for index, query in enumerate(queries):
            if guard:
                guard()
            receipt = self._receipt(store, run_id, f"web_search_{index + 1}", query)
            if receipt.get("error"):
                errors.append(receipt["error"])
                continue
            try:
                results = self.results(json.loads(receipt["body"]))
                retrieved = receipt["retrieved_at"]
                if not isinstance(results, list):
                    raise ValueError("Invalid provider results")
            except (TypeError, ValueError, KeyError):
                errors.append("invalid_provider_response")
                continue
            before = len(sources)
            for provider_rank, result in enumerate(results[:self.config.results_per_query], 1):
                if not isinstance(result, dict):
                    continue
                url = result.get("url")
                if not isinstance(url, str) or len(url) > 2000 or any(c.isspace() or ord(c) < 32 for c in url):
                    continue
                try:
                    parsed = urlsplit(url)
                    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
                        continue
                except ValueError:
                    continue
                snippet = self.excerpt(result).strip()[:5000]
                if snippet:
                    sources.append({
                        "id": f"web-{run_id}-{len(sources) + 1}", "kind": "source",
                        "title": str(result.get("title") or url)[:300], "url": url, "excerpt_start_line": 4,
                        "provider_rank": provider_rank,
                        "text": f"URL: {url}\nRetrieved: {retrieved}\nSearch excerpts (not a full-page read):\n{snippet}",
                    })
            if len(sources) == before:
                errors.append("no_usable_results")
        return {"ok": bool(sources) and not errors,
                "status": ("partial" if errors else "completed") if sources else "failed",
                "provider": self.name, "queries": queries, "sources": sources, "errors": errors}


class OllamaResearch(WebResearch):
    name = "ollama"
    key_variable = "OLLAMA_API_KEY"

    def request(self, query, key):
        body = json.dumps({"query": query, "max_results": self.config.results_per_query}).encode("utf-8")
        return Request("https://ollama.com/api/web_search", data=body, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {key}"})

    @staticmethod
    def results(body):
        return body["results"]

    @staticmethod
    def excerpt(result):
        text = result.get("content")
        if not isinstance(text, str):
            return ""
        # The model sees a bounded excerpt. The original response remains in the receipt.
        snippet = " ".join(text.split())
        return snippet.encode("utf-8")[:1200].decode("utf-8", errors="ignore")


class BraveResearch(WebResearch):
    name = "brave"
    key_variable = "BRAVE_SEARCH_API_KEY"

    def request(self, query, key):
        params = urlencode({"q": query, "count": self.config.results_per_query,
                            "extra_snippets": "true", "text_decorations": "false"})
        return Request("https://api.search.brave.com/res/v1/web/search?" + params,
                       headers={"Accept": "application/json", "X-Subscription-Token": key})

    @staticmethod
    def results(body):
        return body["web"]["results"]

    @staticmethod
    def excerpt(result):
        extras = result.get("extra_snippets", [])
        parts = [result.get("description"), *(extras if isinstance(extras, list) else [])]
        text = "\n".join(s for s in parts if isinstance(s, str))
        return html.unescape(re.sub(r"<[^>]*>", "", text))


def create_researcher(config):
    if config.provider == "exa":
        from .exa_search import ExaResearch
        return ExaResearch(config)
    providers = {"brave": BraveResearch, "ollama": OllamaResearch}
    return providers[config.provider](config)
