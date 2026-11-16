# Commerce test-author evaluation, November 15, 2026

The task was to author executable black-box tests for a 14-criterion Commerce Catalog recreation. The first-pass trials used the same saved plan, trusted reference application, pinned offline Docker image, 65,536-token context, and 32 KiB decoded test-source limit on one RTX 3090. Each first-pass trial allowed one model draft. A draft had to clear scope, syntax, and environment checks before running against the reference. This evaluation stopped at test authoring.

## First drafts

| Test author | Reasoning | Model time | Decode | First-pass result |
| --- | --- | ---: | ---: | --- |
| Gemma 4 31B IQ4_XS | Off | 199.8 s | 35.2 tok/s | Mapped 12/14 criteria and passed 7/12 reference tests. |
| Gemma 4 31B IQ4_XS | On | 263.3 s | 33.7 tok/s | The initial draft omitted three criteria. The separate test-repair run reached 9/15 reference tests at its best. |
| Gemma 4 26B Q4_K_M | Off / on | 67.1 / 128.1 s | — | Both drafts supplied application source and zero test files. |
| Gemma 4 12B | Off / on | 87.8 / 140.4 s | — | Both drafts supplied application source and zero test files. |
| Qwen 3.5 9B Q4_K_M | Off / on | 43.0 / 124.7 s | — | With thinking off, it emitted application source. With thinking on, its 39,280-byte test file covered one criterion and exceeded the suite limit. |
| Qwen3-Coder-Next UD-IQ1_M | Native | 294.0 s | 107.2 tok/s | Returned application source and zero test files. Model loading took 230.4 s. |
| Qwen3-Coder-Next UD-IQ2_XXS | Native | 203.7 s | 88.0 tok/s | Returned a Gradle build file and zero test files. |
| Qwen3-Coder-Next UD-IQ3_XXS | Native | 300.7 s | 55.5 tok/s | Returned test files and classified them as application files, so scope validation stopped the run. The initial startup took 310.3 s and timed out before generation. The table reports the completed retry. |
| Qwen3-Flash-Next IQ3_XXS via Strata | Off | 118.7 s after startup | — | Generated 6,288 tokens and returned invalid JSON. |
| Qwen 3.8 27B MTP Q4_K_M | Medium | 184.6 s | 81.1 tok/s | Mapped 11/14 criteria. Preflight rejected the draft's test-local imports. |
| Qwen 3.8 27B MTP Q4_K_M | Xhigh | 1,095.8 s | 55.3 tok/s | Generated 59,933 reasoning tokens, filled the context, and returned zero test source. |

## Helper import correction and reference comparison

The pinned image invokes isolated Python unittest discovery with the frozen test directory as the discovery root. A helper stored at `tests/helpers.py` is importable as `helpers`. The shared test-author instructions now give that example. A CPU probe in the pinned image confirmed the direct import succeeds and `tests.helpers` fails.

| First pass | Model time | Total time | Generated tokens | Manifest criteria | Reference |
| --- | ---: | ---: | ---: | ---: | ---: |
| Gemma 4 31B IQ4_XS, thinking off, original prompt | 199.8 s | 262.2 s | 6,382 | 12/14 | 7/12 |
| Qwen 3.8 27B MTP Q4_K_M, medium, corrected prompt | 282.3 s | 435.1 s | 19,846 | 13/14 | 37/39 |

Qwen supplied 16,450 bytes across nine files and compiled all of them in the pinned image. Its login and dashboard UI tests passed. The reference returned 500 for the draft's `/nonexistent404` request. A tracked-URL update test failed on a comparison between timestamp strings with different precision. The manifest also cited an API concurrency test for UI CRUD and a dashboard test for Reports. Those citations overstate the tested UI coverage.

Gemma was faster in wall time despite lower decode throughput. Its initial suite failed three UI cases and two API cases on the reference. A separate thinking-off run with test repair reached 9/11 reference tests and ended after 587.4 s without a frozen suite. The thinking-on run also ended without a frozen suite.

Qwen medium produced the stronger first draft after the import correction. Each row represents one sampled draft, and the Gemma baseline used the original prompt. Test counts vary because the models authored different suites. No first-pass draft satisfied all the reference and coverage checks required to freeze it.

## End-to-end Commerce runs

The full runs reused the saved plan, trusted reference, pinned image, and 32 KiB test-source limit. Both used Qwen 3.8 27B at 98K for native implementation and repair, with Gemma 4 26B as the independent reviewer. The Qwen author arm explicitly approved the same model for test authoring and implementation. The Gemma 31B author ran with thinking off.

