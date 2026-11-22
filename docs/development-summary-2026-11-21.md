# Workflow development summary — November 21, 2026

The fresh Commerce workflow now gets through application compilation and its smoke/trusted API checks, including concurrent clicks, persisted decimals and category slug behavior. It has not completed a fresh end-to-end run. Run 16 failed during independent test correction; Run 17 was stopped at the user's request before shutdown while correcting independent test coverage.

This report covers the November 20 work and its continuation into the early hours of November 21, Pacific time. This publication includes the pending recovery/runtime changes developed since the [November 19 report](commerce-fresh-evaluation-2026-11-19.md), together with the all-role OpenCode integration and today's cancellation record. The earlier [November 18 successful continuation](commerce-end-to-end-completion-2026-11-18.md) remains a separate result; it is not evidence that a fresh run has completed.

## Changes published

- **Compiler evidence and bounded progress.** Compiler locations resolve against the current approved source inventory. Kotlin build-script repairs address the complete identified syntax family. Pinned Java formatter signatures guide relevant repairs. Independent Vue parsing supplements unchanged typecheck/build checks. Two ordinary compile repairs remain, with at most two additional repairs only when unchanged-source evidence exposes a new compiler frontier.
- **Reference-qualified smoke recovery.** A passing control for the same frozen suite precedes candidate repair. Route-prefix and mapped MongoDB decimal failures can produce source-bound repair requests. Proven unittest fixture shadows can be corrected without treating a zero-test result as application failure. Reference source remains verifier-only.
- **Shared stage recovery.** Planning, implementation, compilation, smoke and independent-test stages, supplemental checks, profiles, review, final verification and delivery emit owned correction requests. Settled inconclusive failures can receive a diagnosis grounded in actual source quotes and retained diagnostics. The original gate and required downstream checks must run again. Limits remain 16 semantic diagnoses, two identical diagnostic issues and 64 requests; existing self/test/general repair limits are not waived.
- **Source evidence and repair effects.** Repair decisions receive current-source facts plus bounded before/after hashes and actual edit diffs. Earlier diagnoses and source comments are hypotheses. Complete repeated failing cases can be compared without volatile request IDs; incomplete diagnostics cannot establish recurrence. A static persistence risk does not prove the stored BSON type. This mechanism is documented and tested; uninterrupted history through every component-reassembly path remains a practical follow-up rather than a claimed live success.
- **Selector context accounting.** Source selectors project at most 32 KiB of prioritized current source while retaining the complete inventory, and reserve the actual 65,536-token completion allowance. A proved terminal selection limit can use one fresh-session restart with at most 16 KiB of source. Byte-size and output-limit rejections share that single allowance; partial output is not accepted or silently redispatched.
- **OpenCode for every active workflow role.** Planner, implementer, repairer, test author and reviewer now use the pinned OpenCode CLI. The reviewer previously called the model directly. Its replacement delegates to an OpenCode worker with exact one-use CPU admission and receipted native evidence. Structured modes no longer explicitly disable compaction. No-tool decisions allow one primary schema-bound response and up to eight stopped auxiliary compactions; tool permissions and output/schema checks remain enforced. The reusable [HyperQwen bindings and synthetic tests](../examples/hyperqwen/README.md) are included in this repository.

The [stage controller](stage-recovery.md), [source evidence](repair-evidence.md) and [all-role OpenCode](opencode-all-roles.md) documents describe the implementation and bounds. The public example uses repository-relative imports; deployment-specific manifests and private evaluation launchers are not published.

## Fresh-run outcomes

Each trial used a new plan/application state and authored its own test suites when it reached those stages. The original task, public timestamp clarification, pinned verifier/model identities and frozen acceptance requirements were retained across these later runs. The reference application and qualified 14-case supplemental evaluator were verifier tooling, not model-visible application seeds. The active roles retained 98,304-token contexts and 65,536-token completion ceilings, with 64 KiB test and selected-repair source limits.

| Run | Outcome | Furthest result or blocker |
| --- | --- | --- |
| 12 | Review required | Frontend compiler errors remained after the checkpoint repair budget. |
| 13 | Review required | Backend compiler errors remained after the checkpoint repair budget. |
| 14 | Review required | The backend checkpoint produced an inconclusive compiler receipt; generated dependency configuration had been classified as an environment failure. |
| 15 | Failed | Click requests still failed after an operand-only decimal repair. The second source selector exhausted its context. |
| 16 | Failed | All required ordinary smoke/trusted checks passed after automatic repairs. Independent test correction reached the completion ceiling at `build_tests_retry`. |
| 17 | Cancelled by user | Smoke/trusted checks passed. Independent authorship corrected its declared implementation inventory and entered coverage correction. The user then requested cancellation before shutdown. No independent candidate verification or delivery occurred. |

Runs 12–16 reached their own terminal outcomes without operator rescue, with frozen runtime/input integrity retained. Run 17 ran without manual rescue until the explicit cancellation request. These are different outcomes: cancellation is not an end-to-end success or an ordinary orchestrator failure.

### Why the click repair and selection failed

