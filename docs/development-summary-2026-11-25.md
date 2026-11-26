# Workflow development summary November 25 2026

Run 25 reached **completed** today. Continuation 15 finished in **3 hours 22 minutes 53 seconds without operator intervention**, passed every required gate and delivered 91 files: 79 application files and 12 frozen independent test files. The closed-run audit verified 4,936 frozen inputs, 105 checkpoints, 85 completed native requests and one actual OpenCode auto compaction. The owned backend stopped with PID zero, drained transport and a closed bridge; the GPU was released.

This summary covers November 25 in Pacific time: the resume after reboot, five further continuations, the recovery changes between attempts and the successful delivery. This was a checkpoint continuation of Run 25, retaining previously completed application and test work. The [detailed recovery investigation](metadata-repair-recovery-2026-11-25.md) and [completion report with evidence hashes](../benchmarks/reports/run25-context-observation-2026-11-25.json) retain the individual controls and outcomes.

## Recovery changes

- **Distinguish metadata rejection from failed code.** The repeat guard now records whether a proposal failed metadata validation, composition, preflight, affected-case execution or full reference qualification. Identical executable edits can be reconsidered when every matching prior rejection was metadata-only and the corrected metadata passes validation. Executable failures and unsupported legacy repeats remain blocked; history is preserved.
- **Make consultation advice executable and complete.** Correction advice requires a nonempty list of concrete requested changes and an empty missing-capability field. Capability requests must describe the missing capability. Each native action branch now contains all common required properties and the exact evidence enum. An empty-string enum preserves the correction constraint when the native adapter drops string length bounds.
- **Keep current failure evidence across retries and checkpoints.** Rejected patches now retain diagnostics for all failing cases, including cases whose identities remain the same but whose failure modes change. Continuation preparation reconstructs this feedback from settled producer and verifier receipts. Consultations receive bounded recent diagnostics while retaining complete permitted source and unchanged completion/framing reserves.
- **Observe failures in the full test context.** A bounded diagnostic captures public DOM state after selected failed cases in the original complete test order. It records page text, rows, headings and navigation controls without changing mounted application/test files, assertions or fixtures. Observations require pinned source, profile, inventory and cleanup proofs, are cached per suite and receive no acceptance credit. Unusable settled observations are reported as unavailable; unresolved execution blocks further work.
- **Apply the explicitly requested 16/16/16 limits throughout recovery.** Shared output corrections, the total ordinary test-defect cap and coordinated validations now support 16 each. Both repair paths use the same validation pool. Runtime checks, checkpoint validation, finite stage admission and repair/history namespaces honor the expanded grant. Earlier approvals retain their original limits. The two inherited ordinary defect attempts stayed counted; historical shared costs remained archived.
- **Resume accepted work without recreating it.** Continuations preserve the settled application, accepted independent suite and promotion history. A failed attempt that made no test changes can retain its parent's accepted suite. Every new approval binds the closed-run audit and latest checkpoint, preserves spent costs and requires the original downstream gates.

All five roles in these trials used pinned Qwen3.8-27B through OpenCode with auto compaction. Model weights, sampling, the 98,304-token context and 65,536-token completion ceiling were retained. Operators changed recovery code between closed attempts; they did not manually repair generated application or test source during unattended execution.

## Outcomes today

| Attempt | Terminal outcome | Main result |
| --- | --- | --- |
| Continuation 10 after reboot | Failed after 10m28s | Saved work resumed successfully. A correction removed duplicate evidence metadata, but the repeat guard rejected its unchanged executable edits before evaluation. |
| Continuation 11 | Failed after 29m10s | One automatic repair reduced reference failures from three to two out of 49. Later advice omitted executable requested changes and failed its bounded retry. |
| Continuation 12 | Failed after 15m04s | Advice supplied action-specific fields but omitted common diagnosis/evidence fields. No patch was dispatched. |
| Continuation 13 | Failed after 46m52s | Four proposals reached full reference qualification without reducing the remaining two failures. The next advisor lacked changed diagnostics for those same cases. |
| Continuation 14 | Failed after 42m22s | Richer diagnostics reached the agent, but its proposals still failed the complete reference suite. No patch was promoted. |
| Continuation 15 | Completed after 3h22m53s | The ninth proposal qualified all 49 cases. Three automatic application repairs then cleared candidate, supplemental and blocking review failures. All remaining required gates and delivery passed. |

