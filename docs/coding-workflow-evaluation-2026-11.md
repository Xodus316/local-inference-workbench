# Local coding workflow evaluation, November 2026

The evaluation followed a fixed Starship Battle implementation replay and then a full-stack Commerce Catalog recreation. Hardware was an RTX 3090 with 24 GiB VRAM and 64 GiB system RAM. Times refer to the recorded run or inference stage. Generated-token totals include reasoning where the backend counted it. A compiler pass, a scoped diagnostic, and a completed workflow have different acceptance gates.

The report records aggregate results and receipt names from the evaluation workspace. Raw local evidence and candidate repositories are held with the evaluation. The historical test outcomes predate the publication build.

## Starship Battle model and quant comparisons

The same frozen plan and tests were used for the primary comparisons. The approval suite contained 14 tests and the independent suite contained 20. Results in the independent column are for the initial implementation. Wall times include workflow overhead and any repair shown in that row.

| Implementation | Context | Generated tokens | Decode rate | Workflow time | Initial independent | Approved outcome |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| Flash-Next IQ3_XXS, Strata | 32,768 | 24,886 | 65.4 tok/s | 6m 43s model call | Unrun | Generation ended at the context limit during reasoning |
| Flash-Next UD-IQ4_XS, Strata | 32,768 | 24,886 | 20.7 tok/s | 21m 27s model call | Unrun | Generation ended at the context limit during reasoning |
| Flash-Next IQ3_XXS, Strata | 65,536 | 43,158 | 59.3 tok/s | 12m 45s | 20/20 | 14/14 without repair |
| Flash-Next UD-IQ4_XS, Strata | 65,536 | 51,113 | 18.6 tok/s | 47m 30s | 20/20 | 14/14 without repair |
| Coder-Next UD-IQ1_M, Ollama | 65,536 | 1,393 | 112.1 tok/s | 11m 46s | 3/20 | 14/14 after one Qwen repair |
| Coder-Next UD-IQ2_XXS, Ollama | 65,536 | 1,546 | 88.4 tok/s | 8m 12s | 16/20 | 13/14 initially, 14/14 after one Qwen repair |
| Qwen 27B MTP Q4_K_M, Ollama | 98,304 | 20,869 | 52.7 tok/s | 6m 58s | 20/20 | 14/14 without repair |

The Flash-Next runs requested high reasoning, mapped by the pinned template to xhigh without a separate answer reserve. The 32K coding trials used all available generation room after a 7,874-token input. Separate short 32K generation probes ended normally at 61.6 and 21.0 tok/s for IQ3_XXS and IQ4_XS. They exposed a normal EOS cleanup receipt validation defect in the wrapper, which the coding trials exercised with the fix. At 64K, IQ3_XXS took about a quarter of IQ4_XS's total wall time while passing the same two suites. The 98K Qwen 27B baseline finished faster end to end and generated about half as many tokens as the IQ3 run. These arms used different runtimes and context settings.

Ollama reported 22.71 GB fully GPU-resident for Coder-Next IQ1_M. IQ2_XXS used 24.66 GB total with 23.33 GB GPU-resident. These are measured resident model allocations in the tested 64K configuration, in decimal GB. Qwen 27B used 18.14 GB GPU-resident in its 98K sample. The separate higher-quant inventory contains estimates for variants that were never loaded.

DeepSeek R1 Distill Qwen 32B also ran at 32K. Q4_K_M passed 13/14 initial approved tests and IQ4_XS passed 10/14. Both failed before repair because the repair context did not fit. A follow-up with a 64K repair context left Q4_K_M blocked on unknown native completion and paused IQ4_XS before dispatch. This produced no comparable finished repair result.

Evidence in the workspace directories `flash-generation-results`, `starship-flash-results`, `starship-flash-64k-results`, `coder-next-iq2-starship-results`, `qwen27-starship-results` and `r1-32b-starship-repair64-v2-results`.

## Commerce history before the whole-project OpenCode workflow

The Commerce experiment asked the local workflow to recreate an existing full-stack catalog from a frozen specification. The evidence below distinguishes full workflow status from a compiler pass, a review report, and a CPU-only diagnostic on a saved candidate. Durations are wall time where recorded. Token counts and model settings come from the saved calls. Commerce evidence names are relative to the evaluation workspace.

### Initial runs and the first browser preview candidate, November 9–10

The initial configuration used Gemma 4 26B for planning, Gemma 4 31B IQ4_XS for independent tests, and Qwen 27B Q4_K_M at 98,304 context with `xhigh` reasoning for implementation and repair. Early exits explain why Qwen had no implementation call in several runs. The 10 GiB sandbox setting was a system RAM limit for builds and tests.

