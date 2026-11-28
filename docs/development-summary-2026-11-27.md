# Workflow development summary, November 27, 2026

The prompt-driven workflow produced **no end-to-end Snake delivery in 12 fresh GPU runs**. Ten runs reached implementation. Two passed their own smoke tests against the candidate and reached independent test authoring. Neither finished qualifying the independent suite, so neither reached final candidate verification or code review. The last run was paused on request during its second plan check, with six checkpoints saved and the GPU backend shut down.

This report covers the November 27 development session in Pacific time. An earlier Snake build completed on November 25 after four checkpoint continuations. That result established that this toolchain could deliver the game. The November 27 experiment asked whether a fresh run of the revised workflow could do so from a single prompt, with the agents handling recovery. [Earlier Snake results](snake-end-to-end-2026-11-25.md)

## Where the time went

The 12 launches used the same prompt and started from empty project state. They recorded 98 model calls and 215 checkpoints. Nine runs failed, one returned for review, one was paused after four attempts to locate a review finding, and the last was paused on request.

| Phase | Model calls | Elapsed time inside journaled calls |
| --- | ---: | ---: |
| Planning, plan review and planner revisions | 44 | 1h 19m 25s |
| Implementation, including one retry to complete the file inventory | 11 | 39m 01s |
| Smoke-test authoring, draft review and repair | 34 | 1h 53m 05s |
| Independent test authoring and repair | 9 | 30m 55s |
| **All phases** | **98** | **4h 22m 25s** |

Summed launch-to-shutdown time was **4h 55m 38s**. The table sums the journal start-to-finish interval of each model call, assigning corrections to the phase that requested them. These intervals include worker and transport time. The remaining **33m 13s** includes startup, sandbox checks, orchestration and shutdown. The measurement excludes time spent changing the workflow between runs. Individual rows are rounded to the nearest second.

## What changed and what worked

- The planner now receives each independent review's outstanding findings and revises the plan until it passes or exhausts its approved allowance. In one GPU run the checker reported seven findings, then two, then zero. Implementation started after the third check.
- The workflow assigns implementation, independent testing and code review to fresh sessions of the same model. Findings from testing or review route back to repair. A code change returns to independent testing before delivery. CPU tests exercised the review-to-repair-to-retest path. The November 27 GPU trials stopped earlier.
- Repair now tries a fresh proposal before spending the last no-progress cycle. The host can retain an unaccepted working draft when verification exposes a different exception in the same failing cases without regressions. A CPU regression exercised two successive fixes against that evidence. In a GPU run, the existing qualification loop reduced four failing smoke cases to three and then zero. The candidate subsequently passed smoke testing.
- Work on generated browser tests separated observations from protected checks, preserved executable source when shortening explanatory text, and reported which source regions a rejected edit touched. A new pre-execution review caught a check that claimed to cover a 375-pixel viewport while running at 960 pixels. It also caught an assertion that always passed because it contained `|| true`.
- The reviewer originally had to quote unique source text to identify a finding. After four failed attempts on the same review, the interface changed to numbered source lines. The host binds the selected range to exact source bytes. CPU tests verified that a correction could select the second of two identical lines and leave the first intact. The last GPU run paused before reaching that new interface.

The final core CPU sweep ran 794 tests with eight skips and no failures. That establishes coverage of the individual mechanisms. End-to-end GPU delivery remains unverified for this revision. The implementation changes described here are on the local development branch. This publication contains the report.

## Why a small game consumed an evening

**The workflow spent more time establishing that its tests were valid than writing the game.** The brief specifies precise browser behavior, including keyboard input between ticks, randomized food placement and persistent scores. Each fresh run asked the model to write a smoke suite and, if it got that far, an independent acceptance suite. Each generated suite became another program to debug through Python, Node, Playwright and Chromium. Calls during those two phases took **2h 24m**. The ten implementation attempts together took **39 minutes**, including the inventory retry. Candidate defects may still exist. No run reached the final checks that could establish the game's quality.

**Some findings had no legal repair by the time the agent received them.** One generated test formed a set from every nonempty cell, including the food, and then required the food to be absent from that set. A correct game must fail that assertion. Once adopted, the check was protected, and changing game code or faithfully observing the board could never satisfy it. The appropriate intervention is to review the draft predicate against the public contract before adopting it. After adoption, the protection serves its purpose.

**The repair protocol added work even when the agent understood the problem.** A reviewer correctly identified which method used the wrong viewport. The exact line occurred in three methods. The host repeatedly rejected the report because the quotation alone could not identify one occurrence. In another run, a repair proposal was rejected because its explanation exceeded the length limit. Replaying that response showed that the explanation could be shortened while preserving the proposed source and its evidence references exactly. Both problems can be resolved by the host without asking the model to rediscover the defect.

**Our development method multiplied those costs.** We repeatedly changed the runner in response to a generated test failure and used a fresh GPU run to check the change. That bought a clean independence test, but it also recreated the plan, implementation and tests before reaching the failing boundary. The 215 checkpoints preserved evidence and in-run progress. All 12 launches explicitly started fresh, so earlier accepted work was outside the next run's starting state. A checkpoint also cannot make a contradictory assertion satisfiable.

## What to change next

1. **Use recorded failures as the everyday integration test.** Replay a failing stage through the real host validator with its original inputs. Exercise success, repeated failure and fallback on CPU. When live inference is needed, use a compatible, verified checkpoint to test that boundary. Reserve a fresh prompt-to-delivery run for the release check. Carry forward source digests, approvals and spent costs.
2. **Give the repair owner one actionable finding at a time.** Include the exact revision, failed check, observed result and editable location. Let the host resolve line ranges, preserve unchanged bytes and format the patch. Send the next verifier result directly back to the owner. If the same state recurs, change repair strategy before paying for another identical request.
3. **Reduce the amount of test infrastructure a model has to invent.** Put reusable browser launching, clock control and observation capture in a versioned public harness. Keep the smoke suite small. Have the fresh independent tester choose meaningful scenarios and expected outcomes from the brief. Review those expectations before freezing them, then run the frozen checks after each application repair.
4. **Measure delivery and recovery separately.** For each workflow revision, record time to first runnable candidate, time to qualified independent tests, repeated calls per finding, and time to a reviewed delivery. Require a clean end-to-end pass and a separate controlled run that demonstrably recovers from an injected defect. Together those results would show both that the workflow can finish and that its repair loop earns its cost.
