# November 23: compiler recovery and workflow checkpoints

Run 18 reached `review_required` after 39 minutes without operator intervention. The first compiler correction cleared a Gradle Kotlin DSL error. The second relocated an unavailable exception type and introduced an unavailable repository annotation. The final compiler gate still failed on `MongoDuplicateKeyException` and `UpdateQuery`. No independent suite or delivery was reached. All native execution and exact owned-container cleanup were settled; the original trial remains unchanged.

Investigation identified three recovery gaps:

- The approved dependency catalog did not cover the APIs implicated by these errors. Compiler path selection supplied a generic correction request but no way to verify replacement dependency types. The repair worker guessed again.
- Repair-history matching included an artifact's summary. Creating a new compile component changed that label, so the next request omitted an earlier source correction despite retaining the same source scope.
- Initial compilation could fail while terminal output still held a null artifact. Completed implementation/repair receipts retained the source, but there was no validated initial-compiler continuation importer carrying its spent counters.

Recovery now supports an explicitly approved, bounded CPU inspection of the pinned image's dependency cache, checks proposed imports before the existing single edit retry, and supplies actual public bytecode signatures with version provenance. A diagnostic control indexed 105 jars in the original verifier image. It found both generated imports absent from the declared cache and returned the available `DuplicateKeyException`, repository `Query` and `Update` signatures. It ran no application or GPU model. The existing reserve can be used only for new API evidence; identical facts cannot repeatedly grant attempts, and the total compiler bound is unchanged.

Repair history now matches source contents independently of summary labels. Initial compiler drafts are retained as unverified output. The new continuation importer reconstructed Run 18's 71-file application from its stopped worker receipts, bound it to the last compiler hashes and retained both spent compile repairs and all recovery-request costs. Its task, model, verifier, tests and budgets must remain unchanged. A continuation has separate approval and lineage; it does not turn the original trial into a successful or uninterrupted run.

The runtime also saves immutable, approval-bound checkpoints after completed graph steps and settled model/sandbox operations. Build checkpoints include stage state and spent counters. Completed graph nodes can be restored after a process restart, while raw operation receipts support build replay without duplicate dispatch. Missing or changed receipts, changed runtime code and unknown completion block ordinary resume. The panel displays the latest saved step.

The lifecycle regression terminates a separate process immediately after a saved agent step and verifies that the next process skips that agent. Additional checks cover compiler history, missing dependency evidence, bounded reserves, source/counter tampering, owned-container cleanup and corrupted checkpoints. These are recovery and resume checks; no new live end-to-end completion is claimed.

Validation passed 557 core cases (549 passed, eight optional skips) and all 25 exported native-binding checks. The core test process isolated inherited Git overrides for temporary fixtures on Windows. JavaScript syntax and whitespace checks also passed.

See [checkpoint and compiler recovery instructions](workflow-checkpoints.md) and [planned shutdown controls](workflow-pause.md).