| Retained run | What reached the gate | Outcome |
| --- | --- | --- |
| `attempt-1` | Planning, test authoring, one coverage repair and project grouping | Failed because a group identifier violated the schema. No implementation or sandbox test ran. |
| `attempt-20261110` | Planning, test authoring and one coverage repair | Failed because the addition did not reference tests in new files for the requested criteria. |
| `attempt-20261110-r2` | Independent tests passed validation and the project planner tried two repairs | Group-plan repair budget exhausted. A group selected context outside its declared earlier dependencies. |
| `attempt-20261110-r3` | Test authoring and two coverage repairs | Generated `tests/test_ui.py` had invalid Python syntax. |
| `attempt-20261110-10g` | Planning and test authoring | The suite did not supply the required unittest-discovery command. |
| `attempt-20261110-10g-retry` | Planning, test authoring and two coverage repairs | Coverage additions targeted existing test files, so the suite could not be frozen. |
| `attempt-20261110-r4` | Five Qwen implementation groups, then two completed repairs | Workflow failed with an inconclusive Node 512 MiB heap OOM. The 46-file final model candidate was exported separately for review. |

R4 ran for about 80 minutes overall. The five implementation calls accounted for 48m 13s and 170,288 completion tokens. The export retained hashes for all 46 implementation files. A CPU-only replay at a 2,048 MiB Node heap exposed actual frontend type errors. An independent compile-only check of the frozen suite also found a Python 3.10.12 parse error in the API tests. The original workflow stopped before Java compilation, API startup, acceptance execution, or held-out cases. R4 completion in `results/attempt-20261110-r4/completion.json` · Candidate export in `results/attempt-20261110-r4/candidate-export.json` · Final audit in `results/attempt-20261110-r4-static/final-audit.json`

The browser preview came from a separately repaired copy. A byte comparison with the 46-file R4 export finds 33 unchanged files, 13 modified files, and four added preview files. The backend edits corrected MongoDB ID annotation imports, added missing product accessors and registered a service, and wrapped the login response. The frontend edits corrected router typing and auth navigation, consolidated API URL and request handling, and rewired four views to that API client. The added files provided a container recipe, preview server and Vite configuration. The preview therefore demonstrates the model's project scaffold with human-driven integration fixes. Exact export in `commerce-catalog-20261110-r4-candidate` · Preview copy in `commerce-preview`

### Earlier compilation and independent review, November 10

The reviewed workflow inserted pinned backend and frontend compile checkpoints before advancing the affected groups, sent diagnostics to the repairer, and added source-grounded independent review. The first three reviewed attempts failed at test authoring or coverage repair. One exceeded the 16,384-byte test manifest on the second coverage addition, one put Python tests outside the accepted paths or names, and one exceeded the same limit in the initial test suite. The compact run reached the first backend checkpoint and exited with a generic error after 40m 59s. A separate pinned CPU replay exposed `SlugService.normalize` and `long`/`Integer` errors in the backend. Reviewed summary in `results/attempt-20261110-reviewed/summary.json` · Retry in `results/attempt-20261110-reviewed-retry/summary.json` · Recovery in `results/attempt-20261110-reviewed-recovery/summary.json` · Compact run in `results/attempt-20261110-reviewed-compact/summary.json` · Backend diagnostic in `results/attempt-20261110-backend-check-diagnostic/compact-backend-compile.json`

R5 showed the checkpoints operating. Backend compile passed after one repair. Frontend compile passed after two repairs. Full verification still found trusted API defects, including a malformed affiliate request returning 401 where the contract required 400. The run then rejected the first code-review chunk because a citation did not match `SecurityConfig.java` at its claimed line. That failure came from the reviewer handoff after Qwen had implemented and compiled the candidate. The workflow finished `failed` after 44m 12s. R5 summary in `results/r5-reviewed/summary.json`

The standalone reviewer comparison reused the same 45-file candidate and three source chunks. The retained review arms used a 65,536-token context with thinking off. The Gemma 31B QAT arm here is a historical reviewer trial. Gemma 31B IQ4_XS remained the test-author model in the recorded role configuration.

| Reviewer | Validated result |
| --- | --- |
| Gemma 4 31B QAT | Three chunks reviewed, 12 findings including two P0 and three P1. |
| Gemma 4 12B | Three chunks reviewed, 12 findings including one P0 and five P1. |
| Qwen 3.5 9B Q4_K_M | One of three chunks validated. Two failed exact citation checks, leaving an incomplete report and no accepted findings. |
| Gemma 4 26B Q4_K_M | Three chunks reviewed, five findings including three P1. |
| PPLX reconciliation of Qwen 9B and Gemma 12B | 29 claims across 11 batches, with one supported, one contradicted and 27 unresolved. The input retained Qwen's incomplete-coverage status. |

