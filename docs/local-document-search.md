# Search local documents

Retrieval-augmented generation (RAG) gives a reasoning run passages from an indexed Markdown corpus. Workbench searches a standalone Solr core on the same machine and puts the selected passages in the approval bundle. The local model can cite exact lines from those passages.

## Start Solr and index the docs

From the Workbench checkout, start the optional service with Docker Compose. It binds to `127.0.0.1:8983` and keeps the index in a Docker volume.

```sh
docker compose -f compose.rag.yml up -d --wait
```

Copy `config.example.json` to `config.local.json` if you haven't configured Workbench yet. Add or update the top-level `rag` entry.

```json
{"rag": {"enabled": true, "method": "keyword"}}
```

Seed the core, then start or restart Workbench with that configuration.

```sh
python -m orchestrator.index_docs --config config.local.json
python -m orchestrator --config config.local.json
```

## How the index is built

1. The indexer reads `README.md` and Markdown files under `docs/`. Pass `--root PATH` to use another directory with the same layout.
2. It splits the text at Markdown headings and caps passages at 1,600 characters. Each passage keeps its text, relative path, heading, starting line and a deterministic ID. Solr indexes the path, heading and text for BM25 keyword search.
3. With `vector` or `hybrid` enabled, the indexer sends headings and passage text to the local Ollama `/api/embed` endpoint in batches of 16. **EmbeddingGemma** creates the 768-dimensional vectors. Ollama runs the model, and Solr stores the vectors. At search time, Workbench uses the same model to embed the question for Solr's similarity search.
4. The indexer writes the passages as a single document block under a corpus record in the `workbench_docs` core, then commits the update. The corpus record identifies the snapshot used during retrieval.

Indexing is manual. Run the command again after changing the docs. Each successful update replaces the previous corpus, including passages from deleted files.

## Run a grounded workflow

In Workflows, select **Search local docs** in Auto, Fast, Balanced, Verified, or Deep. Enter a question about the indexed docs, select **Prepare for review**, and open the passages under **Local documents** to inspect them. After approval, cited passages are marked in that list. **Full assessment and citations** shows the source IDs, excerpt line numbers and quoted text.

Local and public Internet search are separate options. If the selected local search fails, preparation shows the error. A successful search with no matches prepares the run with an empty retrieval result. Approval and replay use the saved passages, so they can proceed with Solr offline.

## Compare retrieval methods

| `rag.method` | What it retrieves |
|---|---|
| `keyword` | BM25 matches in headings, paths and passage text. Works without an embedding model. |
| `vector` | Solr nearest-neighbor matches using local EmbeddingGemma vectors. |
| `hybrid` | Keyword and vector candidates merged with reciprocal rank fusion. |

For `vector` or `hybrid`, start your local Ollama service and pull `embeddinggemma`. Change `rag.method`, run the indexer with the same config, and restart Workbench. Reindex after updating the embedding model. Run indexing while inference is idle, since indexing and generation can share the Ollama host.

```sh
ollama pull embeddinggemma
python -m orchestrator.index_docs --config config.local.json
```

`rag.top_k` controls how many passages are included (default 5, maximum 8). The query uses the first 80 keyword terms and, for vector retrieval, text up to the embedding model's context limit. Keyword and vector queries each return up to 20 candidates for ranking.

`docker compose -f compose.rag.yml down` stops Solr and retains the index. Set `rag.enabled` to `false` and restart Workbench to hide the option.