| Attempt | Wall time | Outcome |
| --- | ---: | --- |
| Qwen 27B medium, initial | 5:34 | Returned reasoning without a final test draft and stopped at test authoring. |
| Gemma 31B, thinking off | 11:52 | Passed 6/14 reference tests initially and 7/14 after repairs. The suite could not freeze. |
| Qwen 27B medium, retry | 49:43 | Repaired its suite to 28/28 on the reference, froze nine test files, and generated 54 application files in 12 groups. Backend compilation, frontend build, and typecheck passed after three compile repairs. Full verification found a Spring Data repository declaration that prevented API startup. Repair selection then rejected a response containing prose where file paths were required. |
| Qwen candidate with assisted backend repair | 22:59 | Passed runtime verification. Gemma flagged async form validation in Products and Categories, and Qwen repaired both views. Runtime verification passed again. The reviewer filled its context while repeating an unchanged source chunk. |
| Recovery from the verified candidate | 6:45 | Completed with the same frozen suite, 28/28 generated tests, all required trusted checks, and zero blocking review findings. This time covers the final recovery and verification. |

The repair handoff was changed to surface verified startup failures before downstream checks and restrict model selections to existing editable paths. A model-only recovery got the API to start but still failed registration and 24 frozen tests. A three-file assisted backend patch removed an invalid repository update declaration, aligned MVC routes with API authentication, and made tracked-URL click updates atomic. The patch passed the pinned CPU verification before it was used for review. The frozen suite stayed byte-identical through recovery.

The final recovery reused three review receipts only after their full requests and cited source matched byte for byte. Gemma freshly reviewed the remaining chunk, which contained the two frontend changes. Delivery verified hashes for 54 application files and nine test files. The offline image exercised the UI with Vitest DOM. Review left nonblocking findings on logout handling and Affiliates sorting.

## Single-session OpenCode comparison

Each trial used one local model on the same RTX 3090 to plan, implement, test, and repair the same 14-criterion Commerce recreation in one OpenCode 1.19.0 session with automatic compaction. Each model wrote its own plan. The acceptance harness paired a pinned offline image with the fixed 28-test suite from the earlier Qwen run and the trusted checks. Model-authored self-tests were advisory and subject to the 32 KiB suite limit. The workflow assigned review to the same model.

| Model and serving configuration | Planning | Implementation | End of run |
| --- | ---: | --- | --- |
| Qwen 3.8 27B MTP Q4_K_M, medium, 98,304 context, Ollama | 8:48 | Wrote all 80 planned files in 36:46, or 45:34 from the start. | Hit the two-hour deadline during candidate repair. The final acceptance attempt passed 10/28 frozen tests. |
| Qwen3-Flash-Next IQ3_XXS, thinking off, 65,536 context, Strata | 16:11, including one plan repair | Wrote 40 of 76 planned files. | Stopped on request at 1:40:30 during implementation. Build, typecheck, and acceptance were pending. |

Qwen's final candidate passed the frontend build and typecheck, backend build and packaging, API startup, and persistence checks. Trusted API and held-out checks failed. Several endpoints returned HTTP 200 for invalid input, missing records, or duplicate SKUs. Concurrent tracked clicks failed to persist the expected counts, revenue, and last-click timestamp. The offline verifier reported browser execution unavailable for this trial. The harness originally spent time on oversized optional tests and hit Windows path-length problems. Shorter staging paths and running fixed acceptance first resolved those harness issues. The run resumed with the original OpenCode session and model-authored files.

Flash corrected an invalid plan in the same session and continued implementation after a scope check blocked an unplanned write. Automatic compaction reduced one prompt from about 45K tokens to 8.7K, cutting prefill from 129 seconds to 27 seconds. The Strata bridge disabled conversation checkpoints for these verified tool calls, so subsequent requests generally reused only the roughly 2,100-token shared prefix. Ollama restored longer prefixes during the Qwen run. The two trials compare the serving configurations as tested, including their different contexts, reasoning modes, and cache behavior.

| Model | Timed requests | Generated tokens | Prefill time | Decode time | Decode | Including prefill |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen 27B | 144 | 328,524 | 12:31 | 1:35:48 | 57.2 tok/s | 50.5 tok/s |
| Flash-Next IQ3_XXS | 76 | 33,399 | 1:27:41 | 11:30 | 48.4 tok/s | 5.6 tok/s |