These are findings counts for one candidate under citation validation. They do not establish ranking by issue recall or accuracy. Gemma 26B became the reviewer in the repair follow-ups, while Qwen 27B at 98,304 `xhigh` remained the repairer. 31B QAT review in `results/r5-code-review/review.json` · 12B review in `results/r5-review-gemma12b/review.json` · 9B review in `results/r5-review-qwen9b/review.json` · 26B review in `results/r5-review-gemma26b/review.json` · PPLX reconciliation in `results/r5-review-pplx/reconciliation.json`

### Guided repair, CPU controls and alternative models, November 10–11

| Run | Retained result |
| --- | --- |
| `r6-reviewed-repair` | Gemma 26B review and Qwen repair ran. The first repair introduced a Java type mismatch between `UpdateResult` and `TrackedUrl`. The terminal journal marks the interrupted continuation `blocked` with unknown native completion. |
| `r7-reviewed-repair` | Recovered work encountered the Java compile failure again. `repair_2` violated the exact selected-path output contract, so its response could not be accepted. |
| `r8-reviewed-repair` | A repaired version compiled, but trusted affiliate validation still failed. One review passed and a later review was incomplete. Terminal status was `review_required`. |
| `r9-focused-repair` | The saved progress reached repair compile feedback after a second application repair. The initial backend and frontend checkpoints passed, then a later repair checkpoint failed. The retained run has no terminal summary. |
| `r10-storage-evidence` | The repair handoff included storage evidence. Its saved progress still showed trusted API and generated-test failures after the first repair. The retained run has no terminal summary. |
| `r11-verified-guidance` | The repair handoff included verified guidance. The first repair regressed backend compilation. The last saved progress reached compile repair. The retained run has no terminal summary. |
| `r12-exact-repair` | The third baseline passed both the pinned trusted checks and the frozen suite. Gemma 26B review then blocked on product SKU uniqueness. After `repair_3`, trusted checks still passed, but the original generated unittest failed. The run exhausted three repairs and ended `review_required` after 28m 53s. |

The passing R5 text-edit control and the R9 and R11 CPU verifier controls each passed backend packaging, frontend build and typecheck, trusted API and persistence checks, and the frozen Python suite in the pinned environment. They show that a known correction could pass the verifier on those saved inputs. Browser execution was unavailable in the image. A separate R12 diagnostic passed with the reporting fixture moved to a fresh SKU and an explicit duplicate-SKU `409` assertion. The original R12 frozen suite and terminal verdict stayed tied to the original input. R5 CPU control in `results/r5-text-edit-cpu-control-receipt.json` · R9 CPU control in `results/r9-cpu-verifier-control-receipt.json` · R11 CPU control in `results/r11-cpu-verifier-control-receipt.json` · R12 terminal summary in `results/r12-exact-repair/summary.json` · R12 fixture diagnostic in `results/r12-sku-fixture-diagnostic/verification.json`

Four quant variants were tried in targeted Commerce repair or review cases at a requested 65,536 context. Peak figures are total observed GPU VRAM during each initial test, including the pre-existing approximately 935 MiB baseline. They are observations for the installed machine and runtime profile.

| Model variant | Initial peak VRAM | Trial result |
| --- | ---: | --- |
| Ornith 1.5 9B Q8_0 | 10,784 MiB | Thinking-on case ended at its output length limit. Thinking-off follow-up returned an edit, but the edit failed compile and click verification remained inconclusive. |
| Ornith 1.5 9B BF16 | 17,896 MiB | Returned an edit that failed compile. Click verification was inconclusive. |
| Muse Glimmer 30B Meta Q4_K_M | 17,212 MiB | Initial review did not return parseable structured output. Follow-up returned a review that failed the source citation check. |
| Muse Glimmer 30B Dynamic Q4_K_XL | 19,974 MiB | Review did not return parseable structured output. |

The model-removal receipt records retirement of the four experimental variants. The focused screen did not produce a passing model repair or an accepted Glimmer review. Initial GPU results in `results/ornith-glimmer-20261111/gpu-results.json` · Follow-up GPU results in `results/ornith-glimmer-20261111-follow-up/gpu-results.json` · Model-removal receipt in `results/r12-model-removal/receipt.json`

