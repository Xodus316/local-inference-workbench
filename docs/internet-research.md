# Internet Research on a local PC

Choose **Internet Research** when the answer needs current public sources. Gemma and Qwen read the question on the PC, Qwen drafts up to two public search queries, and the configured provider returns result URLs and excerpts. Gemma synthesizes those excerpts with the supplied evidence. The panel shows the queries, clickable sources and which sources the answer cited. The operator approves the search-enabled run before any lookup.

## Which provider to use

**Start with Exa for occasional technical research.** Its hosted MCP search works from the PC without an API key. On three live documentation questions it returned an official source with text supporting the answer in the first three results every time. The free endpoint is rate-limited. Model inference stays on the local Ollama daemon whichever provider you select. [Exa MCP](https://exa.ai/docs/reference/exa-mcp)

| Keyless Exa pilot, three results per query | Evidence returned | Search time |
|---|---|---:|
| What endpoint and authentication does Ollama web search use? | Rank 1 was Ollama documentation showing the HTTPS POST endpoint and bearer key. | 0.388 s |
| What happens when Python 3.13 `asyncio.wait_for` times out? | Rank 1 was the latest Python docs, which described cancellation and `TimeoutError`. Rank 2 was CPython's 3.13 source and corroborated the behavior for that version. | 0.402 s |
| Can SQLite WAL readers run during a write, and how many writers can run? | Rank 1 was SQLite's WAL documentation. It supports concurrent readers and one writer at a time. | 0.402 s |

The pilot used one MCP session. Its initial handshake and tool discovery took another 0.249 s. A second run used Workbench's actual provider adapter and started a fresh protocol session for each query. The three lookups took **0.585, 0.578 and 0.595 seconds** (1.758 s total). An official page appeared at ranks **1, 2 and 1** respectively. Review of the exact excerpts available to synthesis confirmed both requested facts for each question. In the SQLite case the rank-1 WAL excerpt established reader/writer overlap and the rank-3 SQLite isolation excerpt established the single-writer limit.

These are retrieval measurements for three documentation questions. They exclude model generation. Ollama and Brave had no configured keys in the test environment and received zero requests. Their accuracy and speed on these questions remain unmeasured.

| Provider | Fit for this workflow | Cost and operational tradeoff |
|---|---|---|
| **Exa hosted MCP** (integrated) | Returns URLs and highlights. New example configurations select it, and no additional service is needed on the PC. | Free, rate-limited keyless access. The separate Exa REST API lists $7 per 1,000 Auto searches and $10 in monthly free credits. [MCP access](https://exa.ai/docs/reference/exa-mcp) · [REST pricing](https://exa.ai/pricing) |
| **Ollama Web Search** (integrated) | Returns the URL and a content snippet in a documented JSON format. Suitable if you prefer to use an Ollama account. | Free tier with an account key. Higher limits are available. For scheduled or high-volume use, check the limits on your account. [Ollama](https://ollama.com/blog/web-search) |
| **Brave Search** (integrated) | Independent web index, extra excerpts and a published per-query price. Existing configurations that omit the provider keep Brave. | Lists $5 per 1,000 searches and $5 in monthly search credits. At two queries per run, that credit covers about 500 runs. Workbench saves search responses for replay; Brave says result storage requires a plan that includes storage rights. [Brave pricing and storage FAQ](https://brave.com/search/api/) |
| **Tavily** (alternative) | Useful when you want domain restrictions and date ranges in the request, or deeper content extraction. | 1,000 free credits per month. Two basic searches use two credits, giving about 500 two-query runs. Two advanced searches use four credits. [Search options](https://docs.tavily.com/documentation/api-reference/endpoint/search) · [Credit costs](https://docs.tavily.com/documentation/api-credits) |
| **SearXNG** (alternative) | Useful when you want to run the search gateway yourself and choose its upstream engines. | Requires maintaining an instance and enabling JSON output. Queries are sent to the engines you enable, and many public instances disable JSON. [SearXNG search API](https://docs.searxng.org/dev/search_api.html) |

Pricing is as checked on November 6, 2026. The recommendation reflects the setup and excerpt format for this workflow. Search relevance, latency and citation support depend on the questions and require a matched comparison.

## Enable search on Windows

For the keyless Exa configuration, set `"research": {"enabled": true, "provider": "exa", "results_per_query": 3}` in the service configuration. Restart the idle Workbench and select **Internet Research**. Approval sends the generated queries to Exa's hosted MCP endpoint. Its service may throttle anonymous requests; the run records the error and any sources already retrieved.

### Use an Ollama account

1. In the Workbench configuration, set the following `research` object. The selected provider receives the queries approved for that run.

   ```json
   "research": {"enabled": true, "provider": "ollama", "results_per_query": 3}
   ```

2. Create an Ollama API key and make it available to the Windows account that launches the service as the environment variable `OLLAMA_API_KEY`. Use the Windows Environment Variables dialog to set a persistent user variable. Keep the value out of prompts and configuration files. [Ollama key settings](https://ollama.com/settings/keys)

3. Restart the idle service **and its launching control panel** so they inherit the variable. Select **Internet Research** in the workflow panel. Its readiness indicator checks configuration and key presence. The first approved search establishes whether the provider accepts that key.

4. Try “What endpoint and authentication does Ollama document for web search? Cite its documentation.” Open the cited source and check whether the retrieved excerpt supports the answer. No download or model change is required for the search provider.

For Brave use `"provider": "brave"` and `BRAVE_SEARCH_API_KEY`. Each run uses the approved provider. An invalid key, provider throttling or a lost response is recorded against that provider.

## Compare providers on the PC

Run the following from the checkout using the Workbench Python environment. The first command prints the three fixed questions and local provider readiness without sending a request. The second sends the same questions to each selected, configured provider. It uses three results per query and requires no model calls.

```sh
python scripts/compare-search.py
python scripts/compare-search.py --providers exa ollama brave
```

The report gives the native result rank, URL, excerpt and elapsed seconds for each lookup. Check both facts in each excerpt review prompt. A first-party domain alone does not show that the text answers the question. An unavailable provider has zero dispatched queries and has no accuracy score. [Comparison script](../scripts/compare-search.py)

## What a completed run establishes

The final answer must cite at least one retrieved excerpt line. A citation to an attachment or the URL header alone cannot satisfy that check. If no usable excerpts arrive, the run ends without a research answer. If only some queries succeed, an answer supported by the retrieved excerpts can be shown with a degraded status. Checking that a quote exists does not establish that every claim follows from it.

The existing **Balanced** option searches when its readers report uncertainty and search is enabled for that run. **Internet Research** requests a lookup even when both readers are confident. Both modes save local receipts. Resuming a known search reads its receipt; an unresolved dispatch is surfaced for inspection.
