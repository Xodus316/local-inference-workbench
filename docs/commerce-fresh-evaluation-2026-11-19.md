# Fresh Commerce workflow tests, November 19, 2026

Eleven fresh Commerce Catalog runs launched on November 19, Pacific time. Five passed both compile checkpoints. Three reached candidate smoke verification. Run 10 passed the candidate smoke suite and required verifier checks after two automatic repairs, then stopped while authoring the independent suite. All eleven stopped before final acceptance.

Each launch started from the Commerce brief and the same public timestamp clarification, with empty plan and application state. The workflow was configured to author new smoke and independent test suites when it reached those stages. The pinned offline verifier used a reference application and a previously qualified 14-case supplemental evaluator. The reference source stayed in the verifier. The runtime and inputs were frozen per launch, and the orchestrator handled repairs within each run. Runtime changes were developed between runs in separate local checkouts. The [November 18 completion report](commerce-end-to-end-completion-2026-11-18.md) records the earlier successful checkpoint continuation.

## Live runs

The model was Qwen 3.8 27B AutoRound-fast W4A16 served through HyperQwen/vLLM on an RTX 3090, with OpenCode and a 98,304-token active role context. Runs 01–04 used a 32,768-token completion ceiling and 32 KiB limits for test source and selected repair source. Starting with run 05, the five active roles had a 65,536-token completion ceiling and the relevant test and repair source limits were 64 KiB. Completion tokens and source bytes measure different things.

An initial startup attempt stopped before inference because Docker Desktop's Linux engine was unavailable. The eleven runs below started after Docker became available. Durations are elapsed wall time rounded to one decimal minute.

| Run | Minutes | Terminal status | Last result |
| --- | ---: | --- | --- |
| 01 | 7.3 | Failed | The planner reached the 32,768-token output limit on its first step. The native stream ended with a length finish and the workflow had no usable plan. |
| 02 | 21.6 | Review required | The backend compiler reported a Kotlin settings error. Docker reported successful removal, but the verifier did not recognize the exact container's absence and could not authorize a compile repair. |
| 03 | 26.6 | Failed | The implementation worker returned without materializing every approved path. The workflow stopped before the compile checkpoint. |
| 04 | 51.1 | Failed | Both backend repairs ran and the backend compiled. A Vue/TypeScript error entered repair selection, where the selected source context was too large to dispatch the edit. |
| 05 | 44.6 | Review required | Both compile checkpoints passed. The tracked-URL click smoke test received HTTP 403 on the reference. Its first repair left the failure in place, and the second result was rejected for changing the manifest or coverage. |
| 06 | 66.0 | Review required | Both compile checkpoints and the smoke reference passed. The candidate returned HTTP 500 during smoke setup because the expected `/api/auth/register` route was missing. Zero smoke tests ran on the candidate. |
| 07 | 39.6 | Review required | Backend compilation exhausted two repairs. The first fixed the settings script. The second fixed plugin declarations and left Groovy dependency calls in the Kotlin build script. |
| 08 | 90.4 | Review required | Both compile checkpoints and all five reference smoke tests passed. Tracked-URL clicks returned HTTP 409 on the candidate after three application repairs. |
| 09 | 39.5 | Review required | Backend compilation exhausted two repairs in `JacksonConfig.java`. The final patch still declared an invalid `getZone()` override. |
| 10 | 79.5 | Blocked | Both components compiled after automatic repairs. Two smoke repair rounds cleared HTTP 500 on clicks and a missing category `parentId`. All five smoke tests passed with an unchanged suite. Independent test authoring then stopped with `native_completion_unknown`. |
| 11 | 59.3 | Review required | Both components compiled after automatic repairs. The first smoke draft referenced a missing test method. Its replacement reached the reference, where a fixture assignment to `cls.run` shadowed `unittest.TestCase.run()` and crashed the runner. |