### Grouped Commerce with medium implementation and xhigh repair, November 12

The full native comparison kept Qwen 27B at 98,304 context, used `medium` for implementation and `xhigh` for repair, and retained independent test authoring and the pinned verifier. The implementation manifest limit measures UTF-8 source bytes in each structured group response. Context and output-token budgets are separate.

| Run | Wall time | Retained result |
| --- | ---: | --- |
| Fresh medium run | 12m 20s | Group 2 exceeded the 24,576-byte manifest. Planning took 73s, independent tests 242s, grouping 146s, and the two implementation calls took 99s and 168s. |
| Same group resumed | 2m 43s | Group 2 again exceeded 24,576 bytes. |
| Budget-guided retry | 4m 03s | Group 2 again exceeded 24,576 bytes. |
| Regrouped attempt | 5m 38s at last saved progress | Planner reworked grouping and dispatched group 1. No terminal summary is retained. |
| 64 KiB group trial | 11m 01s | Progressed beyond the earlier size failure. Backend compile passed after one repair. Group 4 changed its frozen file set, so the response was rejected. |
| 64 KiB continuation | 88m 13s | Backend and frontend passed their compile checkpoints after three total compile repairs. All four full baselines failed after three final repairs. Trusted API registration, the persistence seed, and frozen unittest remained unresolved. No final code-review pass or delivery receipt was recorded. |

The 64 KiB continuation reached the expensive parts of the workflow. The final repair call took 893s and 50,301 output tokens, and its separate file-selection call took another 398s. The verifier reported HTTP 403 for registration in the first baseline and HTTP 500 in the three subsequent baselines where it expected HTTP 201. The larger manifest removed the earlier size barrier and allowed compilation, while the complete run still ended `review_required`. Fresh medium summary in `results/commerce-native-medium-full-20261112/summary.json` · Resume in `results/commerce-native-medium-full-resume-20261112/summary.json` · Budget retry in `results/commerce-native-medium-full-budget-20261112/summary.json` · Regrouped progress in `results/commerce-native-medium-full-regrouped-20261112/progress.json` · 64 KiB trial in `results/commerce-native-medium-full-64k-bytes-20261112/summary.json` · 64 KiB continuation in `results/commerce-native-medium-full-64k-continue-20261112/summary.json`

## OpenCode comparisons and native model selection

### Frontend implementation replay

The comparison replayed the seven-file Commerce `frontend_core` implementation against an existing plan, test suite, and prior source. Native generation returned one schema-constrained file manifest. OpenCode 1.19.0 received the scoped implementation in an isolated scratch worktree and used read and edit tools to write the approved files. Workbench applied the same intended path checks and pinned frontend verification. This was a partial-project experiment. Both OpenCode reports record `matched_inputs: false` because the OpenCode arm failed without a comparable accepted result, so the runs do not establish a controlled performance ratio.

| Arm | Settings | Observed time and token evidence | Result |
| --- | --- | --- | --- |
| Native baseline | Qwen 27B MTP Q4_K_M, 98,304 context, xhigh | 564.4 s generation, 9,757 prompt tokens, 30,145 generated tokens | All seven approved files. Frontend build passed. Typecheck failed. |
| OpenCode first attempt | Same model and reasoning, scoped worker v1, 32,000 output ceiling | 766.6 s wall. Backend logged 259 generated tokens on an early turn and 32,000 on the final turn. | Worker rejected completion under its step check. No accepted candidate or compile receipt. |
| OpenCode-only retry | Same model and reasoning, scoped worker v2, output ceiling raised to 49,152 and stage timeout raised to 1,320 s | 1,323.0 s wall. Backend logged completed evaluation counts of 14,470, 295, and 45,640 tokens. The last count is backend activity during the timeout, with no corresponding accepted worker receipt. | Timeout. The native baseline was reused and no frontend checks ran for the OpenCode arm. |

The early OpenCode worker ran with auto-compaction disabled. Its tool loop made multiple model requests and carried earlier messages and tool responses into later turns. The backend logs establish a long generation turn at the output ceiling in the first attempt and a wall-time cutoff in the retry. They do not separately count reasoning tokens or support a claim that every backend-generated token reached the caller. A smaller live OpenCode smoke had previously finished in 29.8 s at Qwen 27B xhigh and 98,304 context, with successful scoped reads and a write. That verified tool plumbing on a small example only.

Evidence in `results/opencode-group5-live-20261112/comparison.json`, `results/opencode-group5-retry-20261112/comparison.json`, the respective OpenCode `progress.json` files and backend logs, and `results/opencode-live-smoke-20261112/worker.json`.