Rates divide total generated tokens by measured backend time. Qwen decoded at 64.3 tok/s during planning, 64.1 during implementation, and 48.3 in its last completed repair turn. The native timings count actual generated tokens, including reasoning and tool calls, separately from speculative drafts. The totals omit untimed in-flight cancellations and exclude wall time spent in tools, tests, and restarts. Flash's two preliminary tool smoke requests are also excluded.

Strata acknowledged Flash's cancellation during prefill. Automatic cleanup could not confirm the request because its verifier expected a first generated token. A follow-up check verified the cancellation, exited the remaining trial-owned processes, confirmed port closure, and observed the GPU return to its desktop memory baseline.

## HyperQwen at 98K with medium reasoning

HyperQwen served Qwen 3.8 27B AutoRound-fast W4A16 through vLLM 0.33.0 on the same RTX 3090 under Docker Desktop and WSL2. The profile used a 98,304-token window, medium reasoning, one active sequence, three probabilistic MTP drafts, PTH FlashAttention with an int8 GPU KV cache, and prefix caching. The launcher set vLLM's GPU memory utilization to 0.89, and the server reported a GPU KV pool of 174,684 tokens. Guidance rejected the auto-tool grammar on the first probe attempt. The completed probes and full trial used the pinned image's patched XGrammar backend and strict tool parameters. [HyperQwen revision](https://github.com/syv-ai/HyperQwen/tree/3d6c9543292efc17372b824071ef8d9f33366b21)

### Warm request and OpenCode tool probes

| Probe | Generated tokens | Decode | Including time to first token | Prompt cache reuse |
| --- | ---: | ---: | ---: | ---: |
| Short repeat, warmed by an identical request | 768 | 117.8 tok/s | 111.2 tok/s | 6,528 / 6,947 (94.0%) |
| First long OpenCode request, cold | 206 | 110.2 tok/s | 5.0 tok/s | 0 / 39,763 |
| Six warm OpenCode requests | 1,350 | 107.6 tok/s | 80.4 tok/s | 241,536 / 244,806 (98.7%) |

The short request reached its 768-token cap while reasoning. Cache reuse dropped its time to first token from 6.11 s to 0.39 s. The first long request took 39.38 s to reach its first token. The six later requests took 0.37–0.99 s each. OpenCode streamed reasoning, wrote a line with literal quotation marks, read it back, and replaced it in the same session.

### Full Commerce comparison

The full trial used the same OpenCode 1.19.0 executable, context, reasoning level, Commerce brief, pinned verifier image, reference, and frozen 28-test suite as the Ollama control. Each stack kept one session with automatic compaction. HyperQwen used the AutoRound weights and three MTP drafts. Ollama used Q4_K_M and four MTP drafts. The comparison covers the serving stacks as configured.

| Metric | HyperQwen | Ollama |
| --- | ---: | ---: |
| Total measured run | 1h 27m 49s, exhausted three repairs | 2h 00m 01s, deadline during repair |
| Planning | 3m 34s | 8m 48s |
| Implementation turn | 62m 24s, 81 planned files | 36m 46s, 80 planned files |
| Generated tokens | 421,935 | 328,524 |
| Native decode | 99.5 tok/s | 57.2 tok/s |
| Including prompt time | 83.4 tok/s | 50.5 tok/s |
| Best frozen test result | 21/28 | 10/28 |
| Final verifier | Frontend build and typecheck passed. Gradle failed before runtime tests. | Frontend and backend builds, typecheck, startup, and persistence passed. Frozen tests finished at 10/28. |

HyperQwen decoded about 74% faster and generated about 28% more tokens across the measured run. Two early implementation responses generated a combined 141,346 reasoning tokens before the first application writes. The first filled the context. OpenCode compacted the history and continued in the same session. All 81 planned files were present by about 59 minutes, and acceptance started at 66 minutes. HyperQwen completed 99 measured trial requests and reused 81.2% of their input tokens in aggregate. Its rates exclude startup and smoke checks. The inclusive rate uses engine time to first token for HyperQwen and logged prefill time for Ollama.

HyperQwen's first repair fixed a Vuetify type error and a Java serializer signature. The next verifier passed both builds, typecheck, API startup, MongoDB and persistence, and 21 of 28 frozen tests. Tracked-click requests returned HTTP 500, and reporting tests failed. The trusted API and held-out suites also failed. Later changes to the tracked-click code introduced compilation errors. The final attempt stopped at a Java `Decimal128(String)` constructor error before the runtime suites. Both runs ended without an accepted delivery. The owned HyperQwen container stopped and released GPU memory.

## Agentic Commerce workflow and test repair, November 16

The agentic trial used one 24 GiB RTX 3090 and a pinned offline verifier. HyperQwen served Qwen 3.8 27B AutoRound-fast W4A16 at 98,304 context. OpenCode used medium reasoning for planning, implementation, and the implementer's smoke suite, then xhigh for application repair. Gemma 4 31B IQ4_XS ran with thinking enabled at 65,536 context through Ollama and OpenCode to author and repair the independent suite. Its suite limit was 32 KiB. Gemma 4 26B A4B Q4_K_M was configured as the independent reviewer.

The first planner session failed to return a completed structured plan after compaction. With planning confined to the task brief, HyperQwen produced a 14-criterion plan in 4m 12s and wrote all 57 planned files in 17m 59s. The frontend build and typecheck passed. The first smoke draft lost its Python line breaks. The pinned XGrammar build rejected JSON escapes when the source field included a length bound. Removing that bound from the decoder schema restored escapes while Workbench continued to enforce source size and syntax. OpenCode then compacted after a complete smoke response and continued generating. Recovery reused the saved application and matched the completed smoke response to the backend receipt before running it in the verifier. [XGrammar issue](https://github.com/mlc-ai/xgrammar/issues/800)

### Independent reference testing

| Attempt | Wall time | Trusted reference result |
| --- | ---: | --- |
| First independent suite | 20m 13s | 6/14 passed. Workbench rejected the first repair prompt before dispatch. |
| Token-based admission | 16m 55s | Two Gemma repairs ran. The suite finished at 5/14. |
| Per-file repair | 14m 25s | All seven API tests passed. One UI response proposed an application file and was rejected. The later UI patch left seven failures. |
| Exact test path and public DOM observations | 17m 26s | Both UI drafts failed Python syntax validation. Isolated replays passed 9/14 after removing a stray final parenthesis and 11/14 after removing an embedded JSON suffix. The second draft dropped the prefilled edit-form checks. |
| Recovered draft with edit-form checks | 40m 49s | The reference moved from 9/14 to 12/14 and then 13/14. All 13 retained tests passed after the logout case was quarantined. Time includes application verification and repair. |

The original admission check counted serialized UTF-8 bytes against the numeric context limit. It counted 76,979 units including its reserve, compared with a 65,536-token window. The matching Gemma GGUF tokenizer measured 21,759 tokens for the controlled prompt, formatting, and schema allowance. With 2,048 reserved tokens for dynamic OpenCode framing, the request fit. The live backend reported 22,746 input tokens. In the final resumed attempt, Gemma generated 26,332 tokens across its two UI repairs in 953.2 seconds of OpenCode worker time, or 27.6 tokens/s on that basis.

The API tests had compared freshly issued JWT strings and assumed report fixtures occupied fixed positions in a shared result. The repairs exercised each token on a protected request and checked report ordering and values by fixture ID. The UI tests needed to await login and navigation, match rendered links and row actions, and verify actual dialog fields. File-scoped repair preserved other test methods, constrained the generated path, and used observed public DOM behavior from the pinned reference. Recovery chose the syntax-corrected draft that retained the prefilled edit-form checks. The frozen suite contained 29,837 UTF-8 bytes across three files. Logout still triggered unhandled HTTP 403 responses in the reference after the route assertions passed. Quarantining that test left the logout UI criterion unverified.

### Application verification

HyperQwen used three xhigh application repair attempts. The worker stages took 99.1 s, 82.2 s, and 717.1 s. Their native decode rates were 110.6, 117.2, and 100.2 tokens/s, respectively, compared with 102.5 for planning and 106.0 for implementation. These decode rates include generated reasoning tokens and exclude prefill, tool time, and verifier execution.

The final application passed Gradle compilation, backend packaging, Vue typechecking, the Vite build, and persistence. Tracked URL clicks still returned HTTP 500. Trusted API and held-out checks failed, and the app passed 5/13 frozen tests. Workbench ended at `review_required` before independent code review or accepted delivery. A separate CPU diagnostic with BSON ID matching, decimal arithmetic, and ISO timestamps at MongoDB millisecond precision cleared the trusted backend checks and still failed the frozen UI suite. The trial backends shut down and released the GPU.
