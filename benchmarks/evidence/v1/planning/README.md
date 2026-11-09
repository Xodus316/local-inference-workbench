# Synthetic planning evidence

Two self-authored briefs test exact credit allocation and meter-usage aggregation. These are synthetic programming exercises. They contain no customer/task-platform material. All results are historical first submissions; creating this archive made no model calls.

| File | Contents |
|---|---|
| [apportion.txt](apportion.txt) | Exact standalone credit-allocation brief. |
| [meter.txt](meter.txt) | Exact standalone meter-aggregation brief, including the reset-policy ambiguity. |
| [rubric.json](rubric.json) | Frozen five-dimension, zero-to-two-point human rubric and readiness rule. |
| [results.json](results.json) | Twenty-four plans, their archived reviews, recorded generation/timing values and aggregates from five comparisons, plus three separately labeled inference-fit probes. |

## Reading the evidence

Each comparison uses one fresh submission per model and brief. The same two briefs recur across comparisons; eight ready Gemma 26B Q4 plans are eight submissions over two distinct tasks, not eight independent tasks. The file preserves every compared plan, including non-ready plans and their blocking defects. Guided corrections and implementation repairs are not part of these planning scores.

A ready plan needed a valid terminal response, at least 8/10, credit in every rubric dimension and no blocking defect. Reviewers checked numerical examples before seeing model identities and timings. These are archived human judgments. Independent readers can regrade the exported final plans against the exact briefs and rubric. Aggregating the existing scores is not the same as executing the planned software.

The normalized `score` and `score_maximum` fields have consistent meanings across comparisons. Nested `human_review` fields retain their recorded grading vocabulary: an early review's `total` is its earned score, while later reviews use `score` or dimension arrays. A null `schema_valid` means the selected source record did not provide that explicit field; it is not a failed-validation claim.

All recorded planner profiles used thinking on, 32,768 context, uncapped output, temperature 1 and top P 0.95 with the repetition guard. The four Gemma comparisons set top K to 64. The fifth GLM/Nemotron cohort explicitly left top K unset, using each native package default; it is labeled separately and must not be described as an identical-sampler comparison. Inference used an RTX 3090 with 24 GiB VRAM. Elapsed values include loading and transport; warm residency matters. The first IQ4 comparison began with that model warm. Q4 versus Q5 also compares different deployable package provenance, rather than isolating quantization alone.

## Provenance and limits

The first Gemma 26B versus 31B IQ4 pilot had a source-provenance gap: its deployed revision could not be retrieved for source review, although recorded bundles/settings were checked. This limits source reproducibility; the saved plan and grade remain inspectable. The repeat is a separate comparison and retains its different outcomes.

Machine-local model aliases, private source revisions, service bindings, operational request/run identifiers and raw thinking are withheld. Generic model labels replace those aliases; an unavailable immutable artifact identity is explicitly noted in `results.json`. The final plan fields, human review content, scores, readiness decisions and recorded numeric metrics are retained through explicit field allowlists.

The briefs are exact exported text. The JSON is a reserialized excerpt and does not claim byte identity with original dispatch requests. Complete historical orchestration prompts, rendered templates and native envelopes are unavailable here, so this archive supports independent assessment of plans and audit of totals, rather than automatic reproduction of the original generations.

The original Gemma 26B Q4 eight-ready-submission figure still covers four comparisons and two distinct briefs. A fifth comparison adds six first-pass plans from GLM Flash Q4, GLM Flash Q5 and Nemotron Lightning IQ4_XS. They scored 12/20, 9/20 and 9/20 respectively; no alternative qualified on both briefs. The saved plans and rubric explain their blocking defects. Three separate unscored fit probes show full native-model GPU allocation at 32K, which is neither a whole-board VRAM peak nor evidence of planning quality.