### Native seven-arm implementation comparison

All seven arms replayed the same seven-file frontend group and stopped before repair. Wall time includes startup and any checks the arm reached. Generation time is recorded only for accepted in-scope output. Qwen 27B used 98,304 context. The Qwen 9B, Gemma 12B and Gemma 31B arms used 65,536 context with thinking on and off. Qwen 9B was Q4_K_M, and the Gemma 31B alias was IQ4_XS.

| Native implementation | Wall | Generation | Generated tokens | Scope and first checks |
| --- | ---: | ---: | ---: | --- |
| Qwen 27B medium | 185.9 s | 172.0 s | 11,523 | Seven files accepted. Build and typecheck failed. |
| Qwen 9B thinking off | 58.9 s | — | 4,598 | Returned unapproved frontend files. Checks skipped. |
| Qwen 9B thinking on | 121.3 s | — | 11,376 | Returned duplicate and unapproved paths. Checks skipped. |
| Gemma 12B thinking off | 69.6 s | — | 3,945 | Returned backend files outside the frontend scope. Checks skipped. |
| Gemma 12B thinking on | 902.4 s | — | — | No terminal model receipt. Backend unload confirmed. Checks skipped. |
| Gemma 31B thinking off | 136.0 s | 122.9 s | 3,614 | Seven files accepted. Build and typecheck failed. |
| Gemma 31B thinking on | 136.3 s | 127.9 s | 3,844 | Seven files accepted. Build and typecheck failed. |

The three in-scope drafts and the saved Qwen 27B xhigh native baseline qualified for the next test. Every new in-scope draft needed repair. Both Gemma 31B drafts were faster to produce than the Qwen 27B medium draft, but draft time alone did not predict time to a passing frontend.

Evidence in `results/native-implementation-seven-20261112/comparison-intent.json` and `comparison.json` in the same folder.

### Four targeted frontend repairs

Each saved draft received Qwen 27B MTP Q4_K_M at 98,304 context and xhigh through native scoped text edits. The starting compiler receipt was reused. The comparison allowed up to two repair attempts and stopped after the pinned frontend build and typecheck. Total time combines the recorded draft and repair wall times.

| Starting draft | Draft wall | Repair wall | Total wall | Repair attempts | Verified build and typecheck |
| --- | ---: | ---: | ---: | ---: | --- |
| Qwen 27B medium | 185.9 s | 192.7 s | 378.6 s | 1 | Passed |
| Gemma 31B thinking off | 136.0 s | 425.4 s | 561.4 s | 1 | Passed |
| Gemma 31B thinking on | 136.3 s | 798.4 s | 934.7 s | 2 | Passed |
| Qwen 27B xhigh baseline | 579.0 s | 420.3 s | 999.3 s | 1 | Passed |

All four original run rows were recorded as failed with `Invalid artifact identity` after the passing checks, when the harness tried to write a checkpoint artifact. The reconciliation used retained patch and pinned compiler receipts to verify each final candidate and both checks. The historical run statuses remain failed. Qwen medium followed by Qwen xhigh repair was the fastest verified pair in this comparison, about 6m 19s, versus 16m 39s for the saved xhigh draft plus repair. This was evidence for selecting medium implementation and xhigh repair in the next Commerce run. It measured a frontend slice and did not measure full app correctness.

Evidence in `results/native-frontend-repair-four-20261112/comparison-intent.json` and `comparison.json`, and the reconciled `results/native-frontend-repair-four-verified-20261112/comparison.json`.

### Scoped OpenCode repair and reviewer evaluation

A mixed-review OpenCode trial reached two accepted repairs and multiple verification passes, then regressed in verification. It was stopped after a later mixed review selected authentication files for an unrelated product regression. The trial lasted 2,411.8 s and has no receipt for the third repair. It did not establish a completed Commerce outcome.

A separate matched SKU-only replay used OpenCode with Qwen 27B at 98,304 context and xhigh. The session finished in 302.9 s and changed only `ProductController.java` and `Product.java`. Pinned compile and trusted click checks passed. The original frozen suite still had a reporting-fixture error. An explicitly diagnostic scoring run, using a corrected reporting fixture with the same candidate, passed 11 tests. The native control under the corresponding diagnostic fixture passed compile and trusted click checks but failed the assertion that explicit `active=false` survives product creation. The diagnostic pass measures the narrow repair. The unchanged frozen suite remains the acceptance evidence.

