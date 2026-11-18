# Commerce workflow tests, November 17, 2026

The Commerce workflow made substantial progress but **did not complete an
accepted end-to-end run**. Qwen brought the current independent suite to 13/13
on the reference, repaired the application until it passed those 13 tests, and
completed a full source review with no P0/P1 findings remaining. The final four
smoke tests passed. The broader, original 14-case regression suite then passed
8 cases and failed 6 UI cases, stopping acceptance and delivery.

GPU testing is paused. Qwen was unloaded at **22:39:41 PST**, before the user's
GPU deadline. Subsequent CPU-only work qualified a label-compatibility fix and
replayed the original regression suite on the unchanged candidate. That replay
still passed 8/14: one label failure advanced to a missing Save action, while
the other five first-failure records were unchanged.

This report covers the November 17 local-day campaign: morning Gemma recovery
attempts, North Mini Code versus Qwen comparisons, test-author continuations,
reference and candidate executions, synthetic negative controls, four later
workflow attempts, and the final CPU diagnostics. It continues the
[November 16 report](commerce-recovery-evaluation-2026-11-16.md), where attempt
011 was prepared but unstarted. The
[derived results](../benchmarks/reports/commerce-workflow-2026-11-17.json)
retain per-run outcomes and selected original receipt hashes.

## Scope and interpretation

Dates and times use America/Los_Angeles, UTC−8. The reporting window is
November 17 00:00 through November 18 00:00 PST; late receipts therefore have
November 18 UTC timestamps. Some morning directories retain `20261116` in
their names even though their executions occurred on November 17.

These are iterative repairs of one recovered 76-file application under a
12-criterion plan, not independent attempts at a fresh problem. Three suites
must remain distinct:

- **Smoke4:** four frozen smoke cases, checked against the reference before
  candidate use.
- **Independent13:** thirteen independently authored cases, corrected through
  several explicitly versioned test-author runs before the final suite froze.
- **Original14:** the earlier restored regression suite, with seven API and
  seven UI cases. Its bytes and historical failures remain unchanged.

Reference passes establish that tests accept the trusted implementation.
Candidate passes establish behavior of a specific generated artifact.
Synthetic negative controls measure selected oracle defects. CPU tests with
mocked native responses exercise workflow handling. These are different forms
of evidence; repeated executions and overlapping development batches are not
summed into a single success rate.

Application patches and accepted test-method patches came from the recorded
native model workers. Coordinator changes addressed the generic workflow,
transport schemas, verification helpers, probes, and evidence checks. This
publication adds documentation and derived evidence only; it does not deploy
the experimental runtime changes into the public Workbench.

## Models and execution environment

| Phase | Actual model and configuration |
| --- | --- |
| Morning test correction, attempts 011–015 | Gemma 4 31B IQ4_XS through Ollama/OpenCode; thinking enabled; 65,536-token context. Actual native calls were test-author calls. Other configured roles were not evidence of execution. |
| North comparison arms | North Mini Code 1.0, official Q4_K_M weights through Ollama 0.40.12; thinking enabled; 98,304-token context. The derived local tag changed sampling parameters, not weights. |
| Qwen comparison arms | Qwen 3.8 27B AutoRound-fast W4A16 through HyperQwen/vLLM and OpenCode; 98,304-token context. The first two arms used medium reasoning; later arms and continuations used xhigh. |
| Later test revision, application repair and review | The same Qwen W4A16/HyperQwen package at xhigh. Separate roles used fresh sessions, but shared the same model weights. This is not independent model-family review. |
| Verification | Pinned offline Docker images; frontend/backend compilation; Vitest/jsdom with real HTTP services. No real browser or visual-layout evaluation was available. |
| Hardware | One RTX 3090 with 24 GiB VRAM; model roles executed sequentially. |

The paired comparisons used temperature 1, top-p 0.95, top-k 20 and neutral
penalties. Each arm reset to the same attempt-015 suite and four-record
history, with at most two correction rounds. Diagnosis and patch total-output
caps were 8,192 and 32,768 tokens. Qwen medium reasoning caps were 2,048 and
8,192; xhigh raised them to 4,096 and 16,384 respectively. Later Qwen
review/repair roles generally used a
16,384-token reasoning cap within a 32,768-token completion cap, with separate
native-tokenizer admission checks.

