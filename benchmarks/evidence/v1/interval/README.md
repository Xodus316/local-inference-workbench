# Inclusive interval-union evidence

This is the complete authored interval-function screen: three model packages, one independent implementation and one independent test-writing call per package. The [brief](brief.txt) defines `solution.merge_ranges(rows)`. The [result records](results.json) include exact source hashes, generation settings, call order, completion status, latency and token counts. No additional generation was performed to create this archive.

## Scoring corpus

- [heldout.py](heldout.py): twelve valid and seventeen invalid inputs, including input-preservation assertions.
- [reference.py](reference.py) and [second_control.py](second_control.py): independently authored conforming implementations.
- [mutations.json](mutations.json): seven deliberate defects applied to the first reference.
- [submitted/](submitted/): all six exact model-authored source files, with hashes verified against the recorded submissions.
- [recorded-receipts.json](recorded-receipts.json): allowlisted execution status, cleanup, case outcomes and container settings. Raw logs, run identifiers and machine paths are excluded.
- [replay.json](replay.json): filenames, entry points, expected original outcomes and historical container metadata for the optional Docker score replay.

Implementations are scored against the 29-case corpus. An independent test suite must accept **both** conforming controls before fault sensitivity is scored. For a qualifying suite, a clean assertion failure on a mutation counts as detection; timeout, execution error or failure on correct code does not establish detection.

Recorded outcomes: GLM Flash Q4 passed 16/29 implementation checks, GLM Flash Q5 and Nemotron Lightning passed 29/29. Both GLM-authored test suites rejected both correct controls, so their mutation scores are unscored. Nemotron's suite accepted both controls and detected six of seven faults; input sorting in place survived because its nonmutation witness began sorted.

## Replay and limits

The historical grading image ID and ARM64 Mac host are recorded. The source described "Mac ARM64 Docker" but retained no container-platform inspection; the historical container architecture is therefore null in this archive. Host architecture must not be treated as container architecture. A replay must report its inspected image platform and host separately. A different image or Python version is a portability replay and must be identified as such. Loading an image or calling a model is not required for static hash/numeric verification. An offline replay must use an already installed image and run all generated source only in disposable, network-disabled containers with read-only project/root, bounded temporary storage and the recorded timeout. Never import submissions into a host-side verifier.

This was one small Python contract and one submission per model/role. Timing includes model loading and transport. The retained compact records identify model packages but do not provide immutable weight/renderer fingerprints. The results do not establish general coding reliability, repository-patch accuracy or verdict-only review quality.