The reviewer path has a read-only, structured OpenCode worker and a CPU smoke. The saved comparison runner specifies Gemma 26B Q4_K_M at 65,536 context with thinking on and a 16,384 output cap for both native and OpenCode review. No scored live reviewer-versus-native comparison was found in the retained results, so the reviewer experiment provides no timing or quality ranking.

Evidence in `results/opencode-sku-live-20261112/summary.json` and `operator-stop.json`, `results/opencode-sku-only-20261112/summary.json`, and the `verification.json` files in `results/opencode-sku-only-frozen-20261112`, `results/opencode-sku-only-score-20261112`, and `results/opencode-sku-check-native-control`. Reviewer experiment definitions are in `runner-reviewed/opencode_review_comparison.py` and `runner-reviewed/opencode_reviewer_smoke.py`.

## Full Commerce with whole-project OpenCode, November 12

The experiment used one flat plan and one implementation session. Qwen 27B MTP Q4_K_M used medium reasoning at 98,304 context for implementation, smoke-test authoring and the first self-repair. Gemma 31B IQ4_XS used thinking at 65,536 context to write the independent tests. Qwen 27B used xhigh at 98,304 for subsequent application repairs. The final review was assigned to Gemma 26B Q4_K_M with thinking. OpenCode 1.19.0 ran implementation, test authoring and repairs with auto-compaction. The RTX 3090 logs showed all Qwen and Gemma 31B layers offloaded to the GPU at the trial settings. The monitor displayed reasoning and tool events from the active workers.

| Run | Elapsed | Result |
| --- | ---: | --- |
| `whole-opencode-live-20261112` | 29m 32s | The planner returned a 62-file inventory. OpenCode produced 133,840 bytes of source, which exceeded the 131,072-byte aggregate allowance. The worktree was captured as an unverified draft. |
| `whole-opencode-live-validation-20261112` | 2m 13s | The draft continuation blocked at the first smoke-test authoring call because native completion was unknown. There was no accepted test receipt. |
| `whole3` | 63m 19s | A separate draft continuation reached Docker. Self-repair cleared frontend type errors. Trusted concurrent clicks and the independent suite still failed. Qwen's first xhigh repair stopped at its per-turn output ceiling on step 11. |
| `whole-hardware-rerun-20261112` | 42m 54s | Fresh 56-file implementation completed and crossed the previous source and generation limits. The run stopped on self-test coverage metadata before sandbox verification. |
| `whole-hardware-validation-20261112` | 120m 06s | Continuation from the fresh implementation and existing smoke-test source reached five Docker verification passes. The remaining two-hour overall deadline expired during the third xhigh repair. |

The capacity change raised the whole-project worker output ceilings to the approved context sizes, removed extra whole-project step and stage-time limits, and derived the source budget from 128 files at up to 60,000 UTF-8 bytes per file. Context still counts both input and generated tokens. The 16 KiB and 15-file Commerce test contract and two-hour overall run deadline were still in force.

The fresh implementation completed in 1,878.8 seconds and generated 110,305 tokens across 55 model turns. Its largest response generated 36,014 tokens, above the former 32,000-token ceiling. OpenCode recorded a successful compaction followed by more turns. All 56 planned paths were accepted and the source total was 146,094 bytes. The first self-test response incorrectly returned application source in the test field. The retry produced a 7,302-byte smoke test with five methods. Two coverage entries listed multiple existing methods separated by slashes. The validator rejected those entries. A narrow validator change resolves each named method uniquely in the declared file. A fresh approval imported the verified implementation and unchanged smoke-test source, validated the metadata, and ran sandbox checks anew.

| Continuation stage | Inference elapsed | Generated tokens | Observed result |
| --- | ---: | ---: | --- |
| Initial implementer sandbox check | — | — | Vue template parse errors and Java compile errors in `SecurityConfig.java` prevented application execution. |
| Implementer self-repair | 322.6 s | 15,871 | Four-file patch. Java compile and packaging passed. Vue errors remained. Trusted API checks reached malformed-request and concurrent-click failures. |
| Independent test author and retry | 270.1 s + 227.4 s | 7,812 + 6,933 | Gemma corrected a rejected suite submission. Two files totaling 10,936 bytes passed syntax preflight and were frozen. The first independent execution reproduced the outstanding compile and API failures. |
| Qwen xhigh repair 1 | 1,727.3 s | 81,173 | Four-file patch. Frontend build passed and malformed requests cleared the trusted check. TypeScript `TS2322`, concurrent clicks, and generated tests still failed. |
| Qwen xhigh repair 2 | 3,165.5 s | 159,753 | Two-file patch. Frontend typecheck, both builds, packaging and persistence passed. Concurrent clicks still failed. The independent suite ran nine tests with six passes, two failures and one error. |
| Qwen xhigh repair 3 | 1,175.3 s | Unavailable | The overall deadline expired with no accepted patch or third recheck. Worker cleanup and backend unload were confirmed. |

