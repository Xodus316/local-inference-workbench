# Commerce workflow completion — November 18, 2026

The Commerce campaign completed its declared end-to-end workflow with HyperQwen. The final application passed fresh independent tests, ordinary and production-mode UI/API regression, final smoke tests, full source review under the existing blocking-finding policy, API-restart persistence, delivery checks and all 19 final audit sections. Completion was recorded at **22:35:40 Pacific on November 18** (06:35:40 UTC on November 19). The owned GPU backend was stopped and its physical cleanup verified.

This was a **successful continuation of an iteratively repaired 76-file project**, not a fresh build from an empty repository or an unassisted one-shot result. Application patches came from recorded native model workers; Codex changed the experimental orchestration, test transport, verification and evidence machinery. This publication adds documentation and derived measurements, not a deployment of those experimental changes into the public Workbench runtime.

The [November 17 report](commerce-workflow-evaluation-2026-11-17.md) remains the historical record of the earlier 8/14 regression stop. The [November 16 report](commerce-recovery-evaluation-2026-11-16.md) covers the retained implementation and initial backend recovery. [Machine-readable results and selected receipt hashes](../benchmarks/reports/commerce-completion-2026-11-18.json) accompany this report.

## What passed

| Final gate | Result | Scope |
| --- | --- | --- |
| Fresh independent suite | 13/13 | The separately authored, frozen acceptance suite |
| Ordinary supplemental regression | 14/14 | The qualified amendment of the original 14 cases; rerun before review and at the final gate |
| Production-mode supplemental regression | 14/14 | Same frozen test bytes against the candidate, after fresh ordinary-reference and production-reference controls each passed 14/14 |
| Final smoke suite | 4/4 | Four cases, not six; coverage metadata is not a test count |
| Full source review | 14 groups covering 76 application files | Zero P0/P1 findings; 28 retained nonblocking notes: 15 P2 and 13 P3 |
| Full API-restart persistence | Passed on reference and candidate | Records, authentication, clicks, revenue, timestamps and reports survive an owned API restart |
| Final evidence audit | 19/19 sections | No deficiencies after the terminal-bound finding disposition was recorded |
| Delivery and cleanup | Passed | 76 application files plus six frozen test files; owned inference and verification processes settled and cleaned up |

These are distinct, partly overlapping and repeated batches. They must not be added into a single unique-test count or pooled pass rate. The UI checks used real application components and HTTP services in pinned Vitest/jsdom environments. **Real-browser, driver and visual-layout evaluation remained unavailable.** Production-mode here means verified production library behavior under `NODE_ENV=production`, not a browser deployment certification.

The final implementation digest is `fb857681bb3d2137cc89545722a08275f7759682a63ec23b768e98829ee746a4`. The delivery digest is different because delivery also includes the frozen tests. The final audit receipt is `641b99db7ac0c36d5f0841822097de9a3b355f438913a64419d898eeb2f1c616`.

## What had to change at each workflow step

### 1. Planning: make the contract and file inventory executable

The abandoned first attempt invented an integer-timestamp requirement where the brief required millisecond precision. Its interrupted implementation was not reused. Another plan returned prose where the workflow needed bare file paths. The coordinator clarified the timestamp contract and added canonical-path validation with a bounded correction for a settled malformed plan.

The retained seed then produced 12 criteria, 76 paths and 30 implementation steps. Its planner used a task-only, no-tools request, followed by one whole-project implementation stage. This is a different lineage from the earlier 57-file/14-criterion experiment. Planning, initial implementation and initial smoke authorship used Qwen 3.8 27B AutoRound-fast W4A16 through HyperQwen/vLLM and OpenCode at medium reasoning with a 98,304-token context.

### 2. Implementation and compilation: give repair agents reliable compiler context

The retained implementation returned all 76 files, but the backend did not compile. The first repair selector repeatedly read files through tools; its replacement exhausted its output allowance. Subsequent native patches still guessed incorrect Java dependency APIs.

The selected recovery path used direct selection from verified compiler diagnostics, a bounded source projection, public API signatures from the verifier's actual pinned dependencies, and removal of stale invalid advice. Native Qwen application patches then cleared backend and frontend compilation. Completed source was retained; unknown interrupted worker output was excluded. This recovered implementation was not counted as a fresh generation success.

### 3. Smoke verification: qualify tests before allowing them to repair the application

Early smoke drafts failed in their own fixtures or assumed unauthenticated access and incorrect concurrent-response behavior. A test failing before any case ran was not an application defect. The harness added fixture checks, compact context, native reasoning/completion bounds and a required reference pass before a test could drive application repair.

