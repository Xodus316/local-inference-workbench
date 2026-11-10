# Reusing validated project checkpoints

`orchestrator.project_reuse` lets a trusted host prepare selected completed inputs
for a **new** `offline-project-v1` run and approval. It never resumes the original
run, manufactures cached model calls, changes original files, or calls a provider.
The normal public `Request` does not accept caller-authored checkpoint payloads.
A host resolves its own authenticated local journal and freezes the helper's
manifest in `bundle["project_reuse"]` before creating the new approval digest.

```python
manifest = prepare_project_reuse(
    original_row, read_original_artifact_bytes, fresh_public_bundle,
    source_sdk_revision=original_sdk_revision,
    current_sdk_revision=installed_sdk_revision,
    through="implementation", completed_groups=2,
)
fresh_public_bundle["project_reuse"] = manifest
# Create a fresh run, show its complete scope, and approve its new digest.
```

The source must have its original matching bundle digest and approval, and be
`completed`, `failed`, `cancelled`, or deliberately `stopped`. Every source call
must have a known finish and a matching native raw receipt, including calls after
the selected checkpoint. A blocked, interrupted, running, dispatched, missing,
nonfinite, unknown-cleanup, or otherwise uncertain source is rejected. This
feature cannot recover or retry an unknown inference. A non-dispatch is checked
against its explicit receipt; an imported structured output must have finished
normally with `stop` and pass its current schema.

`through` explicitly selects `plan`, `tests`, `group_plan`, or `implementation`.
Only implementation reuse accepts a positive `completed_groups`, from 1 to 32.
Groups must form the exact contiguous prefix of the frozen partition. Each
group's files, accumulated source, byte limits, interfaces and dependencies are
revalidated. Missing later groups are generated normally; all assembled source
runs through new sandbox acceptance checks. Reusing every group makes no native
implementation call but still executes the full independent test suite.

The fresh destination must preserve the complete brief and sources, immutable
image ID, canonical project environment, build contract/settings, backend,
independent roles, and every role's generation settings. Changed runtime metadata
requires new stages instead of importing an incompatible checkpoint. SDK
revisions are explicit provenance: a different source revision is never assumed
compatible, and even an identical revision does not bypass current validation.
The host supplies verified revisions; loading the approval-bound manifest always
reruns current static validators without model repairs or test-source changes.
If a current SDK identity is supplied to `load_project_reuse`, it must equal the
revision that froze the new import.

New project runs save the complete validated suite as `build_frozen.raw`, bound
to its plan and suite digests. Tests are reconstructed from the independent
author and any completed syntax, fixture, or coverage repairs, then checked
against that frozen source. Coverage, discovery, fixture checks and environment
validation all rerun. A legacy run with only suite metadata cannot supply tests
or implementation checkpoints; its normally completed plan can still be selected.
The grouping and accumulated group artifacts retain their existing identities.

The manifest binds original run/approval/bundle/SDK identities, a separate public
projection digest, every call's raw receipt hash, the selected artifact byte
hashes and validated payload digests. It stores the complete imported content in
the new approval. Source calls/artifacts stay unchanged. A later fresh run can
reuse these inputs while preserving the original component provenance, rather
than inserting pretend native calls into its journal.

Inherited tests cannot cross another SDK revision when their original fixture
transition bytes are unavailable in the immediate source journal. Final source
and hashes alone cannot establish that an earlier SDK preserved assertions during
a fixture repair. In that case, reuse only the plan and author fresh tests. A
direct source with its complete author/repair receipts can cross revisions after
all current transition checks pass; same-SDK inherited reuse keeps its original
provenance and current final-suite checks.

A private host may supply trusted `project_bundle` and `project_call` callbacks.
The original bundle and approval are verified **before** projection. The former
produces the host's ordinary public Build projection; the latter only translates
stage/role names and must preserve request identity, status and timestamps.
`artifact_bytes(original_stage, suffix)` returns exact retained local bytes, never
a model-supplied path. The host remains responsible for local authorization,
fresh-run creation, complete approval verification and protecting its journal.
Neither callback is accepted from an API caller or model.