All eleven launched runs retained unchanged runtime-source fingerprints and ended without manual rescue. Each inference-backend cleanup receipt recorded a drained transport, a closed bridge, and an exited owned container with PID zero.

## Recovery changes and verification

| Failure observed | Change evaluated between runs | Verification |
| --- | --- | --- |
| Planner output limit | Give the planner a single structured turn with a smaller reasoning budget. Permit one clean restart after a verified length cutoff. | The next launch produced a plan under the original 32,768-token ceiling and reached implementation. Regression tests covered the settled cutoff and bounded retry. |
| Docker cleanup receipt | Recognize Docker's case variations for missing objects and require the reported ID or name to match the owned container exactly. | Cleanup regression tests covered exact absence. Subsequent backend compiler failures produced conclusive receipts and entered automatic repair. |
| Incomplete implementation or oversized repair input | Retain safe partial writes from a settled worker and retry only missing approved paths. Select files from validated compiler locations when available and allow a bounded reselection when a file budget is exceeded. | Workflow tests covered incomplete writes, no-change repairs, source limits, and deterministic compiler scope. Later runs reached and repaired both compile checkpoints. |
| Smoke HTTP 403 and coverage drift | The request helper interpreted the third positional argument as the body. The click test passed its token there, leaving the Bearer header unset. Require explicit token binding and compose repairs from changed test source while retaining the suite manifest, commands and coverage. | The saved request exposed the missing header. Regression tests exercised scoped test repairs and metadata retention. Run 06's fresh suite passed its reference qualification. |
| Missing `/api` prefix | When the same suite passes on the reference, combine the candidate's missing-route log with its controller mappings to request an application repair. | Receipt-based tests exercised repair dispatch and replay of the frozen suite. Run 07 stopped during compilation, before reaching this repair path. |
| Groovy syntax in Kotlin Gradle scripts | Detect the syntax family across affected scripts and request a correction if a partial edit leaves recognized Groovy syntax. | Later live runs repaired both scripts and passed backend compilation. |
| Tracked-URL HTTP 409 | The API's generic integrity handler hid MongoDB error 14. The first attempt sent a string operand. A repair converted the operand to Decimal128 and left the stored value as a string. Subsequent repairs changed collection settings. The runtime now scopes this failure to both the update and the BSON field mapping. | A pinned Docker replay confirmed both type mismatches. A disposable Decimal128 mapping passed the original smoke and trusted concurrent-click checks. A separate timestamp check still failed. |
| Jackson date formatter overrides | Provide pinned JDK and Jackson signatures and request one correction if the draft leaves a recognized formatter API error. | A disposable replay using `JsonMapper.builder().defaultDateFormat(DateFormat)` with `SimpleDateFormat` passed the original backend checkpoint. |
| Independent author completion | The ten native requests settled, including a schema-free request before the final schema-bound response. The adapter treated that intermediate turn as an invalid response. It now recognizes OpenCode compaction, verifies the final schema, and permits one restart of a settled but unusable authoring step. | The installed OpenCode binary passed synthetic loopback probes for compaction followed by a schema-bound response and malformed JSON followed by a clean restart. The journal tests exercised restart and reuse of settled results. Run 11 stopped before independent authoring. [OpenCode compaction implementation](https://github.com/anomalyco/opencode/blob/v1.19.0/packages/opencode/src/session/compaction.ts#L332-L421) |
| Smoke fixture shadowed `TestCase.run()` | Match the complete reference traceback to the string fixture assignment, rename the fixture and its references, then rerun the suite on the reference. | The previously failing suite passed the CPU-only pinned Docker replay with all six coverage entries unchanged. |

The final local runtime test suite recorded 489 tests with 481 passing and eight skipped. All 29 local trial-harness checks passed. The CPU-only Docker and synthetic OpenCode probes were separate from the live Commerce results.

Run 12 was prepared from the brief and passed manifest validation. It remained unstarted when GPU usage paused for the day. No fresh run from November 19 reached final independent verification, production-mode regression or the full final API-restart profile.