North and Qwen used different tokenizers, backends and reasoning controls.
Changing schemas and patch protocols between rounds also changes the task.
These results support a local continuation choice, not a clean causal model
benchmark or speed ranking. North receipts bind the configured transport
schema; Qwen receipts also capture the actual wire schema. The Qwen used
here is the HyperQwen W4A16 package,
not an Ollama 37B model or a Qwen3-Next IQ3 result.

## Morning recovery attempts

The time ranges below are recorded attempt boundaries. Reference progress
shows repeated executions of the evolving independent suite; every listed
reference count is out of 13.

| Attempt | PST | Native test-author activity | Reference result and stop |
| --- | --- | --- | --- |
| 011 | 07:58–08:10 | One Gemma correction | Rejected for metadata drift in a preserving correction; no accepted independent suite. |
| 012 | 08:23–08:47 | Five Gemma calls | 6 → 8 → 8 passed. Correction improved the draft but did not make it reference-valid. |
| 013 | 09:02–09:41 | Two normally completed calls and one settled cancellation | 8 → 8 passed. No-op corrections and the cancelled request did not advance acceptance. |
| 014 | 09:55–10:04 | Four Gemma method-patch calls | 8 → 10 → 10 passed. Narrower method correction made progress. |
| 015 | 10:24–10:35 | Three diagnoses and three Gemma patch calls | 10 → 11 → 11 passed. Products and Reports UI remained failing on the reference. |
| 016 | 10:54–11:02 | Qwen author migration configured; zero native calls | Reference remained 11/13. Model startup failed for insufficient free GPU memory; this is not a Qwen quality result. |

All six attempts separately passed the four reference smoke cases and four
candidate smoke cases. Those successes did not make the independent draft
valid, and no morning attempt reached final acceptance. Attempts 000–010 and
the previously successful API-restart persistence probe belong to November 16
and are not counted again here.

## North and Qwen on the two failing UI tests

These runs corrected tests against the reference; they were not candidate
application repairs. Time ranges cover first through last native call and
exclude model loading and surrounding verification. “Composed patches” means
patch bytes passed composition checks, not that the proposed repair solved
the target behavior.

| Arm | Native-call window, PST | Calls | Composed patches | Full reference progression |
| --- | --- | ---: | ---: | --- |
| North, original protocol | 11:35–11:37 | 2 | 0 | 11 → 11 → 11 / 13 |
| Qwen medium, original protocol | 11:41–11:45 | 2 | 0 | 11 → 11 → 11 / 13 |
| North, clarified protocol | 12:01–12:03 | 2 | 0 | 11 → 11 → 11 / 13 |
| Qwen medium, clarified protocol | 12:08–12:14 | 4 | 1 | 11 → 11 → 11 / 13 |
| Qwen xhigh | 12:26–12:33 | 4 | 0 | 11 → 11 → 11 / 13 |
| Qwen xhigh, line edits | 13:01–13:08 | 3 | 1 | 11 → 12 → 12 / 13 |
| North, structured citations | 14:21–14:24 | 4 | 0 | 11 → 11 → 10 / 13 |
| Qwen xhigh, structured citations | 14:30–14:39 | 4 | 2 | 11 → 12 → 12 / 13 |

An earlier North launcher failed readiness/ownership checks at about 11:31,
before any native request or reference execution. The process exited, but
that launch's retained unload status was unconfirmed. It is excluded from
model-quality comparisons; later settled comparison arms have their own
cleanup evidence.

Early arms stalled at citation validation or produced invalid/no-op patch
proposals. Clarified Qwen medium composed an invalid embedded-JavaScript
patch; its next proposal returned both methods byte-identical and was
rejected. Clarifying the protocol alone did not solve both tests. Qwen's
first xhigh arm proposed bodyless Python and was rejected. The line-edit arm
repaired Reports, then stopped when the next diagnosis omitted required prior
citation evidence.

Structured citation fields removed North's citation barrier: it reached patch
generation and produced two proposals. Both were rejected for invalid newline
block encoding, so **North made no accepted test change**. Its final 10/13
result exposed a pre-existing Python 3.10 timestamp-parser defect in unchanged
test code; it was not a regression introduced by North.

