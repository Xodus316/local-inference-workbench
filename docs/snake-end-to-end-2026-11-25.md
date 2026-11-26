# Snake end-to-end test results

Test completed November 25, 2026, Pacific time. Detailed results published November 26.

The fresh browser Snake project **completed** on continuation 4. The final trial
ran unattended for **34m17s**, passed every required gate and delivered nine files.
Code review returned **zero findings and zero blockers**. The owned model backend
stopped, its transport drained, its bridge closed and the GPU was released.

Getting there required **four orchestrator repair rounds after failed attempts**.
Including the recovery-budget change prepared before the first launch, there were
**five orchestrator changesets**. There were **five execution attempts**: the
initial trial and four checkpoint continuations.

## Test scope and models

The task was a new, static Snake game built with vanilla HTML, CSS and JavaScript.
The public contract covered a responsive 20×20 board, 150ms movement, arrow/WASD
steering, reversal and turn limits, food and growth, wall/body collisions,
vacating-tail movement, pause/resume, restart, accessible controls and persistent
best score. The plan, application, smoke suite and independent suite started empty;
no Commerce application or test source was imported.

| Role | Model | Agent transport |
| --- | --- | --- |
| Planner | Qwen3.8-27B | OpenCode |
| Implementer | Qwen3.8-27B | OpenCode |
| Independent test author | Qwen3.8-27B | OpenCode |
| Repairer | Qwen3.8-27B | OpenCode |
| Reviewer | Qwen3.8-27B | OpenCode |

Automatic compaction was enabled for every role. Model weights, sampling settings,
98,304-token context and 65,536-token completion ceiling stayed unchanged across
the continuations. One OpenCode compaction completed during the successful trial.

The verifier used real Chromium in a pinned offline image. Its trusted browser
checks were calibrated against a correct reference and a deliberately broken
scoring implementation before launch. Those CPU controls earned no acceptance
credit. The reference implementation was not supplied to the workflow agents.

## Attempts and terminal outcomes

| Attempt | Terminal outcome | Execution time | Reason for stopping |
| --- | --- | --- | --- |
| Initial trial | `review_required` | 24m34s | Smoke qualification exhausted its separate two-repair allowance. |
| Continuation 1 | `failed` | 3m58s | A structured smoke patch used an unsupported worker/stage route. |
| Continuation 2 | `failed` | 1h40m32s | A verifier-specific diagnostic probe was unavailable; repeated patch requests consumed the consultation allowance and ended at the repeat guard. |
| Continuation 3 | `failed` | 28m14s | A corrective patch had an invalid evidence reference and escaped the original correction loop. Independent reference failures had fallen from seven to one. |
| Continuation 4 | **`completed`** | **34m17s** | All required gates and delivery passed. |

Times are individual execution durations, rounded to the nearest second. They
exclude investigation, code changes, regression tests and preparation between
attempts; 34m17s is the successful continuation's time, not the whole experiment.

Each attempt ran without operator intervention. After a failed attempt terminated,
its frozen inputs, checkpoints, settled native requests and owned-resource cleanup
were audited before an orchestrator fix and a separately frozen continuation.
Application/test source, accepted progress and spent costs were retained. Renewed
shared recovery epochs recorded the earlier costs; ordinary repair counters stayed
charged. No generated application or test source was manually edited.

## Orchestrator fixes

