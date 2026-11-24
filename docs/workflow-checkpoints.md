# Checkpoints and compiler recovery

The runtime saves a durable checkpoint after each completed graph step and each settled model or sandbox operation. This includes planner/implementer/repair/test-author/reviewer calls, compiler checks, test runs, verification gates and dependency inspection. The Workflows panel shows the last saved step or operation; `GET /api/runs/{id}` exposes its checkpoint index.

Checkpoints are immutable files under the run's data directory, indexed in SQLite. They bind the original approval, exact runtime source, input state and receipt hashes. Build operation checkpoints also capture the current output, stage evidence and spent counters. A saved operation receipt is evidence of completion, including a settled failure; it is not a passing test or accepted source. Its result may still need to be applied or validated during replay.

After a service/process interruption, reopen the same data directory and configuration and resume the interrupted run with its saved approval digest. Completed graph steps restore their saved results. Build control flow reconstructs state from completed receipts without redispatching those operations. The restart tests terminate a separate process immediately after a checkpoint and verify that the next process skips the completed agent step.

## Settled worktree output limits

OpenCode compaction summarizes conversation context; it cannot prevent an individual response from exhausting its completion-token ceiling. For implementation and application-edit workers, `settled-worktree-output-limit-v1` collects safe scoped files after a terminal `length` stop and confirmed scratch disposal. Native adapters additionally require completed streams with `[DONE]`, verified generation usage and matching role/session/model profiles. Interrupted or unknown completions remain a retry barrier. A partial receipt is checkpointed as settled evidence, never accepted implementation or passing verification.

The orchestrator routes a proven partial result into its existing single inventory correction or single edit correction. Retained implementation files are read-only while missing files are created. Even a full inventory at an output limit requires a normally stopped completion/review; edits stay inside the original approved inventory. A second capped or unverified correction stops the stage. Workers write one complete file per tool call to avoid a giant source response. Context, per-request completion/thinking caps, repair budgets, models and all downstream gates remain unchanged. Structured planner, selection, test-author and reviewer boundaries retain their separate existing policies. Checkpoint replay retains the spent operation and does not repeat the capped request.

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

New approvals may select `policy: "pinned-java-api-probe-v2"` with the same `classpath_roots` and explicit `include_jdk: true`. V2 derives receiver types and member names from compiler-located current source, prioritizes their actual bytecode owners, and projects relevant method signatures before unrelated members can truncate them. It indexes the pinned JDK's jmods, inspects referenced platform types, and discovers enum implementations of interfaces required by actual method signatures. Public declarations, enum fields, interface relationships, selected members and omissions remain labeled; a projected class does not establish that inherited members are absent. Cached jar versions still do not prove Gradle's resolved version.

V2 inspects public and protected signatures with explicit access labels. It records complete public constructor inventories only for the pinned JDK or classes with one cached origin. An empty inventory establishes that a plain public `new` call is unavailable; protected constructors describe extension access. Multiple cached origins cannot establish a negative constructor or enum claim.

V2 also uses the existing single edit-correction allowance for a proposed enum constant absent from a completely inspected enum, an explicitly removed import still referenced in unchanged code, or a plain constructor call with no supported public argument count. Constructor checks balance nested calls and array initializers, account for varargs, and decline anonymous subclasses, same-package/nested classes and ambiguous expressions. They do not infer argument-type compatibility. Pinned facts already supplied to the worker remain available when a changed diagnostic line yields no new inspection. Corrections stay inside the worker's editable scope. Ambiguous wildcard/local resolution and comments/string literals do not establish these findings. This supplies corrective feedback before the original compiler checkpoint; it grants no passing credit or additional repair allowance. Repeated missing-API facts cannot earn another reserve by changing projection metadata. Existing V1 approvals retain jar-only inspection.

New missing-API evidence can justify using the existing compiler reserve when the earlier unchanged-file frontier rule does not apply. The total remains two base repairs plus at most two reserved repairs per component. Identical API facts cannot repeatedly earn another reserve attempt. Dependency inspection grants no acceptance credit and cannot waive compilation or downstream tests. Approvals without this optional capability keep their existing behavior.

## Continuing a terminal initial compile failure

`orchestrator.compile_continuation.pack` imports only a settled, approved initial compiler failure. The host supplies the original terminal row and its exact journaled requests/responses, verifies the closed backend and frozen source, and prepares a separate target bundle. The importer reconstructs the plan/application from completed worker receipts and validates the resulting source against the final compiler source hashes. It rejects unknown worker outcomes, changed task/model settings/verifier/tests/budgets, incomplete inventories and refunded counters.

The new bundle carries `whole_project_initial_compile_recovery`. It retains prior compiler records, repair history and all spent compiler/recovery counters. It is explicitly a continuation, so it cannot carry the fresh-run declaration. Compilation reruns, all later smoke and independent tests are still authored, and every acceptance gate remains required. The original run and its terminal outcome remain unchanged.

A settled compiler continuation may itself supply a later continuation. Its inherited source and plan are validated first, then only newly completed scoped compiler edits are applied. Histories and counters accumulate without resetting the allowance. Compiler component slots come from their bound checkpoint stage and name; whole-project components share implementation group zero. A separately approved V1-to-V2 frontier upgrade preserves the exact two-repair reserve; it does not change task, model settings, verifier, tests or budgets. Ordinary resume still requires the original code identity.

Compiler drafts are now retained in terminal build output as unverified evidence. Older runs with a null draft can be reconstructed only when their completed receipts establish the full source and its last compiler identity. This is distinct from resuming an ordinary paused run.

## Complete frontend diagnostics

Some compiler summaries retain only the first portion of each error line. A long TypeScript route error may lose the actual incompatible property. Explicitly approved frontend diagnostic replay captures the complete pinned typecheck messages and package versions:

```python
runtime.prepare_whole_project(
    request,
    frontend_diagnostics={
        "policy": "pinned-frontend-diagnostics-v1",
        "node_modules": "/opt/frontend/node_modules",
    },
)
```

The replay uses the same immutable image, `vue-tsc --noEmit`, and the verifier's configuration choice (`tsconfig.app.json` when present, otherwise `tsconfig.json`). It adds plain output and `--noErrorTruncation`. Only the failed frontend component and trusted probe/query are mounted, read-only, in an owned CPU-only container with no network. No reference or tests are mounted. Source hashes, probe bytes, image, exact completion, cleanup and output completeness must validate. Missing, altered, truncated or contradictory evidence stops recovery without passing credit.

Complete source locations augment deterministic path selection. For TS2322 literal mismatches, the repairer also receives source anchors and the compiler's literal alternatives, with an explicit warning that non-literal union members may exist. Values come from the actual pinned toolchain rather than a framework-specific guess. Repair instructions preserve strictness and checked source scope; the original compiler checkpoint and all acceptance gates still run after the patch.

The [November 23 frontend recovery investigation](frontend-recovery-2026-11-23.md) distinguishes the observed failed run, CPU controls and the unstarted continuation.
