# Reader, synthesis, query and decision evidence

This archive contains only self-authored synthetic reasoning and Python exercises. The corrected notes use invented people, dates and counts. They are test fixtures, not customer records. Benchmark values are historical observations; no new inference was run to create this archive.

| File | Contents |
|---|---|
| [reader-fixtures.json](reader-fixtures.json) | Six selected cases with model-visible messages, output schemas and frozen human rubrics. |
| [reader-results.json](reader-results.json) | All 24 model/case records behind the public per-call CSV, with final answers, grades, native finish reasons, timing and token counts. |
| [model-packages.json](model-packages.json) | Whitelisted package identities, quantization, template/parser metadata and recorded Ollama version. |
| [verified-fixtures.json](verified-fixtures.json) | Three scored synthetic briefs, their source records and rubrics, plus the unscored full-chain smoke case. |
| [verified-results.json](verified-results.json) | Fifteen requested scored workflows across two campaigns, including the original two admission failures. Candidate/final answers, decision choices, grades and condition totals are retained. |

## What can be checked

The recorded check vectors, scores and timing values can be independently aggregated. The 24 reader records match the existing public CSV without a numerical mismatch. The fixtures and saved final outputs permit independent human regrading. Schemas establish output shape; they do not establish factual correctness. Human grades are archived judgments, not outputs of an executable accuracy grader.

Reader profiles used thinking on, 32,768 context, no separate output cap, temperature 1, top P 0.95 and presence penalty zero. Top K was 64 for Gemma and 20 for Qwen. Request elapsed time includes loading and transport. Native text generation-only timing was unavailable in that campaign. The Qwen Q4 package's native presence penalty was 1.5; the experiment explicitly overrode it to zero.

The six generic cases are a selected subset of a larger nine-case text campaign. Preliminary planning, report comparison, functional-evidence and image packets are not included here. Broader campaign call totals are clearly separate from the 24 selected records. Each model answered each case once. Results do not establish statistical superiority or a general accuracy rate.

## Fixture identity and sanitization

The public-query-drafting case originally contained two deliberately invented private-looking strings as a non-disclosure test. This export replaces the invented local file string with `EXAMPLE_LOCAL_FILE` and its invented marker with `EXAMPLE_PRIVATE_MARKER`. The rest of that case's instructions, schema and rubric are retained. The change is declared in the fixture. Its historical grade remains an archived result on the original synthetic input; the sanitized fixture is an equivalent demonstration, not a byte-identical replay.

JSON excerpts are reserialized. Hashes for files in this bundle identify the exported files. They must not be described as the original dispatch/request hashes. Complete historical native requests, operational identifiers, private configuration, raw thinking and authenticated transport envelopes are excluded. Final-answer explanations and source citations are retained as scored output.

## Decision and scheduling limits

The original campaign requested nine scored workflows and delivered seven. Two independent siblings timed out before dispatch; those failures remain in coverage. Available candidates recovered from those failed runs had an identity-disclosed grading limitation. The follow-up delivered all six scored answers after the admission fix. Its two full-chain smoke runs are excluded from scored totals.

PPLX categorical choices are not truth grades. The alias identifies a local 32K decision profile; the exact immutable decision-model artifact is unavailable in this export. No alternative decider comparison was performed. Parallel-requested scheduling did not demonstrate simultaneous GPU token generation. An extra synthesis call, output length and warm weights confound the timing comparison.

Deep was invoked zero times in the thirteen delivered scored answers across the campaigns. These records cannot establish direct Deep answer quality. Chat quality is also outside this bundle: the public chat report describes earlier synthetic functional checks, rather than a comparative chat benchmark.

Fresh inference requires compatible packages, schema handling, prompt rendering and decision protocol. This archive does not recreate the original orchestration service or prove that another runtime will reproduce the same answers.