The combined smoke-stage verifier then exposed real backend defects. Native repair removed an invalid nonblank requirement for an optional tracked-URL label. At that point the four smoke cases passed, but four trusted held-out checks still failed. Further native repair corrected tracked-URL defaulting and updates and returned the required report aggregate fields. A strict self-repair loop prevented handoff to independent testing while any required check was still failing. Compiler, self-repair and final-verification workspaces and counters were separated.

### 4. Independent testing: fix the test author's contract, selectors and assertions

The independent author initially missed a real criterion and cited a nonexistent one. Exact numbered criteria and preserving coverage corrections made that handoff checkable. Gemma progressed but stalled on Products and Reports. North Mini Code reached diagnoses but did not produce an accepted correction in the matched comparisons. Qwen produced useful corrections, so the campaign continued with Qwen, eventually at xhigh. Those small, assisted comparisons do not establish a general model ranking.

The test path needed several concrete fixes: parse the permitted timestamp representations correctly; use the actual Products Save action rather than assuming native form submission; recognize a unit suffix such as `Price (USD)` while preserving exact-match priority and rejecting ambiguity; and assert Reports values by their corresponding headers and rows. A shorter materialization path resolved Windows path-length staging without changing application or test bytes. Label normalization was a helper-only image migration, separately qualified against the reference with both suites while preserving application and test bytes. The strengthened Reports oracle had to reject numeric-substring matches, missing required text and non-USD values. The independent suite was frozen at 13 cases after reference and synthetic-control qualification. Arbitrary aliases such as treating Create and Save as interchangeable were not introduced to hide behavior differences.

### 5. Supplemental regression: repair brittle UI tests without deleting their requirements

The original 14-case suite had been restored as a separate gate, including the previously quarantined logout case and removal of rejection suppression. It still exposed failures after the independent 13 passed. Some were genuine application gaps; others came from brittle Edit/Delete discovery and dialog handling. A separately controlled amendment preserved all 14 case identities and their discriminating assertions. Native Qwen JavaScript added row-scoped accessible-control discovery, a constrained icon fallback, real cancellation/consent handling, bounded polling and cleanup. Five Python definitions changed in `tests/test_ui.py`; the other two test files stayed unchanged. The amendment did not inject application controls.

Getting those amendments through the agent protocol also required harness work. The path moved from exact line edits to complete Python definitions and finally to JavaScript slots with deterministic Python-string packaging. Parse-only syntax feedback, current-source and applied-state facts, exact target/digest binding, ordered final-content capture and admission receipts made failures diagnosable. A decoder-facing string `minLength` constraint blocked valid escaped content in pinned grammar probes; it was removed from that schema while the original runtime validation remained. Repeated historical context was compacted with omissions declared. Supplemental source capacity increased from 32 KiB to 48 KiB; the independent suite retained its 32 KiB limit.

The accepted amendment is **native-authored JavaScript with deterministic packaging**, not a claim that every packaged Python byte was literal model output. Rejected patches were not manually repaired and relabeled as native. The final amendment passed **53/53 synthetic outcomes: 24 positive and 29 negative**, plus a fresh reference 14/14. Its lineage retains 25 spent amendment rounds and 46 settled native requests, including one historical length termination. These are cumulative amendment costs, not final application-test counts. The helper's finite timing windows remain a documented robustness limitation; passing selected controls does not prove every possible false pass is impossible.

### 6. Application repair: fix behavior revealed by the qualified tests and review

Earlier native application repairs corrected Products' action/layout, Reports' headings and response-field handling, and Dashboard/shared types. Full review then drove four additional mechanisms: duplicate persistence errors map to HTTP 409; the frontend forwards an optional custom tracked URL; the tracked-URL form exposes, populates and submits that field; and targeted updates preserve concurrent click/revenue data and metadata. Final source reconciliation retained all four fixes. The concurrency-preserving mechanism was checked in source; this report does not invent a separate targeted race benchmark.

The later supplemental suite ran **before** the expensive full review, with fresh independent tests after application changes:

| Native repair | Application change | Ordinary supplemental result |
| --- | --- | --- |
| Starting checkpoint | All seven API cases, Products UI and Reports UI passed | 9/14 |
| Repair 4 | Existing Affiliates primary action caption became Save; handler unchanged | 10/14 |
| Repair 5 | Categories and Tracked URLs Save captions; Dashboard totals actual `clickCount`; login 401 is exempted from the protected-resource logout interceptor | 13/14 |
| Repair 6 | Removed an extra required password-confirmation gate; retained real registration, authentication and session handling; added guarded API-base fallback and actual Logout controls through route wrappers | 14/14 in ordinary mode; subsequently failed production mode |
| Repair 7 | Router lazy loader returns an object render component instead of a bare function | 14/14 ordinary and 14/14 production; fresh independent 13/13 |

