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

Qwen medium produced the stronger first draft after the import correction. Each row represents one sampled draft, and the Gemma baseline used the original prompt. Test counts vary because the models authored different suites. No draft in this comparison satisfied all the reference and coverage checks required to freeze it.