Qwen's structured-citation arm composed two patches and fixed Reports
navigation, reaching 12/13. Products still assumed a native form that the
reference UI did not provide. Qwen was selected to continue because it had
produced a useful, executable correction in this comparison. This does not
establish general superiority over North or justify declaring Gemma unusable.

## Reaching a valid independent suite and testing the candidate

| Execution | What ran | Result |
| --- | --- | --- |
| First persistence continuation | Attempted project staging | A long Windows path failed before native calls or reference execution. The failed attempt was retained. |
| CPU materialization preflight | Exact 82-file materialization at a shorter absolute path | All file bytes matched; longest path 234 characters. No Docker, reference, model or GPU execution. |
| Qwen continuation 02, 14:57–15:00 native calls | Two native calls, one method-local timestamp parsing patch; shared helper unchanged | All 13 deterministic timestamp expected outcomes matched; full reference remained 12/13 with Products failing. |
| Qwen continuation 03, 15:09–15:12 native calls | Two native calls, one Products patch | Replaced the disproven native-form assumption with field readiness and a real Save click while preserving the created-product assertion. Reference improved 12 → 13/13; timestamp controls remained satisfied. |
| Candidate 01, 15:17–15:21 | CPU-only compile, reference controls and candidate verification; zero native calls | Both compilers passed; reference and candidate smoke each passed 4/4; fresh independent reference passed 13/13; candidate passed 11/13. |

The cumulative three-method test change passed a separate semantic review.
The handoff suite digest was
`0e542b61b67dc1b7966a18581122363ddb377755315cb23122891022728c031c`.
Candidate 01 reached the requested durable `build_verify_0` milestone, then
stopped with Products and Reports UI failures. It was an intended intermediate
handoff, not an end-to-end pass.

Products failed at `Price` versus `Price (USD)` label lookup. Reports failed at
the expected heading; source inspection also exposed missing/incorrect report
fields. The next work separated genuine application requirements from brittle
test assumptions before allowing either kind of change.

## Qualifying the helpers and strengthening the Reports oracle

The first generic label fix accepted a known unit in parentheses while
preserving exact-match priority and ambiguity rejection. It did not alias
Create to Save. The new verifier image was derived from the old pinned image
with a helper-only layer.

Image qualification 01 ran the unchanged independent reference suite and
passed 13/13, then paused before the original regression suite. Its receipt
explicitly marked qualification incomplete. Qualification 02 verified that
prior receipt and ran the unchanged original reference suite, passing 14/14.
It did **not** rerun the first 13 cases. Together they qualified the migration
from image `669eabe…` to `da55b81…`; no candidate pass was inferred from it.

The Reports test also needed stronger assertions. A heading-only or
page-wide numeric check could accept wrong row values, unrelated text or
incorrect currency. Four native Qwen revisions were evaluated separately:

| Revision | Native calls | Composed / ultimately accepted patches | Fresh reference executions | Outcome |
| --- | ---: | ---: | ---: | --- |
| 02 | 4 | 0 / 0 | 2 × 13/13 | No acceptable patch; an output-limit completion was retained as rejected. |
| 03 | 2 | 1 / 0 | 2 × 13/13 | Improved API and row fixtures, but numeric substring assertions accepted invalid values. A 29-case synthetic UI probe exposed eight false passes. |
| 04 | 4 | 2 / 0 | 3 × 13/13 | Passed the 29-case probe, but omitted required exact text and admitted non-USD currency. Six added negatives exposed those gaps in a 35-case probe. |
| 05 | 2 | 1 / 1 | 2 × 13/13 | Header-bound row assertions, required text and complete two-decimal USD/`$` values. All 35 UI-probe outcomes and 13 timestamp-probe outcomes matched expectations. |

The separate synthetic baseline evaluations matched 3/15 expected outcomes
with the initial helper, 4/19 after the first label migration, 21/29 with the
expanded numeric controls, and 29/35 with the added field/currency controls.
Revision 02 retained 4/19; revision 03 reached 19/19 on the smaller fixture
set; revision 04 progressed 27/29 → 29/29 before the six additional negatives
were introduced. Timestamp controls matched 13/13 in each revision, twice in
revision 04. These are literal test-method executions against synthetic
HTTP/DOM fixtures. A negative scenario succeeds when the test rejects the
defective data; these are not dozens of additional application acceptance
cases or full restart-persistence tests.