The full-order diagnostic showed the category and tracked-URL tables displaying their first ten rows out of 11 and 12 respectively, with next-page controls enabled. The successful ninth proposal used native element clicks to navigate pagination and targeted the tracked URL fixture's own row action. The first eight proposals were rejected or rolled back. The successful attempt spent eight shared corrections and nine validations, continuing beyond the former four/six ceilings. Its ordinary defect counter ended at three, including the two inherited attempts.

After qualification, application repair 1 corrected post-login navigation. Repair 2 addressed supplemental UI failures involving logout, dashboard/report labels and affiliate selection; OpenCode compacted context during this work. Repair 3 addressed the blocking code-review finding that a tracked-URL edit could overwrite concurrent click increments. These were native workflow repairs under frozen tests.

## Verification and delivery

The final application passed:

- All **49 independent cases** and **14 supplemental cases**, including final supplemental verification.
- Backend/frontend builds, trusted API and held-out checks, and trusted persistence checks.
- Candidate source checks for packaging, environment configuration and development instructions.
- The production DOM verification profile and final smoke verification.
- All **13 code-review chunks**, with **zero P0/P1 blocking findings**.
- Persistence verification before and after an owned API restart, final acceptance and every delivery-file hash check.

The final implementation checks passed **694 core tests: 686 passes and eight optional skips**, plus **38 native-adapter tests**. CPU controls verified retained source and cost floors, grant tamper rejection, the sixteenth repair/validation boundary, unchanged test execution order, diagnostic source bindings and actual prompt admission. Some earlier test attempts exposed host Git configuration or Windows socket behavior; the complete final suites passed.

Code review retained **five P2 and four P3 nonblocking model findings**: shared CORS configuration, blank-login response status, frontend 403 handling, dark-theme activation, duplicate Enter submission, empty validation messages, username whitespace normalization, dashboard behavior when one request fails and an extra product-table column. These are reviewer judgments, not nine independently confirmed defects; the [completion report](../benchmarks/reports/run25-context-observation-2026-11-25.json) records their locations. The original blocking threshold remained P0/P1.

The approved offline image had no real browser; production UI coverage used DOM checks. Python mutation scoring does not cover this Java/Vue stack. The persistence profile restarted the API while MongoDB stayed running; this completed trial did not reboot the computer. The earlier saved workflow did resume after an actual reboot.

The combined delivery digest is `1104c5a74b6e4f049ef18092c90ae78fc92b16c63a8533785d651ef526230eb2`. Raw journals, private reference source, prompts and deployment-specific manifests remain outside this public repository. The public reports contain derived results and evidence hashes.

## Published changes

| Commit | Change |
| --- | --- |
| `bc598aa` | Record the resume after reboot and audited repeat-guard failure. |
| `eadc125` | Separate metadata rejection from executable repeats and retain checkpoint repair epochs. |
| `2b90e9f` | Require executable change instructions in recovery advice. |
| `d978a37` | Serialize complete advice branches and retain unchanged checkpoint suites. |
| `48a3c0c` | Preserve changed diagnostics for existing failed cases across retries and continuations. |
| `6bfc4a9` | Expand repair capacity and collect full-order public UI context. |
| `a107d90` | Publish Run 25's unattended completion, audit and verified delivery. |

The successful terminal outcome ended the Run 25 repair/restart cycle. No further Run 25 continuation was started.
