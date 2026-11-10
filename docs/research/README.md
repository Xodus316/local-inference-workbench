# Model selection on a 24 GB RTX 3090

The defaults below are engineering choices based on the specific workloads and checks described here. Read the [versioned synthetic evidence bundle](../../benchmarks/evidence/v1/README.md) alongside the reports. It contains frozen exercise material, archived outcomes and recorded experiment settings without operational service data.

Run `python scripts/verify-benchmark-evidence.py` from the repository root to check file integrity and reconcile the public measurement tables. This offline command does not run inference or execute generated code. Recomputing a historical score also requires its original grader and environment; unavailable assets are marked explicitly. A newly authored grader defines a separate benchmark and must not be pooled with historical pass rates.

## Benchmark results

| Question | What was measured |
|---|---|
| [Which coding models returned working programs?](implementation-screen.md) | 198 independent attempts across six Python exercises. Whole-program pass rates by model, partial successes, output-limit failures and the result after a second model reviewed the draft. |
| [Which models wrote tests that caught defects?](test-authorship.md) | First submissions on occupancy, cache expiration, stock allocation and interval union. Control passes, detection rates and examples where a seemingly sensible test exercised the wrong behavior. |
| [Could a model strengthen a frozen suite?](independent-testing.md) | Seven model/package configurations on combat simulation, with the original nine-fault corpus. First submissions, guided additions and evaluator-assisted outcomes are identified individually. |
| [Who should plan, implement and repair a Build?](coding-comparisons.md) | Readiness on blinded planning briefs, independent execution checks on two combat implementations, and the effect of running Qwen xhigh only after a reproduced failure. |
| [When did a second reader help?](reasoning.md) | All-checks pass rates, individual rubric checks and adjudication mistakes in summaries, research queries and the Verified chain. |

## Selected roles

| Use case | Preferred models | Basis |
|---|---|---|
| Fast / Auto | Gemma 12B Q4_K_M | Complete FIFO, extraction and synthesis checks with the lowest combined latency. |
| Balanced | Gemma 12B Q4_K_M and Qwen 9B Q4_K_M, with Gemma synthesis | Independent readers caught different details. The smaller pair was substantially faster across the full chain. |
| Optional research | Qwen 9B Q4_K_M for queries, Gemma 12B for synthesis | Qwen followed the preference for primary documentation and kept private identifiers out of search strings. |
| Verified | Both Q4 readers followed by the local PPLX decision adapter | The decider selected a more complete clean-code explanation twice. On another brief it selected a weaker answer. Sequential operation fit the measured hardware. |
| Deep | Gemma 31B QAT | Existing difficult-reasoning profile. Direct fallback accuracy remains an open measurement. |
| Standalone planning | Gemma 26B A4B MTP Q4_K_M | Four ready plans across two repetitions of the same two briefs. |
| From-scratch Build | Gemma 26B Q4 plans, Gemma 31B IQ4_XS writes tests, Qwen 27B xhigh implements and conditionally repairs | The current coding roles share a 98,304-token profile. Earlier Coder-Next comparisons remain historical evidence. |
| Existing-repository edits | Gemma 12B investigation, Qwen 27B patch, Gemma 31B QAT review | A live edit exercise verified the whole path through a working patch and execution checks. |
| Interactive chat | Gemma 12B initially, with manual model selection | Responsive text results and functional checks for history, uploads, model switching and stopping. |

- [Reasoning, independent readers, search and adjudication](reasoning.md) gives check counts and timing, explains model placement, and traces individual decision mistakes.
- [Planning and code edits](planning-and-edits.md) examines exact arithmetic, incomplete specifications, coding roles and test sensitivity.
- [Chat and operating a single GPU](chat-and-operation.md) records functional checks and explains what the runtime measurements mean.
- [Per-call measurements](measurements.csv) contains selected anonymized reasoning, query-writing and Python-review observations. In the two code exercises, A contains an actual dictionary mutation defect and B iterates a safe copy of the keys.

The selection experiments used local inference and included loading where wall time is reported. Short probes establish feasibility. Blinded scoring of answers, comparisons against trusted implementations, and deliberate fault injection answer different questions about quality. Sample sizes and settings are given with each result.

The [Build role comparison](planning-and-edits.md#from-scratch-build) covers initial coding, test authorship and repairs. The [GLM and Nemotron coding screen](interval-union.md) compares three additional local packages on independent implementation and test design.

## Evidence limits by role

| Selection | What the evidence establishes | What remains unmeasured |
|---|---|---|
| Primary and second reader | Quality and latency on fixed synthetic source-reading exercises; candidate and final outcomes in a small paired comparison | General document quality, alternative model pairs and reversed synthesis roles |
| Query writer | Four criteria on one public-search query-writing prompt | Retrieval relevance and final-answer accuracy after search |
| Planner | Structured-plan readiness on two briefs repeated across comparisons | Breadth across unrelated projects and average full-Build benefit |
| Editor and reviewer | Standalone implementation and review-with-rewriting results; one small live Edit check | Verdict-only review accuracy, false approvals and matched multi-file edit performance |
| Build roles | Independent controls, seeded faults and held-out outcomes on a small number of programs | Reliability on unseen specifications and the average cost of repairs |
| PPLX and Deep | Recorded decision choices and routing frequency | Comparative decider superiority and direct Deep answer quality |
| Chat | Earlier synthetic functional checks and example latencies | A matched comparison of current Workbench chat answer quality |

Exact templates, parser paths and quantizations are part of the deployable package. Where immutable identity was not retained, the archive records that absence instead of treating a mutable local model tag as a reproducible identifier.