Positive reference passes alone did not qualify revisions 03 or 04. Revision
05 became the frozen independent suite:
`b95e87ff4d98b05d81cd40aa97e4160cf5e13d8bfec7d3676565da9b95d9c504`.
These synthetic controls cover selected oracle weaknesses; they do not prove
that every incorrect application will be rejected.

## Four later workflow attempts

| Run | Actual execution | Result and next fix |
| --- | --- | --- |
| E2E 01 | Docker provisioning only; no runtime store, native call or application verification | A 60-second create timeout was shorter than Docker Resource Saver's roughly 95-second wake-up. The lifecycle fix used one bounded 180-second provisioning window and reconciled the exact owned container. |
| E2E 02 | Fresh reference controls; candidate independent suite 11 → 12 → 13/13; two native application repairs; full-review attempt | Application checks passed after repair. Four large review chunks and corrections still failed citation/schema validation, leaving review incomplete. Eleven journal calls contained 33 native requests. |
| E2E 03 | Fresh reference controls, compilers and candidate independent suite 13/13; no application patch | Eight review chunks plus two corrections. Six chunks were accepted; two still had invalid copied evidence. Ten journal calls contained ten native requests. |
| E2E 04 | Fresh controls and candidate verification; complete review, native repair, complete second review, final smoke and original regression | Current suite 13/13, final smoke 4/4, final review zero P0/P1, original regression 8/14. Stopped `review_required`; persistence, final acceptance and delivery were not reached. Thirty journal calls contained 50 native requests. |

Each of E2E 02–04 freshly passed reference smoke4 and independent reference13.
Their closed records contained no unknown or orphan native requests. Journal
calls and native requests are different counts: one high-level writer call
can contain multiple native requests. No total is presented as a count of
independent model trials.

### Application repair in E2E 02

The first native repair changed four files: it supplied the required Products
Save action, removed an unresolved `v-app-layout`, and corrected Reports
headings/columns. Frontend compilation passed; candidate verification improved from
11 to 12/13, with Reports still failing because the expected affiliate row was
absent. The second native repair changed three files to use actual report and
dashboard response fields and matching shared TypeScript types. Frontend
compilation and all 13 candidate tests passed. Initial backend/frontend
compilation had passed; each frontend-only repair reran the frontend check
and retained the unchanged backend's source-bound result.

The resulting 76-file source digest was
`254d826a01525f39710dd5dddb060b936ec5b74084fd25250017dbbb2bb666eb`.
Its retained counters included two general repairs and two earlier self
repairs; resuming did not reset those costs.

### Repairing review citations without weakening source validation

E2E 02 review failures included descriptions exceeding the 900-character
limit and malformed `path|line|code` evidence. E2E 03 used smaller chunks and
bounded correction. Six initial chunks were valid, but both correction
responses still contained invalid evidence, including curly-quote
substitutions and invented ellipses in copied excerpts. These
were invalid review outputs, not new application test failures.

The next adapter presented an enumeration of exact source anchors. The native
reviewer selected an anchor and still authored severity and explanatory
prose; deterministic expansion supplied the exact coordinates and excerpt.
Full numbered source remained available. Separate raw native, anchor-registry
and canonical-response evidence preserved provenance, and the original
source-validation checks still applied. Correction remained bounded and was
permitted only for known-completed requests.

Mocked-native tests also exposed a real Store artifact-whitelist rejection.
The fix added a finite set of companion evidence stages without granting
additional native calls. E2E 04 then completed both 14-chunk reviews covering
all 76 files: **28 first-response-valid review chunks, with no citation
correction requests**. This demonstrates the new path on this run; it does
not prove review quality or general reliability on other projects.

### The last native repair and final review

E2E 04's first complete review found 3 P1, 13 P2 and 11 P3 findings. Native
repair 3 selected four files and made these changes:

- Map data-integrity conflicts, including duplicate-key conflicts, to HTTP
  409 in the global exception handler.
- Update only editable tracked-URL fields with targeted database updates,
  preserving concurrent click counts, revenue, last-click time, associations
  and creation metadata.
- Carry the optional tracked URL through the frontend API payload and
  create/edit/save UI, including the specified blank-value default.

