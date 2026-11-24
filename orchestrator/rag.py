"""Retrieve local document passages from a dedicated Solr core."""
import http.client
import json
import re
from urllib.parse import urlsplit

from .schemas import Source

EMBEDDING_MODEL = "embeddinggemma"
VECTOR_DIMENSIONS = 768
CORPUS_ID = "workbench-corpus"
CHUNK_CHARS = 1600
SNAPSHOT_ID = re.compile(r"[0-9a-f]{64}")


def _post_json(url, path, payload, timeout, service):
    endpoint = urlsplit(url)
    connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=timeout)
    try:
        connection.request(
            "POST", path, json.dumps(payload, allow_nan=False).encode("utf-8"),
            {"Content-Type": "application/json", "Accept": "application/json"},
        )
        with connection.getresponse() as response:
            if response.status != 200:
                raise ValueError(f"{service} returned HTTP {response.status}")
            body = response.read(1_000_001)
        if len(body) > 1_000_000:
            raise ValueError(f"{service} returned an oversized response")
        result = json.loads(body)
    except (OSError, http.client.HTTPException):
        raise ValueError(f"Could not reach {service} at {url}") from None
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError(f"{service} returned invalid JSON") from None
    finally:
        connection.close()
    if not isinstance(result, dict):
        raise ValueError(f"{service} returned an invalid response")
    return result


def embed(ollama_url, texts, timeout, *, keep_alive=0, truncate=False):
    try:
        result = _post_json(
            ollama_url, "/api/embed",
            {"model": EMBEDDING_MODEL, "input": texts, "truncate": truncate, "keep_alive": keep_alive},
            timeout, "Ollama embeddings",
        )
    except ValueError as exc:
        raise ValueError(f"{exc}. Check Ollama and run 'ollama pull {EMBEDDING_MODEL}'") from None
    vectors = result.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != len(texts) or any(
        not isinstance(vector, list) or len(vector) != VECTOR_DIMENSIONS
        or not all(type(value) in (int, float) and -1 <= value <= 1 for value in vector)
        or not any(vector)
        for vector in vectors
    ):
        raise ValueError("Ollama returned invalid EmbeddingGemma vectors")
    return vectors


def _reciprocal_rank_fusion(*rankings):
    hits = {}
    scores = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            key = hit["id"]
            hits.setdefault(key, hit)
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
    return sorted(hits.values(), key=lambda hit: scores[hit["id"]], reverse=True)


class SolrRetriever:
    def __init__(self, config):
        self.config = config

    def _post(self, handler, payload, *, timeout=None):
        response = _post_json(
            self.config.solr_url, f"/solr/{self.config.core}/{handler}", payload,
            timeout if timeout is not None else self.config.timeout_seconds,
            f"Solr core {self.config.core}",
        )
        header = response.get("responseHeader")
        if not isinstance(header, dict) or header.get("status") != 0:
            raise ValueError("Solr did not complete the request")
        return response

    def _select(self, query, fields, limit, *, filters=(), params=None):
        response = self._post("select", {
            "query": query, "filter": list(filters), "fields": fields, "limit": limit,
            "params": params or {},
        })
        matches = response.get("response")
        docs = matches.get("docs") if isinstance(matches, dict) else None
        if not isinstance(docs, list) or len(docs) > limit or any(
            not isinstance(doc, dict) for doc in docs
        ):
            raise ValueError("Solr returned invalid document search results")
        return docs

    def _manifest(self):
        docs = self._select(f"id:{CORPUS_ID}", ["snapshot", "embedding_model"], 1)
        if not docs:
            raise ValueError("The Solr corpus is empty. Run 'python -m orchestrator.index_docs'")
        snapshot = docs[0].get("snapshot")
        if not isinstance(snapshot, str) or not SNAPSHOT_ID.fullmatch(snapshot):
            raise ValueError("Solr returned an invalid corpus snapshot")
        return snapshot, docs[0].get("embedding_model")

    def retrieve(self, question, top_k, embed_question):
        snapshot, model = self._manifest()
        if self.config.method != "keyword" and model != EMBEDDING_MODEL:
            raise ValueError("This Solr corpus has no EmbeddingGemma vectors. Reindex with the configured search method")
        filters = ("kind:chunk", f"snapshot:{snapshot}")
        fields = ["id", "path", "heading", "line", "text"]
        limit = 20
        rankings = []
        if self.config.method != "vector":
            # Pass plain terms as a parameter instead of exposing the Solr query language.
            terms = " ".join(re.findall(r"[^\W_]\w*", question.lower())[:80])
            if terms:
                rankings.append(self._select(
                    "{!edismax qf='heading^3 path^2 text' uf='-*' v=$terms}", fields, limit,
                    filters=filters, params={"terms": terms},
                ))
        if self.config.method != "keyword":
            vector = embed_question(question)
            rankings.append(self._select(
                f"{{!knn f=embedding topK={limit} v=$vector}}", fields, limit,
                filters=filters, params={"vector": json.dumps(vector, separators=(",", ":"))},
            ))
        if self._manifest() != (snapshot, model):
            raise ValueError("The Solr corpus changed during retrieval. Prepare the run again")
        hits = _reciprocal_rank_fusion(*rankings)
        return snapshot, [self._source(hit) for hit in hits[:top_k]]

    @staticmethod
    def _source(hit):
        try:
            text = hit["text"]
            if len(text) > CHUNK_CHARS:
                raise ValueError("Passage is too long")
            return Source(
                id=hit["id"], text=text, kind="local_document",
                title=f"{hit['path']}:{hit['line']} · {hit['heading']}"[:300],
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("Solr returned an invalid document passage") from None

    def replace_corpus(self, manifest, chunks):
        # Replace one nested Lucene document block so removed Markdown passages
        # disappear in the same update as the new snapshot.
        self._post("update?commit=true", [{**manifest, "_childDocuments_": chunks}], timeout=60)
