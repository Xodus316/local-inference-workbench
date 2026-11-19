# Fresh whole-project builds

The public runtime now includes the reusable workflow fixes developed during the Commerce campaign. A fresh build creates a new plan, application, smoke suite and independent suite. It can use a hidden reference implementation and a previously qualified supplemental evaluator, but neither supplies application source to the model. The [Commerce completion report](commerce-end-to-end-completion-2026-11-18.md) describes the earlier checkpoint continuation; it is not evidence that this newly integrated runtime has completed a fresh Commerce run.

## Prepare a fresh build

Configure the runtime's `whole_project_workers` with implementation, test-author and repair workers, optionally including a planner worker. `whole_project_prompt_tokenizers` can provide exact or conservatively bounded native-template token counts. These objects must expose stable identities matching the configured roles, context, reasoning and source capacities.

Use `Runtime.prepare_whole_project(request, **options)` for the public entry point. It returns an unapproved prepared run. Preparation does not approve, enqueue or dispatch inference.

```python
prepared = runtime.prepare_whole_project(
    {
        "mode": "build",
        "build_profile": "offline-project-reviewed-v1",
        "prompt": brief,
    },
    dependency_reference=pinned_dependency_api_catalog,
    supplemental_suite=qualified_supplemental_evaluator,
)

# A separate explicit launch step, after checking the prepared result:
runtime.approve(prepared["id"], prepared["digest"])
```

The optional dependency catalog must bind the verifier image. The optional supplemental suite must preserve every discovered case and include its bound reference qualification. The ordinary source and test size defaults remain bounded; a separately approved supplemental suite can use 48 KiB while independent tests retain their 32 KiB limit. Only implementation and repair roles can see that larger, exact supplemental projection.

The fresh entry point rejects application seeds, imported plans/checkpoints, reuse records, previous diagnoses and external findings. It records zero initial repair costs and binds the public engine's source files. Engine drift is rejected before execution. Same-weight roles require an explicit model-manifest and fresh-session identity binding; they are not described as independent model families. Default configurations still require a separate test-author model.

The `reference_project` build setting points to verifier-only source. It is packed into the private local approval record and excluded from model prompts and worker worktrees. Do not publish raw approval records or run journals as benchmark reports.

## What the runtime does

1. Validate the plan's canonical file inventory and allow one correction of a settled malformed plan.
2. Generate the application, compile declared components and repair within the approved component budgets. Compiler diagnostics and pinned public API signatures guide source selection.
3. Generate smoke tests, reject fixture errors and run the reference before using a test failure to repair the application. Every required verification check must pass before advancing.
4. Author independent tests from the public contract. Preserve existing source and coverage during bounded syntax, coverage or reference-test corrections. Reject tautological assertions, invalid fixture patterns and skipped required cases.
5. Run independent and supplemental tests before expensive review. A changed candidate returns through verification; an unknown execution cannot authorize an application repair.
6. Review all source with exact source anchors and, when needed, one native response correction. Preserve grounded findings, raw responses, the anchor registry and canonical expansion.
7. Run final smoke/regression and configured verification profiles. Require current-source evidence, complete cleanup and settled outcomes before delivery.

General repairs share one counter. The default allowance remains three; an explicit configuration can allow up to eight. Compiler and test-author correction budgets remain separate and bounded. Setting `run_timeout_seconds` to `null` removes the overall deadline only; it does not remove individual operation bounds or permit replay of unknown model calls.

The public queue and journal retain failure history and spent costs. A normal native completion rejected by validation stays rejected when read from cache. Scoped whole-project acceptance cannot pass with quarantined tests. Frozen application tests are not rewritten by source repair.

## Additional verifier profiles

`build.verification_profiles` accepts up to eight named profiles. Each has a `candidate` or `final` phase, explicit environment values, hash-bound readonly verifier files, and optional trusted commands and entrypoint. Profiles are part of the approved configuration and effective-runtime identity. They cannot mount tooling over the application workspace, and the executor verifies application/test source before and after execution.

Every profile runs fresh ordinary-reference and profile-reference controls before the current candidate. Reference failures stay harness-side and never become candidate repair instructions. The reference can use a different internal file layout from the candidate. Only a settled candidate failure supplies feedback to the native repairer, using the same general repair budget.

A production-mode DOM profile can set `NODE_ENV=production` and mount a pinned bootstrap helper. The base image may remain unchanged, but the profile is a distinct effective runtime. A restart companion can use an explicit entrypoint and named result checks. Companion commands do not earn credit for executing the independent test cases.

Profiles are generic execution and evidence machinery. Framework-specific bootstraps, API fixtures, restart semantics and private reference source belong in the local test setup. Production-mode DOM checks are not real-browser or visual-layout coverage; API restart does not establish database-restart or power-loss durability.

Docker lifecycle receipts bind a unique name, ownership nonce, image and exact container ID. Creation and cleanup have finite bounds. Cleanup verifies the recorded ID's absence; a timeout, changed identity or ambiguous dispatch stays inconclusive rather than receiving passing credit.

## Native structured-output adapters

`project_wire_schema` provides an opt-in decoder projection for the observed XGrammar string-constraint incompatibility. It records each omitted constraint and preserves literal `const`/`enum` data. The original runtime schema remains authoritative; the projection alone never admits a response.

`project_native_content` preserves ordered final-channel content and binds it to the native receipt. It excludes private reasoning, rejects incomplete or inconsistent streams and compares decoded JSON without conflating booleans and numbers. Provider adapters must apply these checks together with the original response model and terminal-protocol validation.

The injected provider/worker interfaces remain available for native backends. The HyperQwen launch, model files and Commerce evaluator fixtures are local configuration, not bundled into the public runtime.

## Validation and experiment boundary

The integration is checked with offline unit and mocked workflow tests, including source/capacity boundaries, isolated-role preparation, reference barriers, repair costs, citation retention and exact-owner cleanup. These checks do not substitute for the new live Commerce experiment.

The final integration batch completed 463 tests: 455 passed and eight optional tests were skipped (one Docker test and seven PDF-dependency tests). The existing benchmark evidence verifier also passed. [Validation counts and receipt identities](../benchmarks/reports/runtime-integration-2026-11-18.json) distinguish this offline result from the still-unstarted Commerce run.

For an observation-only trial, freeze the public commit, brief, model settings, reference, verifier profiles and evaluator suite before launch. Start from an empty application state and retain the terminal result, including any failure. Built-in bounded repairs are part of the experiment; operator edits, altered tests, enlarged budgets and rescued continuations would constitute a different trial.