The last trusted API receipt passed two of three tests and failed the concurrent-click check because every click had to succeed. The independent Python suite saw HTTP 500 from reporting and tracked-URL operations. Its UI runner used an undefined `cls` in an instance method. One UI method only prepared JavaScript before returning, and the other used a placeholder assertion. The pinned image had no browser or driver. Source edits could address the API failures, while independently authored test code remained frozen. No full code-review receipt, final smoke recheck or delivery was produced. The fresh run and continuation consumed 2h 43m of active time in total.

Evidence under `results/` includes each named run's `summary.json` and `terminal.json`, the fresh `implement.raw.json`, the continuation's `build_verify_0.raw.json` through `build_verify_2.raw.json` and `repair_3.raw.json`, and the matching backend cleanup receipts.

## Commerce frontend model comparison, November 13

The local arms replayed the same seven-file frontend request on the RTX 3090 through native Ollama, using 65,536 context, Q8 KV cache, and non-thinking generation. Each got one implementation response. Wall time includes the checks an arm reached and excludes downloads. System RAM figures are Ollama's reported model allocation outside the GPU. The Terra comparisons used the same approved file scope and compiler checks. Their inference time and token counts were unavailable.

| Implementation | Approved files | Wall | Generated tokens | Decode | System RAM offload | Build / typecheck |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Coder-Next UD-IQ1_M | 7/7 | 64.6 s | 4,071 | 110.5 tok/s | 0 GiB | Failed / failed |
| Coder-Next UD-IQ2_XXS | 7/7 | 68.3 s | 3,382 | 89.3 tok/s | 1.24 GiB | Failed / failed |
| Coder-Next Q2_K | 2/7 | 44.6 s | 889 | 60.7 tok/s | 6.91 GiB | Unrun |
| Coder-Next UD-IQ3_XXS | 7/7 | 94.8 s | 3,062 | 52.3 tok/s | 6.14 GiB | Failed / failed |
| Coder-Next UD-IQ4_XS | 0/7 accepted | Stopped at 29m 21s | 36,253 at last backend sample | 24.8 tok/s at last sample | 15.47 GiB | Unrun |
| GPT-6.1 Terra medium | 7/7 | 10.5 s compile only | — | — | — | Passed / passed |
| GPT-6.1 Terra high | 7/7 | 8.7 s compile only | — | — | — | Passed / passed |

The three complete Coder-Next drafts had an invalid `@mdi/font` import. Their type errors included Vuetify configuration, Vue Router types, or missing shared exports. Q2_K completed with only two approved files. IQ4_XS was stopped during generation with no complete response accepted. The compiler receipts for Terra cover the seven-file slice. The experiment did not measure browser behavior.

Qwen 27B MTP Q4_K_M repaired the original IQ1 and IQ2 drafts at 98,304 context and xhigh. The first repair made both builds pass, with Vuetify type errors remaining. IQ1 took 550.8 s for repair and recheck at 51.8 tok/s, or 615.4 s including the draft. IQ2 took 368.2 s at 52.1 tok/s, or 436.5 s including the draft. An IQ1 second repair had already started when the comparison was narrowed to one pass. It passed both compiler checks in 779.6 s total. The IQ2 first compiler receipt survived backend shutdown, after which the runner recorded a terminal error.

### Same-model drafting and repair

Fresh Coder-Next responses at 65,536 context passed native generation but failed exact file scope. The runner dispatched no repairs for those drafts. Earlier seven-file drafts from each quant then received one scoped same-model repair at 98,304 context.

| Quant | Fresh approved / extra files | Fresh wall / decode | Saved draft repair wall / decode | Repair outcome |
| --- | --- | --- | --- | --- |
| UD-IQ1_M | 0/7, 8 extra | 288.3 s / 106.3 tok/s | 275.5 s / 106.5 tok/s | Patch edited four out-of-scope files. Checks unrun. |
| UD-IQ2_XXS | 5/7, 1 extra | 266.1 s / 88.5 tok/s | 254.8 s / 85.3 tok/s | Patch included an unchanged edit. Checks unrun. |
| UD-IQ3_XXS | 6/7, 0 extra | 291.9 s / 57.7 tok/s | 307.7 s / 54.5 tok/s | Patch applied. Build and typecheck still failed. |