The repair used one native selection request and 21 native writer requests.
Both compilers passed, and candidate independent13 passed again. The accepted
76-file checkpoint digest is
`313c5a2fabf9967a9c2174ac4a6caaca17a20b601e5ab35ebbe9cdd60052d2c0`.

The second complete review returned **0 P0, 0 P1, 16 P2 and 8 P3**. Independent
source audits tracked the previously identified blocking issues to their
actual fixes. One P2 duplicate-SKU complaint persisted because its chunk
lacked the fixed global-handler context; it was separately adjudicated as a
cross-chunk false positive, without rewriting the original finding. Zero
blocking findings did not mean a clean review or accepted delivery.

All 411 frozen workflow/source pins for this attempt remained unchanged. The
cumulative general repair count reached three, exhausting that run's finite
general repair allowance; two prior self repairs remained recorded. There
were no compile-repair calls in this continuation.

## Why the original regression still stopped delivery

All seven API cases and the Reports UI case passed. The remaining six UI
cases stopped at the following boundaries:

| Case | First E2E 04 failure | Diagnosis against the brief and source |
| --- | --- | --- |
| Authentication | Registration never left the login page | The application required an extra confirmation input beyond the specified username/password flow, returning before the registration API call. |
| Dashboard | Required tracked-click aggregate was absent | Application computation/rendering was missing. |
| Categories CRUD | No Save button in create mode | Application used Create category where the required action was Save. |
| Tracked URLs CRUD | No Save button in create mode | Application used Create tracked URL where the required action was Save. |
| Affiliates CRUD | `Commission Rate %` did not match `Commission rate (%)` | Label-helper portability problem. Source inspection also identified the later Create affiliate versus Save mismatch. |
| Products CRUD | Private `.mdi-pencil` selector found no element | Test portability problem: a visible Edit action existed with `.mdi-pencil-outline`. |

The first failure in a case does not establish that its later actions pass.
Unreached CRUD delete branches also assume a second rendered Delete button,
while the application uses conditional native `window.confirm`. The brief
requires confirmation without prescribing a modal representation. A qualified
test amendment must check cancellation, confirmation and actual deletion
without assuming private icon classes or one confirmation transport.

The original14 result remains an immutable **8/14**. Any future semantic
selector amendment must be a separate native-authored, qualified suite
version retaining the same 14 case identities and behavioral/API assertions.
It cannot retroactively turn this run into a pass.

## CPU work after GPU release

The owned model container exited normally at 22:39:41 PST. Observed VRAM use
fell from 23,643 MiB to 1,229 MiB out of 24,576 MiB. The parent workflow later
confirmed drained transport and closed ownership. CPU verification continued;
no new model or GPU test was started.

A second generic label helper accepts the same explicit known unit with or
without parentheses, such as `Rate %` and `Rate (%)`. Exact matches retain
priority, ambiguous controls are rejected, and conflicting, unknown or
doubled unit suffixes are not treated as interchangeable by the fallback.
Literal exact-label matches remain allowed. Save/click/submit behavior is unchanged.
The helper passed **58 mocked DOM/Node CPU checks**.

A derived verifier image added only the new helper layer to the prior image.
The first image-hash probe succeeded, but its cleanup observer rejected
Docker's lowercase “no such object” response because the observer expected a
different case. That preparation error was preserved, exact container absence
was checked, and a repeated probe succeeded. It was not an application or
model failure.

The new image is
`sha256:0d35bb15643677c37ad9b5047ebe502ef375559a7779479f9493e2743edfffd9`.
Image qualification 03 freshly passed both unchanged reference suites:
**13/13 and 14/14**. This was real CPU/jsdom/service execution, not mocked
model acceptance. It did not change E2E 04's frozen image or receipts.

A separate candidate diagnostic ran the unchanged final candidate and
original14 on the new image. The result remained **8 passed, 6 failed**.
Affiliates successfully found the commission field and advanced to the
predicted missing Save action. The other five complete first-failure records
were byte-identical. Its process exited successfully because the diagnostic
completed; its regression suite did not pass. No application/test source
was edited and no end-to-end acceptance credit was awarded.

## Workflow validation and evidence limits

The development checks covered bounded correction, method/line patch
composition, citation schemas, context admission, materialization, suite and
checkpoint binding, lifecycle ownership, review provenance and safe GPU
release. Selected recorded CPU batches are retained in the derived JSON;
they overlap and are not a new aggregate benchmark.

