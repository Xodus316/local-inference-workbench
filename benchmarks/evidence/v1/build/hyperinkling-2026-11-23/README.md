# HyperInkling at 98K on an RTX 3090

DSpark approximately doubled HyperInkling's decode rate on both the Starship Build and the direct Commerce test-authoring replay. In Starship, DSpark on passed 19 of 20 held-out checks. DSpark off passed all 20 and finished about four minutes ahead of the updated Unsloth build. In the separate Commerce implementation test, HyperQwen wrote all 71 requested files and cleared frontend type checking. Every submitted implementation failed at least one build gate.

## Starship Build

Each stack ran one Workbench Build with the same frozen task, plan and tests, a 10,409-token implementation prompt, 98,304-token context, and `high` reasoning. There were no repair calls. All three passed the ordinary Build tests. The same independent 20-case suite scored the first implementation and the final submission, with identical scores at both checkpoints. [Task, suite and earlier quant comparison](../combat/inkling-2026-11-21/README.md)

| Stack | Held-out checks | Decode tok/s | Build wall time | Peak board memory |
|---|---:|---:|---:|---:|
| HyperInkling, DSpark off | 20/20 | 103.39 | 2:54 | 21,408 MiB |
| HyperInkling, DSpark on | 19/20 | 226.66 | 1:39 | 21,433 MiB |
| Unsloth `UD-IQ4_XS` | 20/20 | 61.20 | 6:56 | 18,771 MiB |

The DSpark-on submission returned `Draw` when a ship that was already destroyed should have been unable to fire and `Ship 2 Wins` was expected. DSpark accepted 15,361 of 32,295 drafts (47.6%). Decode rates include reasoning and cover model generation. Build time includes verification and workflow overhead.

## Commerce test authorship (direct replay)

The three stacks replayed the same frozen Run 17 test-authoring prompt with 12,620 input tokens, `xhigh` reasoning and a 65,536-token response cap. All three reached the cap during reasoning and returned no answer. No test suite reached reference grading.

| Stack | Decode tok/s | Time to first token | Wall time | Peak board memory |
|---|---:|---:|---:|---:|
| HyperInkling, DSpark off | 100.24 | 4.62 s | 10:58 | 21,183 MiB |
| HyperInkling, DSpark on | 211.26 | 5.19 s | 5:15 | 21,279 MiB |
| Unsloth `UD-IQ4_XS` | 58.39 | 22.97 s | 19:05 | 18,199 MiB |

DSpark accepted 48,538 of 94,330 drafts (51.5%). The measured rate was 2.11× the matched DSpark-off rate and 3.62× the Unsloth rate. The reported pair used the same Docker memory settings. An earlier DSpark-on start under the default memory settings stalled before a request was submitted.

## Commerce Run 18 implementation (OpenCode)

Four fresh OpenCode 1.19.0 sessions received the same saved Run 18 implementation instructions and 71-file approved scope. Each used `xhigh` reasoning, a 16,384-token thinking target and a 65,536-token completion ceiling per request at 98,304 context. Backend compilation and frontend parsing, type checking and bundling were evaluated independently in the same offline verifier. The 14-case supplemental regression suite required a complete scope and passing compiler gates, so it was skipped for all four stacks.

| Stack | Files written | Gradle compile | Frontend parse | Type check | Bundle | Completed native requests | OpenCode wall time |
|---|---:|---|---|---|---|---:|---:|
| HyperQwen, Qwen 3.8 27B AutoRound-fast + MTP | 71/71 | Fail (11) | Pass | Pass | Fail | 47 | 24:01 |
| HyperInkling, DSpark on | 67/71 | Fail (10) | Pass | Fail (1) | Pass | 346 | 23:25 |
| Unsloth `UD-IQ4_XS`, compatibility retry | 60/71 | Fail (10) | Pass | Fail (6) | Fail | 40 | Unavailable |
| HyperInkling, DSpark off | 0/71 | Skipped | Skipped | Skipped | Skipped | 6 | 5:49 |

Parenthesized counts are Java or TypeScript diagnostics visible in bounded verifier excerpts. HyperQwen's failures included an unresolved Java `ObjectId` and an unresolved `@mdi/font/css/mdi.css` import during bundling. HyperInkling with DSpark on omitted four API response classes and failed type checking on `vuetify/lib`. The DSpark-off session stopped normally without writing any files.

The first Unsloth session hit a Windows tokenizer resource error before any writes. The compatibility retry removed only generated assistant reasoning fields longer than 65,536 characters from later requests and preserved the original instructions and tool payloads. A second tokenizer error left the stream incomplete. The verifier graded 60 distinct approved files recovered from 66 confirmed write receipts. This row represents a partial result under modified transport handling.

HyperQwen was the most complete starting point on this task. HyperInkling with DSpark on remains a candidate for the implementation role with compiler and held-out verification gates. The results are single trials under these specific runtime and prompt settings.

## Runtime and evidence

All trials used a single 24 GiB RTX 3090 and seed `20261123`. HyperInkling 0.1.3 ran a W4A16 g128 GPTQ checkpoint in CUDA Docker under WSL2 with FP8 KV caches and lookup disabled. Its matched toggle used GPU utilization `0.92` and a PyTorch allocator memory fraction of `0.95`. The native Windows Unsloth `b12649-mix-c7059d6` runtime used the updated `UD-IQ4_XS` GGUF and Q8_0 KV caches. The HyperQwen row used the pinned OpenCode profile. [HyperInkling checkpoint](https://huggingface.co/Pentium95/Inkling-2-Mini-W4A16-g128-gptq) · [Unsloth release](https://github.com/unslothai/llama.cpp/releases/tag/b12649-mix-c7059d6) · [HyperQwen profile](../../../../../examples/hyperqwen/README.md)

The archive records aggregate metrics and package fingerprints. The exact Commerce prompts, submitted source and verifier inputs are unavailable in this bundle. [Machine-readable results](results.json)
