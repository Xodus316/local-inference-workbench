# Synthetic model-selection evidence, version 1

This bundle accompanies the [model-role choices](../../../README.md#model-choices-by-role) and [research reports](../../../docs/research/README.md). It preserves self-authored software and reasoning exercises, recorded outcomes and the evidence available for assessing those choices. Adding it made no new model calls and did not change the historical scores.

## Contents and coverage

| Archive | Included evidence | What can be reproduced or inspected |
|---|---|---|
| [Reasoning](reasoning/README.md) | Six reader fixtures and rubrics, all 24 reader calls, package metadata, three Verified briefs and all 15 requested workflows across two campaigns | Reconcile scores with the public CSV, inspect final answers and decision outcomes, and regrade synthetic answers manually. Two admission failures remain included; 13 final workflows completed. |
| [Planning](planning/README.md) | Exact allocation/meter briefs, readiness rubric, final plans and recorded reviews/settings | Regrade plans against the rubric. The eight ready Gemma 26B Q4 submissions repeat two distinct briefs. |
| [Coding and review](coding/README.md) | Six frozen contracts with visible checks/support files, all 198 draws, 36 planned review pairs, 68 draft/revised sources and 15 transaction outcomes | Reconcile retained grades and inspect review transitions. The original hidden coding/transaction graders are unavailable here. |
| [Build and test authorship](build/README.md) | Occupancy/cache specifications, controls, verifiers, faults/witnesses and saved suites; assisted inventory material; combat observations | Controls and test assertions are inspectable. Available saved programs can be evaluated in containers. Missing combat artifacts and assisted outcomes are labeled. |
| [Interval union](interval/README.md) | Exact contract, two controls, 29-case evaluator, seven faults and all six submitted sources | Regrade saved implementations and independent tests using the opt-in Docker replay. |

The domain READMEs describe exact coverage, exclusions and unavailable identities. Some artifacts are intentionally invalid source: preserving a failed first submission is part of the accounting. Archived human grades are judgments against a frozen rubric; they are not an automated proof of correctness.

## Offline archive checks

From the repository root, using Python 3.11 or newer:

```sh
python scripts/verify-benchmark-evidence.py
```

The standard-library verifier checks every file against [manifest.json](manifest.json), reconciles all 198 coding records and 24 reader records with the public CSVs, checks their cohort denominators, and checks selected planner/cache selection totals. It does not call a model, access the network, import archived submissions or execute their code. It does not recompute every historical grade. The manifest detects accidental content drift; it is not a signature or an independent attestation of the experiment.

The bundle uses UTF-8 and LF line endings so hashes are stable across checkouts. A manifest update represents a deliberate archive change and must accompany review of any changed evidence; refreshing hashes alone does not establish a new score.

## Optional interval score replay

This command requires Docker and an already installed Python image. It verifies archive integrity first, qualifies the controls and seven faults, then grades all six saved submissions. It never downloads an image or runs inference.

```sh
docker image inspect python:3.13-alpine
python scripts/replay-interval-benchmark.py --image python:3.13-alpine
```

Generated code runs only in disposable containers with networking disabled, a read-only project/root, an unprivileged user, memory/process/CPU limits, bounded temporary storage and execution deadlines. Cleanup is checked after each container. The host treats submission source as data. Do not execute the archived source directly or include it in ordinary repository test discovery.

The report prints the actual immutable image ID and architecture. Historical scoring ran in Docker on an ARM64 Mac, while inference used an RTX 3090. The original records retain the image ID but do not separately identify the container architecture. A replay records its own environment; matching grades do not reproduce historical model latency. The replay never rewrites archived outcomes when it disagrees with them.

## How the evidence supports the defaults

- Gemma 12B Q4 combines complete checks on three reader exercises with low observed latency. Qwen 9B Q4 supplies independent reading and met all four query-writing criteria on its one prompt.
- Gemma 26B Q4 repeatedly returned ready plans on the two briefs. Faster 31B IQ4 plans lost a readiness case on the repeat. Later alternative screens remain small, separately labeled comparisons.
- Qwen 27B and Gemma 31B have complementary standalone coding results. The review experiment allowed code rewriting; the deployed Edit reviewer only returns an approval verdict. Its false-approval and missed-defect rates remain unmeasured.
- Gemma 31B IQ4_XS produced a successful first-pass cache suite against two controls and ten faults, while other suites needed assistance or could not be scored. Occupancy and inventory failures remain visible.
- Coder-Next provides a fast initial Build draft, with Qwen xhigh reserved for reproduced failures. One matched strengthened-suite comparison supports the observed time saving, rather than a universal speed or reliability claim.
- PPLX has recorded decision outcomes but no matched alternative-decider comparison. Direct Deep accuracy and comparative current-Workbench chat quality remain unmeasured.

## Provenance and sanitization

Exports use explicit field and file allowlists. They retain final answers, submitted source, grades, metrics and available immutable fingerprints. Actual customer assignments, real review/website replay packets, browser captures, credentials, authenticated envelopes, service addresses, operator paths, operational run identifiers, caller bindings and raw model thinking are excluded.

One synthetic query fixture replaces an invented local-file marker and private-looking example path with neutral placeholders. The domain metadata records this transformation. Historical grades are preserved, but the transformed prompt is not represented as a byte-identical original request. Original artifact hashes and newly exported file hashes have distinct meanings.

Missing evidence remains explicit:

- The original 475-check coding grader and 202-history transaction validator were held separately and are unavailable in this checkout. The newer independently written grader defines a different benchmark and cannot replace or be pooled with those historical grades.
- The first 26B-versus-31B-IQ4 planning pilot has an unavailable deployed-source revision. Recorded inputs/settings were inspected, but that source revision could not be reviewed.
- Combat request equality came from reconstructed frozen bundles and the prompt builder; original native request artifacts were not directly retrieved. Some original combat programs, suites and grading assets are unavailable.
- Mutable model tags do not identify immutable weights/templates. Missing model fingerprints or runtime details are null or described as unavailable, rather than inferred. Package and parser differences prevent treating these results as controlled quantization-only experiments.

Keep first-pass, syntax-only, guided and manually assisted outcomes separate. Different graders, output budgets, source-dependent mutation samples and scoring environments describe different experimental conditions. New runs belong in a new versioned record with their own identities and settings.
