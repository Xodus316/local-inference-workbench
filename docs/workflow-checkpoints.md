# Checkpoints and compiler recovery

The runtime saves a durable checkpoint after each completed graph step and each settled model or sandbox operation. This includes planner/implementer/repair/test-author/reviewer calls, compiler checks, test runs, verification gates and dependency inspection. The Workflows panel shows the last saved step or operation; `GET /api/runs/{id}` exposes its checkpoint index.

Checkpoints are immutable files under the run's data directory, indexed in SQLite. They bind the original approval, exact runtime source, input state and receipt hashes. Build operation checkpoints also capture the current output, stage evidence and spent counters. A saved operation receipt is evidence of completion, including a settled failure; it is not a passing test or accepted source. Its result may still need to be applied or validated during replay.

After a service/process interruption, reopen the same data directory and configuration and resume the interrupted run with its saved approval digest. Completed graph steps restore their saved results. Build control flow reconstructs state from completed receipts without redispatching those operations. The restart tests terminate a separate process immediately after a checkpoint and verify that the next process skips the completed agent step.

For a planned shutdown, use [Pause after current operation](workflow-pause.md) and wait for **paused**. A checkpoint cannot establish the outcome of a later interrupted model stream or sandbox process. Unknown execution, missing/changed receipts, corrupted checkpoint content or changed runtime code block ordinary resume. A runtime fix requires a new, explicit continuation; cancellation and an exhausted recovery budget are not converted into a paused run.

## Compiler dependency evidence

A compiler can report an unavailable dependency type or annotation member even when the repair worker has been given a partial public API catalog. Repeatedly moving an unknown type to another package does not establish that it exists. Optional image-cache inspection supplies evidence before another compiler repair:

```python
runtime.prepare_whole_project(
    request,
    # Existing model workers, verifier and other scope settings still apply.
    java_api_probe={
        "policy": "pinned-java-api-probe-v1",
        "classpath_roots": ["/opt/gradle-ro"],
    },
)
```

The operator explicitly approves the cache roots in the bundle. A CPU-only container uses the pinned verifier image, no network, a read-only root and a read-only mount containing only the trusted inspection script and query. It indexes at most 2,048 cached jars and two million archive entries, then runs bounded `javap -public` queries. It never mounts the application, reference implementation or tests, and never executes application code. The normal exact-container ownership/drain lifecycle and durable operation journal apply.

The result identifies imports absent from those declared cache roots and supplies up to 12 relevant public signatures with jar/version provenance. Multiple cached versions are reported explicitly; the cache does not by itself establish which version Gradle resolved. The worker must match evidence to the unchanged approved dependencies. A second inspection checks the proposed patch; imports still absent from the cache trigger the existing single bounded edit correction before the original compiler gate runs again.

New missing-API evidence can justify using the existing compiler reserve when the earlier unchanged-file frontier rule does not apply. The total remains two base repairs plus at most two reserved repairs per component. Identical API facts cannot repeatedly earn another reserve attempt. Dependency inspection grants no acceptance credit and cannot waive compilation or downstream tests. Approvals without this optional capability keep their existing behavior.

## Continuing a terminal initial compile failure

`orchestrator.compile_continuation.pack` imports only a settled, approved initial compiler failure. The host supplies the original terminal row and its exact journaled requests/responses, verifies the closed backend and frozen source, and prepares a separate target bundle. The importer reconstructs the plan/application from completed worker receipts and validates the resulting source against the final compiler source hashes. It rejects unknown worker outcomes, changed task/model settings/verifier/tests/budgets, incomplete inventories and refunded counters.

The new bundle carries `whole_project_initial_compile_recovery`. It retains prior compiler records, repair history and all spent compiler/recovery counters. It is explicitly a continuation, so it cannot carry the fresh-run declaration. Compilation reruns, all later smoke and independent tests are still authored, and every acceptance gate remains required. The original run and its terminal outcome remain unchanged.

Compiler drafts are now retained in terminal build output as unverified evidence. Older runs with a null draft can be reconstructed only when their completed receipts establish the full source and its last compiler identity. This is distinct from resuming an ordinary paused run.