Model loads took about 207–239 s in this later trial. Both the scope failures and the retained-draft repair results matter when comparing decode speed with elapsed time. The earlier in-scope drafts show that a valid response was possible. The fresh attempts show the one-call workflow had no path to correct an invalid file set before trying repair. All six trial backends confirmed unload and exited.

Evidence in `results/coder-next-frontend-comparison-20261113.md`, `results/coder-next-frontend-repair-comparison-20261113.md`, the named quant comparison receipts, and the two Terra compiler summaries.

## Commerce reference verification and independent tests, November 13

The native full-project trials added trusted held-out checks to the pinned CPU image and ran generated tests against the reference before freezing them. The first CPU check exposed missing Vite environment types in the reference and a product timestamp assertion that compared MongoDB millisecond precision with the initial nanosecond response. The CPU check passed after those corrections. Five standalone reference checks passed on the subsequent reference and image revisions. The final pinned image passed all ten required checks. A separate jsdom smoke passed Login and Dashboard component checks, signed in against the real API, and navigated to a seeded product. The image has no browser or browser driver, so visual layout was unevaluated.

Qwen 3.8 27B MTP Q4_K_M planned at 98,304 context with medium reasoning. Gemma 4 31B IQ4_XS wrote and repaired the independent tests at 65,536 context with thinking enabled. The prepared workflow also assigned Qwen 27B medium implementation, Qwen 27B application repair with medium first and xhigh escalation, and Gemma 4 26B A4B MTP Q4_K_M review with thinking. These full-project attempts ended before implementation, application repair, or review.

### Coverage and the 16 KiB suite limit

Early attempts exposed missing test files, invalid discovery filenames, and coverage references to absent methods. Coverage repair was updated to treat unsupported claims as uncovered and request executable additions. Other blockers included an undeclared npm invocation, a harness file error, and one coverage request whose full prompt and schema did not fit the test author's context. Separate attempts hit the 16 KiB UTF-8 source limit during initial authoring, defect repair, and coverage addition. The source-size limit and context admission are separate checks.

One reference control passed 21 of 24 tests, with all three remaining UI failures caused by an unwritable Vite cache. A later control passed 13 of 14 tests, with a Vitest worker exit in the remaining check. Neither suite froze. The Commerce trial then raised the independent suite limit to 32 KiB. The whole-project OpenCode trial described above used its separate 16 KiB contract.

### Four runs at 32 KiB

| Run | Elapsed | Last generated suite on the reference | Terminal outcome |
| --- | ---: | --- | --- |
| CV5 | 22m 59.6s | 13 of 16 passed | Two wrappers pointed to missing Login and Dashboard specs. The third spec failed to load Vuetify CSS. Capturing stderr in a CPU replay exposed both causes. |
| CV6 | 24m 30.8s | 18 of 21 passed | The new pinned Vitest runner exposed an assumed app factory, private view paths and brittle assertions. Login, CRUD and Reports UI still failed after two rewrites. |
| CV7 | 15m 52.6s | 6 of 10 passed in the first control | All three UI checks imported unavailable Testing Library. An API report assertion selected another test's row. Test docstrings hid Vitest tracebacks from the repair prompt. The first rewrite introduced a Python syntax error before a second control. |
| CV8 | 26m 12.1s | 11 of 12 passed | Login, Dashboard navigation, both Reports UI checks and all seven generated API tests passed. Product creation failed after the second rewrite. The reference gate stopped the run and the isolated model backend confirmed unload. |

The local runner now accepts complete Vitest source on stdin, uses the app's Vite configuration with jsdom and Vuetify support, and carries combined output into the Python test receipt. Shared DOM helpers bootstrap the public Vue entry and interact with rendered controls. The workflow now preserves the failure headline for unittest cases with docstrings and routes a defect rewrite with invalid Python through bounded syntax repair before freezing.

The final CV8 suite ran in 15.0 s and used 17,680 bytes across three source files, within the 32 KiB limit. Its product creation test hit a missing jsdom `visualViewport` API when opening the Vuetify dialog and expected an exact `Name` label where the reference renders `Name *`. The earlier sign-in and navigation smoke never opened that dialog. The proposed diagnostic is a CPU replay of the saved suite with viewport support and label matching that tolerates the required marker, followed by a check that saving a unique product closes the dialog and renders the persisted row. The remaining failure supplies no evidence for raising the suite limit to 64 KiB. No candidate was generated in these full-project attempts.

Evidence in the `cv5` through `cv8` run summaries and reference control receipts, `ui-diagnosis/report.json`, `vui-77f18706/report.json`, and the final CPU reference report under `reference-image-check-20261113T201745`.