Run 15 changed the `$inc` operand to Decimal128 while leaving an unannotated BigDecimal field persisted as a BSON string. Its generic HTTP 500 handler concealed the driver exception. Disposable CPU controls reproduced the saved candidate, exposed MongoDB's non-numeric string error through diagnostic logging, and passed the same smoke/trusted checks after adding numeric field mapping. Those controls never seeded a new application and do not establish migration of arbitrary old records.

The second selector used 50,753 prompt tokens plus 47,551 completion tokens, exactly filling the 98,304-token context. The orchestrator reserved only 32,768 completion tokens although the native worker allowed 65,536. The updated prompt/admission replay used 28,579 reserved prompt tokens and retained both implicated files. Run 16's three selectors then completed normally and its click repair passed after two compiler corrections. The selector restart path was not exercised in that live run.

### Run 16

Run 16 ran November 20 from 15:35:22 to 18:23:45 Pacific, approximately 2 hours 48 minutes. Automatic repairs cleared compilation, an invalid Jackson startup setting, click persistence/update behavior and timestamp precision. The independent author's first draft declared the wrong implementation path inventory. Its correction stopped on step 7 with 11,719 prompt tokens and 65,536 completion tokens: 77,255 total context tokens. This reached the single-response completion ceiling. No independent suite was accepted. The owned backend stopped, and all 110 frozen source/input/helper hashes matched afterward.

### Run 17 and today's shutdown

Run 17 started November 20 at 19:22:11 Pacific. The user requested cancellation before shutdown on November 21; ownership-verified backend shutdown was recorded at 00:11:47, approximately 4 hours 49 minutes after launch. The workflow had passed all required ordinary smoke/trusted checks after three source-repair rounds, including two compiler corrections inside the first round. Its independent author produced a seven-file, 54,635-byte draft whose declared implementation inventory was rejected. The inventory correction cleared, and `build_tests_coverage_1` was still running when cancellation was requested.

The native journal retained 226 completed request receipts and 17 normally stopped compactions: three in implementer sessions, four in repairer sessions and ten in test-author sessions. These demonstrate live compaction in the reached roles. Planner and reviewer compaction configuration/delegation were checked synthetically; source review was not reached in the live trial. All five routes were explicitly bound to OpenCode and checked before launch.

Cancellation interrupted the runner; it did not produce the workflow's normal terminal outcome receipt. The exact owned model container was then stopped and independently inspected as exited, not running, with PID zero. No runner/OpenCode process remained and the model's GPU allocation was released. The interrupted request has no proof of normal native completion, so its evidence remains unresolved rather than being relabeled accepted. No resume or rescue was performed. All 111 frozen runtime/input/helper hashes still matched. The original journal was preserved separately from the operator-cancellation record.

## Durable pause and restart

After the shutdown request, the workflow gained a cooperative pause lifecycle. The Workflows panel, API and standalone operator command can request a pause; the runtime waits for a journaled operation to finish, saves its receipt and build evidence, verifies sandbox cleanup and releases its managed backend before recording **paused**. Reopening the same state/configuration and resuming replays saved receipts without repeating physical calls or resetting repair budgets. Finite runtime allowances retain their remaining active time across offline periods.

The optional HyperQwen runner reconstructs the approved OpenCode workers in a new process and treats **pausing** as active until cleanup completes. Unknown completion, unresolved sandbox execution, failed cleanup or changed identities prevent a safe pause/resume acknowledgement. This does not recover Run 17's interrupted request, change its cancellation outcome or alter its frozen runtime. See [pause and restart instructions](workflow-pause.md).

The feature checkout passed 543 core tests (535 passes and eight optional skips) and all 25 exported native-binding/launcher checks. The core run isolated temporary Git fixtures from host-injected Git configuration after Windows environment restoration dropped an empty override value. An earlier Windows HTTP rejection connection-abort did not recur in the final run. Lifecycle checks cover separate-process resume without repeated native calls, repair replay and budgets, repeated queued pauses, offline deadlines, configuration/backend drift, unresolved execution and cleanup failures. The launcher checks use synthetic bindings. No new GPU run, live model restart or full computer reboot was performed for this feature.

## Validation and remaining work

The frozen Run 17 runtime passed 533 tests: 525 passes and eight optional skips. All 38 local harness checks passed, including every actual role configuration, reviewer delegation through OpenCode, compaction wire-protocol boundaries, schema/identity checks and unknown-completion handling. CPU diagnostic controls and exact-tokenizer replays made no GPU/model calls. The merged publication checkout also passed the 533 core tests with the same eight skips and all 22 exported native-binding tests; those portable binding checks now run separately in CI.

No new fresh end-to-end completion is claimed. Independent test inventory/coverage correction remains an observed bottleneck. Compaction manages accumulated conversation context and does not enlarge a single response's completion limit. The supplemental evaluator, required production UI/full-restart profiles, review, final acceptance and delivery remain necessary; they were not reached in Runs 16 or 17. Ordinary image results reported browser capability unavailable, so passing API/DOM checks must not be described as real-browser coverage.

Private references, candidate source, full prompts, model credentials/manifests, raw journals and verifier fixtures stay outside this repository. This report publishes derived outcomes and reusable source/test changes, not hidden evaluation inputs. Run 17 is consumed and must not be restarted; any further unattended test needs a new frozen preparation and launch authorization.