The login-interceptor change did not itself resolve registration. The API-origin and auth-race hypotheses were not established as root causes. Out-of-scope edit attempts were denied rather than silently broadening the writer's authority. Repair 6's new router wrapper introduced a real production defect, so its ordinary passing result was not accepted as completion.

### 7. Full review: make citations dependable without rewriting the reviewer's findings

Review initially failed because generated excerpts did not match the source exactly. The adapter changed from model-copied path/line/excerpt text to source-bound anchor IDs, expanded mechanically into exact citations. Full numbered source and the anchor registry remained available; raw and expanded outputs were preserved. A bounded companion-source rule supplied relevant shared-store context.

The final review covered all 76 files in 14 groups. One normal response had a 949-character description against a 900-character limit. A bounded native correction shortened it to 529 characters while retaining the same anchor, category, P2 severity and allegation. The coordinator did not rewrite or downgrade the finding. The final 28 notes are review observations, not 28 independently confirmed bugs.

Fresh role sessions used the same Qwen weights. That provides session separation, not independent model-family review. More significantly, the preceding full review had missed the router defect that production execution later reproduced. A clean blocking-finding review was therefore necessary but insufficient for completion.

### 8. Production verification: reproduce and repair a bug ordinary tests missed

Repair 6's lazy route loader returned a bare functional component. In the pinned Vue Router, a cached unmarked function could be mistaken for another lazy loader. On navigation, production code then attempted `.then` on a VNode. Development behavior masked the distinction. A framework probe isolated the mechanism, but it was only diagnostic evidence; it did not replace application testing.

The first production-mode application probe failed on the reference before application import: Vite externalized the test bootstrap's `node:fs` read. The reference barrier held, and the candidate was not run. A generic, readonly runner overlay moved the real immutable HTML read into Node bootstrap and safely embedded it in the DOM helper. It did not change the frozen application or test bytes. The base verifier image stayed pinned, but the overlay and environment changed the effective runtime and were identified separately.

With both ordinary-reference and production-reference controls passing 14/14, the candidate passed all seven API cases and failed all seven UI cases with the router error. That confirmed a production P1 despite ordinary 14/14 and a review with no blocking findings. The run was frozen and closed at a known native boundary. Its source, costs and unresolved finding were carried forward.

Native repair 7 changed only the router file. Returning an object with `render()` preserved the loaded views, real Logout action, six protected routes and guards while removing the ambiguous cached-function behavior. The other 75 application files were unchanged. Fresh independent, ordinary and production checks then all passed. Resolution required current-source execution and final finding reconciliation, not merely a changed source hash or an old passing receipt.

### 9. Persistence: test an actual restart and repeat it on the delivered source

Ordinary create/read-back was insufficient evidence of persistence. The companion probe owns a database and two successive API processes, verifies that the first API has exited, then checks a fresh login, all four resource types, three recorded clicks and $7.50 revenue, millisecond timestamp preservation and both report aggregates. Reference-first qualification also caught defects in the probe's timestamp assumptions and log collection.

The full probe was rerun on the final exact candidate, with complete bounded logs and cleanup evidence. MongoDB remained running during the API restart. Database restart and power-loss durability were not tested and are not claimed.

### 10. Recovery, acceptance and delivery: preserve failure history and close every gate

Recovery had to preserve immutable source, test and runtime identities, settled native-call records, unresolved findings and spent budgets across GPU pauses and closed attempts. Earlier infrastructure changes also increased Docker startup's bounded timeout after a measured cold-start delay and reconciled ownership before deciding whether dispatch had occurred. Unknown or cancelled outcomes never earned passing credit.

The successful continuation froze 728 inputs, retaining all 709 parent pins. It inherited six spent general repairs and used one more, finishing at **7 of 8** in that continuation budget, with two earlier self-repairs retained. These counters do not erase earlier compile, authoring or recovery costs. The final continuation had 17 journal calls and 24 known native requests; that is not the whole campaign's cost.

