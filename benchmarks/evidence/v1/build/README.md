# Synthetic Build and independent-test evidence

This archive supports the public model-role discussion in [coding comparisons](../../../../docs/research/coding-comparisons.md) and [independent test authorship](../../../../docs/research/test-authorship.md). It contains synthetic specifications, trusted controls, fixed witnesses, final submitted test source, and compact allowlisted historical observations. These examples are self-authored software exercises.

## What can be checked

| Domain | Included | Limits |
|---|---|---|
| TTL cache | Complete public specification, two controls, independent integer-time oracle, 2,500-case preflight generator, 13 explicit cases, invalid cases, ten fixed mutants/witnesses, successful IQ4_XS submission, and compact challenger outcomes | One successful first submission is supporting evidence, not general reliability. |
| Occupancy | Complete specification, two controls, exhaustive small-domain preflight, ten mutants/witnesses, four first submissions and a separately saved syntax-only cleanup | The rejected IQ4_XS source is intentionally invalid Python. Cleanup remains an assisted diagnostic. |
| Inventory | Specification, trusted reference, 31 held-out cases, nine fixed defects/witnesses, guided candidate, reviewed tests, first implementation, three added regressions, repaired implementation and compact receipts | Assisted continuation. The exact original failed author source is unavailable; the guided source is not substituted for it. |
| Combat | Original synthetic task, later boundary tests, matched historical scores, settings and limitations | Archived comparison only. Missing original suites, plans, implementations and independent simulator prevent score replay. |

The `results.json` files distinguish first-pass outcomes, diagnosis, manual assistance and later verification. The observed TTL IQ4_XS result was one call in 147.37476897239685 seconds, with eight tests passing each of two controls and ten witnessed defects detected. No syntax repair or assertion edit was used for that TTL submission.

## Code and evaluation boundary

Generated submissions and implementations are saved as `.py.txt` and as final source strings inside JSON. Nothing in this archive executes during manifest verification. The controls and fixture verifiers use only the standard library; the verifiers are opt-in evaluation tools, not build hooks. Generated code must be materialized and executed only in an explicitly selected isolated container with networking disabled, read-only inputs, execution deadlines and confirmed cleanup. The bundle provides no automatic model calls, downloads or publication.

Historical inference used a 24 GB RTX 3090. Test evaluation for the cache, occupancy and reviewed inventory results used Docker on an ARM64 Mac; the original records did not separately record the container architecture. Scoring time is distinct from model latency. The immutable historical image identity is retained in applicable result records. Replaying tests checks saved outputs on a fixed domain; reproducing historical latency would additionally require identical hardware, model artifacts, parser/template, runtime and warm-start state.

## Provenance and sanitization

Text snapshots use UTF-8 with LF newlines; generated source is taken from final parsed submissions, without changing assertions. Physical checkout CRLF differences are normalized. Source-text hashes identify those exact archived strings. Each JSON exporter uses explicit field allowlists. Transport envelopes, authentication, service locations, run identifiers, caller bindings, local profile names and raw reasoning are excluded. The combat message-identity limitation is recorded instead of implying original native requests were archived.

The public benchmark reports retain the interpretation and caveats; this bundle adds inspectable evidence. Bounded mutation samples do not prove general semantic correctness, and source-dependent samples are not comparative accuracy scores.