| Saved preparation batch | Recorded passing checks | Scope |
| --- | ---: | --- |
| Comparison preparation auditors, versions 1 and 2 | 7; 10 | Separate versioned CPU guard batches |
| Qwen xhigh adapter | 11 | Mocked native request budgets, identity and cleanup |
| Independent line renderer; structured citation checks | 15; 15 | Exact anchors, patch boundaries and citation validation |
| Candidate transition adapter | 16 | Runtime/Store with synthetic execution boundaries |
| Original label helper | 24 | Synthetic Vitest/jsdom fixtures |
| Reports-probe guards, versions 2, 3 and 4 | 12; 18; 24 | Separate, overlapping mocked Python batches |

The line-decoder investigation retained two failed schema experiments: one
rejected valid literal backslash-plus-`n` characters; another admitted actual
CR/LF characters. The final CPU probes passed 11 decoder fixtures and 9
runtime lexical fixtures. Structured citations passed 12 decoder and 12
runtime fixtures. North's native decoder was not tested by these CPU probes.
Comparison context-admission maxima were at most 66,293 tokens in the 98,304
window; the later Products continuation measured 70,041. These were real
tokenizer checks, not inference results.

A 12-case timestamp reproduction and two overlapping 13-case literal-method
replays exposed four rejected valid fractional-second formats while
preserving five negative controls. Later native method patches passed all 13
expected outcomes. A retained failed-looking synthetic Runtime fixture is
accounted for only within the candidate adapter tests, not as another live
candidate failure.

Preparation audits additionally record agent-reported workflow batches of 31
line-edit and 35 citation tests; standalone raw unittest output was not found
for those totals in the publication audit. Recorded console development
batches for the later review/lifecycle work included 15 lifecycle and 14 lifecycle-auditor
checks; 15 pure source-anchor checks; 8 mocked-native ask/flow checks; 14 real
Runtime/Store tests with mocked native responses; 10 review-audit provenance
and 4 checkpoint checks. Separate saved late preparation receipts record 6
mocked GPU-release checks, the 58 label checks, 10 image-qualifier checks and
7 candidate-diagnostic checks. Mocked request
tests verify handling and evidence, not a successful model generation.

The final closure audit bound 238 retained proof files. Its incomplete-run
status must not be mistaken for another native citation failure: the terminal
failure artifact contains the implementation files, whereas review evidence
binds the source plus frozen tests. Separate same-source review evidence is
valid; the overall workflow still failed at original14 and never reached
final acceptance.

Public data is an allowlisted summary. Raw prompts, model responses,
authentication material, local machine paths, ownership identifiers, private
application/reference source and complete journals are not included.
Receipt hashes identify retained evidence but do not make unavailable raw
executions independently replayable. This campaign does not measure
unattended success rates, independent model-family review quality,
real-browser behavior, responsive layout or visual equivalence.

## Remaining work

1. Keep GPU testing paused until the user releases it again.
2. Have Qwen author a separately versioned amendment replacing private icon
   and confirmation assumptions with unique, row-scoped semantic actions.
   Preserve every case and assertion, and qualify positive and negative
   controls before candidate use.
3. Freeze a new continuation using the qualified helper image and regression
   binding. Declare an additional finite repair allowance while preserving
   the already-spent three general and two self repairs and all native costs.
4. Use native Qwen repairs for registration, the dashboard click total, and
   the Categories/Affiliates/Tracked-URLs Save actions.
5. Freshly pass both compilers, independent13, complete source review, smoke4,
   the qualified regression suite, full restart-persistence verification,
   final source-bound acceptance and delivery checks.

Two general workflow improvements remain: persist open review findings across
resumes with explicit source-bound dispositions, and supply dependent-file
context to reduce cross-chunk false positives. An earlier supplemental
regression gate is also proposed so known failures are addressed before an
expensive full review. That sequencing change is **not implemented**; it must
retain checkpoints, cumulative budgets, uncertainty stops and all final
review/smoke/regression/persistence gates.

The November 16 API-restart success cannot substitute for a fresh persistence
receipt on the final repaired source. A successful end-to-end run still
requires the entire remaining chain. Publishing this report did not resume
testing or deploy the experimental agents.
