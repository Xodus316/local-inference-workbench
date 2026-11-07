# Model selection on a 24 GB RTX 3090

| Use case | Preferred models | Basis |
|---|---|---|
| Fast / Auto | Gemma 12B Q4_K_M | Complete FIFO, extraction and synthesis checks with the lowest combined latency. |
| Balanced | Gemma 12B Q4_K_M and Qwen 9B Q4_K_M, with Gemma synthesis | Independent readers caught different details. The smaller pair was substantially faster across the full chain. |
| Optional research | Qwen 9B Q4_K_M for queries, Gemma 12B for synthesis | Qwen followed the preference for primary documentation and kept private identifiers out of search strings. |
| Verified | Both Q4 readers followed by the local PPLX decision adapter | The decider selected a more complete clean-code explanation twice. On another brief it selected a weaker answer. Sequential operation fit the measured hardware. |
| Deep | Gemma 31B QAT | Existing difficult-reasoning profile. Direct fallback accuracy remains an open measurement. |
| Standalone planning | Gemma 26B A4B MTP Q4_K_M | Four ready plans across two repetitions of the same two briefs. |
| From-scratch Build | Gemma 26B Q4 plans, Gemma 31B IQ4_XS writes tests, Coder-Next IQ1 implements and Qwen 27B xhigh repairs | Measured first-pass speed, independent controls and reproduced failures guided the split. |
| Existing-repository edits | Gemma 12B investigation, Qwen 27B patch, Gemma 31B QAT review | A live edit exercise verified the whole path through a working patch and execution checks. |
| Interactive chat | Gemma 12B initially, with manual model selection | Responsive text results and functional checks for history, uploads, model switching and stopping. |

- [Reasoning, independent readers, search and adjudication](reasoning.md) gives check counts and timing, explains model placement, and traces individual decision mistakes.
- [Planning and code edits](planning-and-edits.md) examines exact arithmetic, incomplete specifications, coding roles and test sensitivity.
- [Chat and operating a single GPU](chat-and-operation.md) records functional checks and explains what the runtime measurements mean.
- [Per-call measurements](measurements.csv) contains selected anonymized reasoning, query-writing and Python-review observations. In the two code exercises, A contains an actual dictionary mutation defect and B iterates a safe copy of the keys.

The selection experiments used local inference and included loading where wall time is reported. Short probes establish feasibility. Blinded scoring of answers, comparisons against trusted implementations, and deliberate fault injection answer different questions about quality. Sample sizes and settings are given with each result.

The [Build role comparison](planning-and-edits.md#from-scratch-build) covers initial coding, test authorship and repairs. The [GLM and Nemotron coding screen](interval-union.md) compares three additional local packages on independent implementation and test design.
