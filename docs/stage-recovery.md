# Failure assessment and fix requests across the workflow

The orchestrator must distinguish what failed, who owns the correction, what may change, and what must be reverified. A process exit category is one piece of evidence. It is not sufficient to identify fault ownership. In particular, generated dependency declarations or startup settings can produce a verifier's environment-classified failure.

New fresh whole-project approvals freeze `whole_project_stage_recovery_policy` as `evidence-routed-stage-recovery-v1`. Existing approvals without that policy retain their prior behavior. The shared implementation is `orchestrator/stage_recovery.py`.

## Recovery contract

Every recorded request has an originating stage, stage family, owner, action, scope, evidence digest, immutable-contract list, and recheck requirement. Existing model corrections record `dispatch_requested` before the worker call; that status does not claim a physical dispatch or successful repair. A source diagnosis records `authorized` only after the runtime validates its evidence and scope. A later successful execution of the same gate scope can record `gate_rechecked`. Passing another component's gate cannot close the request.

The source-diagnosis path first requires a settled process, known cleanup, the immutable verifier image/profile, readonly mounts, exact current application/test hashes, and a matching verifier result. Timeouts, cancellation, unknown native completion, resource termination, missing markers, source drift or tooling drift cannot authorize a model retry. Prior unknown model calls remain protected by the existing worker journal guards.

A bounded, one-step diagnostic call then sees the public contract, current candidate source, public dependency reference and candidate-only failure excerpts. It distinguishes application, test-author, verifier, infrastructure and unknown ownership. An application diagnosis must quote a retained diagnostic and exact current source lines, explain the cause, and request minimal changes to existing files within the frozen byte/file scope. Fabricated anchors, test paths, changed source, modified proof data and expanded scopes are rejected. A diagnosis alone grants no passing credit.

Application requests use the existing scoped repair worker and existing stage/general repair budget. The runtime retains the original receipt, including exit 125 and `review_required`; it records a separate source-repair authorization. After editing, the original pinned gate and required downstream gates run again. No verifier output is rewritten to simulate a compiler failure or a pass.

Test concerns route to the test owner and the existing author/reference-qualification path. The diagnostic source-repair path cannot edit tests, relax assertions, drop cases, change identities or weaken coverage. Reference-first profiles must actually reach the candidate before application diagnosis; reference source and logs never enter that prompt. Verifier or infrastructure corrections become explicit operator requests, not candidate patches or host-side model commands.

## Stage coverage

| Stage family | Fix-request owner and execution path |
| --- | --- |
| Planning | Planner; existing bounded schema/inventory correction now also checks declared required files before freezing. |
| Implementation | Implementer; existing settled inventory recovery, with a recorded request and original path scope. |
| Compile checkpoints | Repairer; compiler-guided selection or source-grounded diagnosis of a settled inconclusive result, followed by the same compiler checkpoint. |
| Smoke authoring | Implementer; existing bounded source/schema correction. |
| Smoke reference qualification | Implementer; existing fixture/source correction with reference requalification and preserved identities. |
| Smoke verification | Repairer configured by the workflow; known failures or validated source diagnoses use the existing self-repair allowance. |
| Independent authoring | Test author; existing syntax, coverage and settled-author recovery. |
| Independent reference qualification | Test author; existing diagnosed corrections preserve source/manifest/test identities and requalify on the reference. |
| Candidate verification | Repairer; existing general repair loop now supports grounded diagnosis before stopping on an inconclusive candidate result. |
| Supplemental verification | Repairer; shared assessment and general budget, with the evaluator and all cases unchanged. |
| Verification profiles | Repairer after reference barriers pass; exact profile identity and source bindings are mandatory. Reference/tooling failures route to the operator. |
| Code review | Reviewer for response/anchor correction; repairer for grounded blocking source findings, using existing bounds and revalidation. |
| Final verification and acceptance | Existing current-source gates remain mandatory; supported application failures use shared assessment, and terminal failures retain an owned correction request. |
| Host delivery/export | Operator; the development harness writes `recovery-request.json` and retains the runner error without retrying/resuming the workflow. |

These are routing and correction mechanisms, not a promise that every failure is automatically fixable. Exhausted budgets, an ungrounded diagnosis, failed reference controls, an unavailable verifier capability or unresolved execution remain terminal and produce explicit blocked requests. The runtime does not modify the verifier, dependency cache, model pins, task, tests or budgets to make a candidate pass.

## Bounds and persistence

The policy allows at most 16 semantic diagnoses, two identical diagnostic issues and 64 correction requests. Existing compile/self/general/test-author repair bounds remain unchanged. Counts are spent before diagnostic dispatch, retained in the workflow counters and included in acceptance-checkpoint bindings. The request cap prevents unrecorded correction dispatches; even when it is exhausted, the terminal request remains visible. A resumed approval cannot regain spent diagnostic/request costs.

Request journal artifacts are finite: `whole_recovery_request_1` through `whole_recovery_request_64`; diagnosis calls are `whole_recovery_1_select` through `whole_recovery_16_select`. Structured output uses the existing no-tool selection transport. A malformed diagnosis fails closed rather than becoming an application patch.

## Validation and practical limit

Fault tests cover fourteen stage families, source/receipt/profile drift, missing cleanup, unknown native completion, reference privacy, fabricated anchors, stale/modified authorization, test ownership, request/diagnosis bounds, same-gate recheck closure, and host delivery failure. A mocked compiler loop confirms that an unchanged exit-125 receipt can lead to a bounded source fix and a later verified pass.

The separate CPU replay uses the exact saved Run 14 backend input. Its deterministic diagnostic control identifies missing dependency management in `api/build.gradle.kts`, issues a scoped request, and preserves the original inconclusive receipt. A deterministic pinned-BOM patch clears the offline Spring Boot dependency blocker; the next compile exposes ordinary Java errors. That is evidence of diagnosis/routing and gate reexecution, not an end-to-end success or a live-model repair result. The original journal is unchanged, replay containers confirm cleanup, and the diagnostic application is not a future application seed.

Private validation receipts and logs remain outside the public checkout under `diagnostics/stage-recovery-20261120`. The updated local harness is `comparison-prep/commerce-stage-recovery-dev-20261120`; it is a development harness, not a prepared or running Run 15. A new frozen preparation and an explicit GPU release are still needed for another live trial.
