# General coding workflow

General uses the shared engine developed in the Commerce and browser Snake trials.
It is an explicit configuration of the ordinary Build API. There are no Commerce
or Snake branches in the runner: project-specific capabilities, references and
verification profiles are supplied as approved configuration.

Enable it in the operator's existing configuration:

```json
{
  "build": {
    "enabled": true,
    "offline_project_enabled": true,
    "general_coding": {"policy": "general-coding-v1"}
  }
}
```

This fragment is not a complete deployment. Configure a preinstalled verifier
image, `project_environment`, trusted `reference_project`, resource limits and model
roles as described in [whole-project builds](whole-project-builds.md). Same-model
roles require `allow_shared_test_author` and manifest-bound isolated sessions. The
pinned OpenCode integration provides those identities for all five roles.

With General enabled, `Runtime.prepare({"mode":"build","prompt":brief})` and
`POST /api/runs` prepare a fresh reviewed whole-project build. Preparation does not
approve it or start inference. Explicit conflicting profiles and per-request
whole-project policy overrides are rejected. Ordinary configurations without
`build.general_coding` keep their existing behavior and approval identities.

## Shared behavior

1. Plan a complete canonical file inventory, then implement it from an empty seed.
2. Run declared compiler checkpoints and bounded source-grounded repairs.
3. Author smoke tests and qualify them against the reference before repairing the
   candidate from their failures.
4. Author independent tests from the public contract. Correct syntax, fixtures and
   reference failures using anchored edits with per-edit evidence IDs. Preserve
   case identities, assertions, coverage and previously passing methods.
5. Run the frozen suite, optional supplemental gates, candidate source checks and
   configured verification profiles. Repair only with settled, source-bound evidence.
6. Review all source, run final smoke and acceptance, then verify delivery hashes.

All stages share agent consultation, explicit fix requests, complete failure
feedback, no-progress/repeat guards and checkpoint accounting. Rejected candidate
diagnostics remain bound to their candidate source; they never earn acceptance
credit. Unavailable verifier evidence is a gate failure, not permission to rewrite
tests. Diagnostics use the complete approved verifier instead of importing its
private Python implementation. Settled malformed patches may return through their
original validator; unknown execution is a hard barrier.

General defaults to the explicit **16 output corrections / 16 ordinary test-defect
repairs / 16 coordinated validations** grant. These are finite, separate from
application and compiler bounds. `"recovery":"legacy"` explicitly selects the
older ceilings for a new approval. No old approval is upgraded or refunded.

OpenCode with automatic compaction is required for the planner, implementer, test
author, repairer and reviewer by default. The integration example validates the
complete deployment routes as well as the core workflow bindings.

## Project capabilities are data

| Configuration | Java/API Commerce application | Browser-only Snake application |
| --- | --- | --- |
| `build.image` | Provisioned Java/Node/database verifier | Provisioned Chromium/Node verifier |
| `build.project_environment` | API URL contract, backend/frontend compiler checks | Static frontend URL, JavaScript and browser checks |
| `build.reference_project` | Private correct API/frontend implementation | Private correct static implementation |
| `build.verification_profiles` | Declared production DOM and API restart checks | Declared mobile browser viewport |
| `build.general_coding.dependency_reference` | Optional pinned dependency signatures | Omitted unless needed |
| `build.general_coding.java_api_probe` | Optional approved image-cache/JDK API probe | Omitted |
| `build.general_coding.frontend_diagnostics` | Optional pinned TypeScript diagnostics | Omitted |
| `build.general_coding.supplemental_suite` | Optional previously qualified evaluator | Omitted unless supplied |
| `build.general_coding.candidate_source_contract` | Optional existing Commerce packaging policy | Omitted |

Optional policies retain their existing strict validators, image/source bindings
and qualification requirements. Enabling General does not infer installed tools,
invent a verifier, copy a previous application or enable Java checks for a browser
project. Trusted reference source remains outside model prompts and delivery.

## Reusable pinned OpenCode runner

Use the same commands for either project's configuration. The request file is a
JSON object containing `mode: "build"` and the prompt. Configuration, deployment
pin, OpenCode executable and verifier must already be provisioned locally.

```powershell
python examples/hyperqwen/general_workflow.py prepare --data runs/new-project --config config.local.json --request request.json --pin backend-pin.json --opencode C:/path/to/opencode.exe
```

Preparation freezes the normal Build approval, all role identities, source hashes
and input hashes. It creates a private `general-manifest.json` and prints its
SHA-256. No backend container or model call is started by preparation.

Inspect the prepared approval and use its exact manifest hash to start:

```powershell
python examples/hyperqwen/general_workflow.py start --data runs/new-project --manifest-sha256 HASH_FROM_PREPARE --execute
```

The runner owns one backend lease, observes the workflow through its terminal
state, waits through `pausing`, closes the runtime before the backend, and exports
accepted files only after source identities and backend cleanup are verified.
`progress.json`, `terminal.json`, `outcome.json` and `delivery.json` are local
evidence; do not publish raw private manifests or journals.

After a clean pause and computer restart, use the unchanged approval:

```powershell
python examples/hyperqwen/general_workflow.py resume --data runs/new-project --manifest-sha256 HASH_FROM_PREPARE --execute
```

[Pause controls](workflow-pause.md) and settled checkpoints preserve completed
steps and costs. Changed code, inputs or unknown native/sandbox execution prevent
resume. A failed workflow requiring code changes needs a separately approved
checkpoint continuation; `resume` does not silently relaunch failed work.

Audit the terminal evidence and exact owned-resource cleanup with the same tool
for either project:

```powershell
python examples/hyperqwen/general_audit.py --data runs/new-project --manifest-sha256 HASH_FROM_PREPARE
```

The [Commerce](commerce-end-to-end-completion-2026-11-18.md) and
[Snake](snake-end-to-end-2026-11-25.md) reports describe earlier experiments. Their
successes are not a claim that a new fresh General trial has already passed.