| Change | Issue and resulting behavior | Commit |
| --- | --- | --- |
| Prelaunch budget fix | Fresh approvals did not carry the requested shared 16 output corrections, 16 test-defect repairs and 16 coordinated validations. Added an explicit, approval-bound grant. | [`a07ecd2`](https://github.com/Xodus316/local-inference-workbench/commit/a07ecd2) |
| Repair round 1: smoke recovery | Smoke qualification retained a separate two-attempt loop, and outer Python assertions hid nested browser errors. Connected smoke repair to shared anchored recovery, retained hash-bound nested diagnostics and added an audited smoke-checkpoint continuation. | [`b0c9238`](https://github.com/Xodus316/local-inference-workbench/commit/b0c9238) |
| Repair round 2: worker routing | The first smoke-recovery change dispatched ordinary structured patches through the implementer/base tool route. Corrected this regression to use the structured test-author route and tested the ordinary dispatch path. | [`a4bc137`](https://github.com/Xodus316/local-inference-workbench/commit/a4bc137) |
| Repair round 3: verifier compatibility | The selected-case probe imported a private Commerce verifier module absent from the Snake image. Missing verifier evidence was treated as correctable model output. Diagnostics now run the complete approved verifier; unavailable evidence stops output rewriting. Inconclusive verifier attempts can be revalidated without erasing history. Checkpoint replay also separates earlier smoke patches from later independent authorship. | [`533cfde`](https://github.com/Xodus316/local-inference-workbench/commit/533cfde) |
| Repair round 4: diagnostic feedback and patch schemas | An early no-progress rejection omitted the rejected candidate's new failure details, including `d0 is not defined`. A later invalid evidence reference lost the original correction capability. Rejected-candidate diagnostics now retain source/receipt bindings, and settled malformed patches return through the original bounded validator. Unknown execution still stops recovery. | [`033ce78`](https://github.com/Xodus316/local-inference-workbench/commit/033ce78) |

Repairs remained bounded and required fresh verification. A proposal could not earn
promotion by skipping tests, changing identities or coverage, waiving a gate,
altering models/budgets or claiming a pass from advisory diagnostics. Captured
failures from a rolled-back proposal described that rejected source, not the
retained checkpoint.

## Successful continuation

The agents resolved the final independent-test defect, bringing reference failures
from one to zero without regressions. Candidate verification then found one
best-score persistence failure. The workflow made one application repair: opening
food avoids the cell immediately ahead of the starting head. Subsequent food
spawning and the rule of +10 points per food remained unchanged.

| Required gate | Result |
| --- | --- |
| JavaScript syntax/compiler checkpoint | Passed |
| Independent tests | All 22 passed |
| Smoke tests, including final smoke | All five passed |
| Trusted Chromium browser checks | All 15 passed |
| Ordinary and profiled reference checks | Passed |
| Mobile candidate verification at 375×812 | Passed |
| Code review | Passed; zero findings or blockers |
| Final acceptance | Passed |
| Delivery | All nine file hashes verified |
| Owned backend cleanup | Stopped; transport drained; bridge closed |

The final audit verified **970 frozen files**, **27 checkpoints** and **16 completed
native model requests**, including **one compaction**. There were six journaled
workflow operations; an OpenCode operation can contain several native requests.
The delivery contained four application files and five independent-test files.

The latest recovery implementation passed **708 core tests: 700 passes and eight
optional skips**. The native-adapter suite passed **38 tests** before the final
feedback-only changes. Additional CPU controls checked source/cost preservation,
tamper rejection, complete browser diagnostics and carried rejected-patch evidence
without model inference or acceptance credit.

## Evidence and limits

The [machine-readable report](../benchmarks/reports/snake-recovery-2026-11-25.json)
contains the attempt history, control results and final outcome. Its top-level
`status` and `terminal_outcome` describe completion; legacy top-level fields such
as `terminal`, `elapsed_seconds` and `candidate_behavior_not_yet_validated` describe
the initial attempt. The `continuations` array records each later attempt.

| Final artifact | SHA-256 digest |
| --- | --- |
| Application | `d8fbe746da29bc281c079313d8fe9a86b0e413b07a72e26b0ffe856d7af5f6ac` |
| Frozen independent suite | `3c04139ae222e2d6ddb6ebf43166a786b106eff6db35d5812d5d4597071b7f25` |
| Combined delivery | `adb52e6a93c15d5c628095f4069b43e67adef60cd2cd63665860e925769a6769` |

The successful runtime was commit `033ce782359b2a98dee7410208a70e887c681ee6`.
Raw journals, private reference source, deployment-specific manifests and generated
delivery files remain outside this public repository. This test demonstrates one
completed browser application with the declared verifier and gates; it does not
establish coverage of every application, browser engine or possible recovery fault.

See also the [November 25 development summary](development-summary-2026-11-25.md#later-work-a-fresh-browser-snake-project)
and [whole-project recovery documentation](whole-project-builds.md).