Acceptance required the current independent and supplemental tests, current production proof, complete review, final smoke/regression, full restart persistence, historical and external finding reconciliation, delivery binding and owned-process cleanup. The first audit passed 18 sections but preceded the terminal-bound external-finding disposition. That record was added and the unchanged pure auditor reran all 19 sections successfully. No new model generation or application rerun was needed for that bookkeeping correction, and the initial audit was retained.

The GPU backend was confirmed stopped at 22:29:50 Pacific; physical verification observed 23,079 MiB free afterward. This is an observation after releasing the owned backend, not a claim that every other process on the machine was idle.

## Model and routing comparison in the same work period

The final Commerce roles used **Qwen 3.8 27B AutoRound-fast W4A16 served by HyperQwen/vLLM**, with OpenCode for application-writing tools and separate native review calls. The active final roles used xhigh reasoning, a 98,304-token context, 32,768-token completion cap and 16,384-token reasoning budget. Sampling was temperature 1, top-p 0.95, top-k 20 and neutral penalties. The backend used xgrammar and three-token probabilistic MTP. This was not Ollama's 37B model or Qwen3-Next IQ3.

A separate requested comparison reran the frozen seven-file frontend task with Strata/Flash-Next routing switches explicitly off. It contributes no Commerce acceptance credit:

| Matched frontend request | Observed result | Accepted files and checks |
| --- | --- | --- |
| Historical Flash-Next | Hit the 1,800-second deadline | Zero accepted files; compiler not run |
| Historical HyperQwen | Normal stop in 632.914 seconds after startup | Seven files; build and typecheck failed |
| Flash-Next, explicit routing switches off | Runtime reported a length stop after 1,017.659 seconds | Zero accepted files; compiler not run |

The new Flash request had 9,922 input tokens and used the matched 64K context, intended effective xhigh, sampler and deadline. Tail skipping, route prior and resident routing bias were explicitly zero. Historical tail skipping was already zero and the prior was unspecified, so this was not a clean enabled-versus-disabled experiment. RAM cache also differed: 17.79 GiB versus 10.44 GiB historically. Historical HyperQwen used different guidance/speculation settings from the later Commerce run.

The native Flash log recorded 55,606 generated tokens in 970.390 seconds, but its full response was not retained after a capture error. The saved source/error path points to the capture-size limit; the exact lost serialized size was not measured. The content, reasoning/answer split and complete request-bound protocol therefore cannot be independently replayed. A prior preparation attempt dispatched no generation because an adapter hash check failed. Cleanup was verified. These outcomes support neither a causal routing-speed claim nor a clean quality ranking; final Commerce success does not turn the historical HyperQwen frontend draft into a passing build.

## What remains

The declared offline/API/DOM workflow is complete. The following remain distinct follow-up work:

- **Optional tracked-URL labels:** a documented, unresolved P2 concern remains: PUT retains the prior label when null or omitted, alongside UI assumptions about nonblank/nullable values. The assessment is grounded in source; the brief does not separately spell out missing-versus-null replacement semantics. Existing tests cover creating without a label and updating to a nonempty label, not clearing it, and this edge was not separately executed. The concern was retained under the existing policy that P0/P1 findings block acceptance; it was not silently marked fixed.
- **Other review notes:** triage the retained P2/P3 observations on their merits. Some depend on stronger guarantees than the brief requires. A note is not automatically a reproduced defect.
- **Browser and robustness coverage:** add actual browser/navigation/layout checks, broader dialog-timing controls and any desired database-restart or durability checks as new evidence.
- **Fresh-run and broader-task validation:** repeat from the brief without importing the repaired application, then use other coding challenges to establish whether the workflow generalizes. One highly instrumented recovery campaign does not establish reliability across projects.
- **Runtime integration:** review and port the selected generic orchestration changes into the maintained runtime, with its normal regression checks. This documentation push does not perform that integration. Prepared but unused experiments, including the optional generic failure-context extractor and a later Strata capture fix, receive no credit for the successful run.

## Evidence and publication limits

The accompanying JSON contains allowlisted counts, configuration, outcomes and original receipt SHA-256 identities. Raw prompts, model responses, private reference source, operational identifiers and host paths are not published. Hashes identify retained evidence but do not themselves provide public replay. Earlier failed attempts and unsupported diagnoses remain part of the lineage; no acceptance case was quarantined to obtain success.

The final audit covers closed journals, continuation pins, infrastructure and native-source lineage, frozen tests, final acceptance, full source review, final self-tests, persistence, delivery, settled sandbox outcomes, owned backend/native inventory, exact lifecycle ownership, early regression, cumulative budget, historical blocker reconciliation, final amended tests, production-attempt inventory and external-finding reconciliation.
