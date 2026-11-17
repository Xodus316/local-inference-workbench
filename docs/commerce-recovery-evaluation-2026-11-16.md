# Commerce recovery campaign, November 16, 2026

The latest Commerce Catalog campaign reached a generated application that passes
the backend checks, build checks, four smoke tests, and an expanded API-restart
persistence probe. It has **not completed an accepted end-to-end workflow**.
The latest executed attempt stopped because the independent test author omitted
one acceptance criterion and invented another index. A separate replay of the
restored regression suite passed all seven API cases and failed all seven UI
cases. Attempt 011 is prepared and independently audited, but has not started.

This report continues the [earlier Commerce evaluation](commerce-test-author-evaluation-2026-11.md#agentic-commerce-workflow-and-test-repair-november-16).
It records a local development campaign, including changes to the experimental
workflow and runner. This publication adds the report and
[derived evidence data](../benchmarks/reports/commerce-recovery-2026-11-16.json);
it does not deploy those experimental engine and adapter changes to the public
runtime. Dates use America/Los_Angeles; some underlying receipts fall on
November 17 in UTC.

## Scope and controls

This campaign used a new 12-criterion plan and a 76-file application inventory.
The previous report used a 14-criterion plan. Criterion counts, authored test
counts, and workflow outcomes from those plans must not be pooled. Attempts
000–010 below are development iterations on one project, often recovering
settled source from an earlier attempt. They are not eleven independent samples
of model success.

| Role or control | Configuration used in this campaign |
| --- | --- |
| Hardware | One RTX 3090 with 24 GiB VRAM; model roles run sequentially |
| Planner, implementer, smoke-test author | Qwen 3.8 27B AutoRound-fast W4A16 through HyperQwen/vLLM and OpenCode; medium reasoning; 98,304-token context |
| Application diagnosis and repair | The same Qwen package and context, with xhigh reasoning |
| Independent test author and test correction | Gemma 4 31B IQ4_XS through Ollama/OpenCode; thinking enabled; 65,536-token context |
| Independent code reviewer | Gemma 4 26B A4B Q4_K_M, thinking enabled, 65,536-token context; configured but not reached by the latest executed attempt |
| Independent test source limit | 32 KiB decoded UTF-8 source |
| Overall deadline | None for the recovery campaign; bounded individual repair and generation policies still apply |
| Verification | Exact pinned offline Docker image and reference source; CPU-only compilation and execution |
| UI execution | Vitest with a DOM environment, not a real browser or visual-layout evaluation |

The verifier image remained
`sha256:669eabe295932edf880617afcc85fcd2014e462cc89989938f150e28d123d703`.
The approved reference digest was
`aea104efe189e18f96f83fda7db26de6c58a34e6d0d22f0d4d066b91d009082a`.
The campaign froze model identities, source hashes, the brief, and recovery
evidence before each live attempt. Workflow changes were made only after the
affected attempt stopped and its owned backends were cleaned up.

The application's implementation and accepted application patches came from
the recorded native model workers. Coordinator changes in this campaign were
to the workflow, harness, reference/test controls, and recovery machinery.
The earlier assisted backend repair described in the previous report is a
different result; it is not credited as this campaign's model-only application
repair.

## Attempt history

Times are the recorded attempt wall times. They exclude work between attempts,
such as debugging the workflow, CPU regression testing, and separate diagnostic
replays. A prepared preflight is not a completed model run.

| Attempt | Recorded wall time | Observed stop or result | What changed before continuing |
| --- | ---: | --- | --- |
| 000 | 598.3 s | Plan audit found an invented integer-timestamp requirement; the brief specified millisecond precision, while the reference and checks used strings. Implementation was interrupted; its native completion was unknown. | Clarified the public timestamp contract. Interrupted application output was not reused. |
| 001 | 346.3 s | Plan validation rejected prose where a bare file path was required. | Strengthened canonical-path validation and added a bounded correction for a settled malformed plan. |
| 002 | 3,287.7 s | Produced the retained 12-criterion plan and all 76 application files. Backend compilation failed; repair selection entered repeated tool reads and was frozen. | Preserved the completed plan/source, isolated the interrupted selector, and changed selection to a bounded source projection. |
| 003 | 233.7 s | The no-tools selector reached its output limit without an acceptable selection. | Added direct selection from verified compiler diagnostics and improved the bounded fallback. |
| 004 | 924.1 s | Two native compiler patches still left incorrect Java dependency API usage. | Added a pinned public API-signature catalog from the verifier's actual dependencies. |
| 005 | 420.4 s | Two more compiler patches still called an invalid Jackson exception overload. | Extended the API catalog and removed stale implementation advice from repair inputs. |
| 006 | 869.5 s | A native Jackson repair cleared backend and frontend compilation. Smoke authorship exhausted the model context in reasoning; a subsequent request was interrupted. | Added a native reasoning/output budget, a single-request no-tools smoke path, and compact source context. |
| 007 | 450.4 s | The smoke suite called an instance assertion incorrectly from a class fixture, causing an error before any test ran. | Added fixture linting, a preserving correction path, and a guard against treating zero-test fixture errors as application defects. |
| 008 | 446.8 s | The smoke tests made invalid authentication and concurrent-click assumptions. A later reference replay reproduced both test failures. | Required smoke tests to pass the pinned reference before they could drive application repair. |
| 009 | 680.3 s | A fresh smoke suite passed the reference. A native patch fixed the optional-label validation error, but four held-out failures remained. The workflow incorrectly began independent authorship anyway and was cancelled. | Added a bounded self-repair loop that requires every required check to pass before that handoff; separated repair counters and workspace slots. |
| 010 | 1,011.2 s | Reused the exact validated smoke suite. Native repair fixed tracked-URL and report contracts; the candidate passed every required smoke-stage check. Independent authorship then failed coverage validation: missing criterion 2, unknown criterion 13. | Added exact numbered criteria, preserving author correction, a required restored regression gate, and expanded persistence verification. |
| 011 | Not started | CPU preflight and independent launch audit passed. The manifest freezes 162 workflow/runner/verifier files. | Saved for the next GPU session; no acceptance or delivery credit is awarded. |

Unknown native completion remained unknown in the records even after the owning
processes were physically stopped. Recovery used only completed, bound source
and patch receipts. Four unknown worker calls remain excluded in the retained
recovery ancestry; the abandoned attempt 000 is outside that ancestry. This is
not acceptance-test quarantine: no missing or quarantined acceptance case can
be used to declare success.

## What changed in application verification

### A trustworthy smoke suite came before application repair

Attempt 007's fixture error was an invalid test call, not proof of broken
application behavior. Attempt 008's product test expected a protected success
response without authentication, and its concurrency test required every
intermediate click response to contain the final aggregate. Both expectations
failed against the reference. They were retained as diagnostic evidence and
were not reused as approved smoke tests.

The revised author path supplied all 12 criteria and all 76 paths while
avoiding a duplicated full source tree. It enforced an 8,192-token native
reasoning budget and a 32,768-token completion bound, checked terminal usage,
and required one completed worker request. Attempt 009's fresh smoke response
used 7,684 reasoning tokens within 11,273 completion tokens. Its four cases
passed the reference without a correction call.

That exact suite, digest
`9446cb69fa02be9eb2b2064feaab539de268cbdc412065b3426c417a5cb3e6c0`,
was retained into attempt 010 and passed a fresh reference control there.
Reuse preserved the test bytes, not a prior passing result.

### Native repairs cleared the backend gate

The first settled semantic repair in attempt 009 removed the erroneous
nonblank requirement on the optional tracked-URL label. The next verification
could reach later behavior and exposed four remaining failures: tracked-URL
defaulting, tracked-URL updates, and both revenue report response shapes.

Attempt 010's next native repair changed six selected Java files. It added the
requested tracked URL to the request model, defaulted a blank or missing value
to the original URL, preserved the update contract, and returned the required
`totalClicks` and `totalEstimatedRevenue` report fields. The writer completed
17 native requests and the affected backend compiled successfully.

| Attempt 010 stage | Worker wall time | Recorded prompt tokens | Completion tokens |
| --- | ---: | ---: | ---: |
| Repair-path selection | 91.246 s | 18,558 | 7,449 |
| Selected application repair | 179.002 s | 626,282 | 14,256 |
| Independent test authorship | 427.339 s | 19,909 | 11,362 |

The repair's prompt-token total sums repeated inputs across its 17 requests; it
is not a single context size. Worker times include more than native decoding.
These rows are stage measurements from one changing workflow, not a matched
model-speed comparison.

The repaired 76-file candidate contains 147,215 UTF-8 application bytes and has
artifact digest
`48ef3de5deb92597ee3a1fe3a5d21d43281127af5a7336eededc90f544d746d1`.
Its `whole_self_verify_2` receipt passed frontend typecheck/build, Gradle build
and backend packaging, MongoDB readiness, API startup, trusted API and held-out
checks, the original persistence check, frozen-test syntax, and all four smoke
cases. The verifier still reported real-browser execution unavailable.

## Why independent acceptance did not complete

Gemma returned a completed five-file independent draft, but its coverage indices
were `1, 3, 4, …, 13` for a 12-criterion plan. Criterion 2, covering protected
API access, was absent; index 13 did not exist. The engine stopped before
freezing or reference-validating that draft. No independent candidate result,
independent code review, final acceptance smoke check, or delivery followed.

The prepared correction now provides exact one-based criterion numbers and the
complete original draft. Existing files and valid coverage rows must remain
unchanged in this narrow correction. An unknown index may be remapped only to
already-covered behavior; a missing criterion requires a new discoverable test
file. Merely renumbering an unrelated report test as security coverage is
rejected. Syntax, fixture, coverage, and reference validation still follow.

All independent-author requests, including subsequent corrections, now use a
public-contract projection: goal, full criteria, assumptions, and path
inventory, plus the public brief and harness contract. Stale implementation
and test-strategy narrative is omitted and its projection is recorded. Actual
draft correction admission was 24,414 tokens in the 65,536-token window; a
UI reference-correction-shaped request used 14,552. These were CPU tokenizer
checks, not successful live corrections.

Inspection also found fixture weaknesses in the rejected draft. Browser-side
sessions were not established for some protected UI checks, DOM helpers were
called incorrectly, and a create/read-back test claimed restart persistence
without restarting the API. The helpers themselves exist. The new guidance
requires correct invocation, authentication for each isolated mount, observable
values and state changes, and truthful descriptions of what a test proves.
These instructions have not yet demonstrated a successful live correction of
the retained draft.

## Restored regression suite: backend passes, UI fails

The earlier logout case was restored to the reference suite, and rejection
suppression was removed from the diagnostic copy. A reference control passed
all 14 cases and all required image checks. Its three files total 31,521 bytes.
Their hashes, expected case identities, and the complete passing reference
report are bound into the prepared supplemental gate. The original historical
run artifacts remain unchanged.

Replaying those exact tests against candidate 010 produced a settled result:
**7 API cases passed and all 7 UI cases failed**. Other required image checks
passed. No timeout, cancellation, changed source, or changed test bytes explains
the failures.

| UI case | First retained failure or observation |
| --- | --- |
| Registration, logout, login, and guards | Registration remained at `/login` when the case expected `/dashboard`. |
| Products CRUD | The requested `Price` field was not found. |
| Categories CRUD | A rendered `Save` button was not found. |
| Affiliates CRUD | The `Commission Rate %` field was not found. |
| Tracked-URL CRUD and clicks | A rendered `Save` button was not found. |
| Dashboard | Expected tracked-click text was absent. |
| Reports | Expected report heading was absent; rendered rows also showed zero values despite the seeded clicked revenue. |

The captured UI diagnostics also report an unresolved `v-app-layout` component.
Source inspection found a concrete report mismatch: frontend types and rendering
still read `clicks` and `revenue`, while the corrected API returns `totalClicks`
and `totalEstimatedRevenue`. The report regression seeds a product, affiliate,
and click and checks names plus `1` and `$10.00`; the new independent draft's
heading-only report assertion could not establish those values.
The restored report case stopped at its heading assertion before executing its
numeric assertions; the zero values are a retained DOM observation, not a
reported failed numeric assertion.

Several earliest failures are exact label or heading differences. They identify
where execution stopped, not seven independently proven business-logic causes.
Native repair must inspect the public contract, current source, and complete
diagnostics. The restored suite exercises CRUD and numeric report behavior,
but does not establish complete pagination, sorting, responsive-layout, error-
recovery, or exact dashboard-count coverage. Report assertions are page-wide,
not scoped independently to each table row.

The supplemental gate runs before acceptance, after final smoke verification.
A settled failure invokes native application repair against the same immutable
suite. A replacement must return through independent verification, full code
review, final smoke, and the regression gate. Missing cases, skips, inconclusive
execution, or exhausted repair budget cannot produce delivery. This integration
has passed CPU tests; its complete live repair loop remains unproven.

## Expanded restart persistence

The existing trusted restart check proved a stored user could log in and a
product ID/SKU survived. That was narrower than the current persistence
criterion. The authored draft's ordinary create/read-back test could not fill
the gap, and the public authored-test harness exposes no restart API.

A separate CPU-only companion probe therefore uses the same pinned image and
exact candidate source. It owns its MongoDB process and two successive API
processes, proves the first API has exited before starting the next, and checks:

- A fresh login using the persisted account.
- Category, product, affiliate, and tracked-URL records before and after restart.
- Three recorded clicks and `$7.50` estimated revenue.
- Product creation/update and tracked-URL creation/click timestamps at MongoDB
  millisecond precision.
- Both report aggregates, complete process-log hashes, and owned-process cleanup.

The probe was validated on the reference before being allowed to run against
the candidate. Three preliminary probe runs failed closed: an unsupported
requirement for timezone-qualified timestamps; log collection attempted after
temporary container storage disappeared; and Python 3.10 parsing of legal
fractional-second lengths. Those were probe defects, not candidate failures.
The fixes accept the declared ISO representation, compare milliseconds without
host-timezone dependence, and export bounded complete logs before exit.

The fourth probe run passed on both the reference and exact candidate 010. This
is strong evidence for the specified restart behavior of that artifact. It is
not a complete application acceptance result. MongoDB remained running during
the API restart; database restart and power-loss durability were not tested.
The final campaign audit requires
a new companion receipt bound to the eventual final attempt and source digest;
a stale passing artifact cannot satisfy that gate.

## Workflow validation and evidence limits

The local fixes include canonical plan paths, direct compiler-diagnostic
selection, verified dependency signatures, bounded smoke reasoning, test-fixture
linting, reference-first smoke validation, a strict self-test handoff, preserving
coverage correction, and the supplemental regression gate. Compiler, self-test,
and final-check workspace ranges are separate. Recovery records spent repair
counts and reconstructs source from completed patches, including cases where an
older terminal artifact was stale.

The latest recorded validation batches include 117 whole-project CPU tests,
15 retained legacy replay tests, three standalone regression-receipt tests,
six persistence-probe tests, and 23 standalone audit tests. Recovery, regression
binding, legacy, and checkpoint batches reported 118 passing tests across their
named runs. These are different, partly overlapping development invocations;
they must not be added into a new aggregate success rate. They exercise control
flow and evidence validation, not proof that the unfinished UI works.

The public JSON is an allowlisted, derived summary with original receipt hashes.
It does not publish raw model responses, application/reference source, local
machine paths, authentication material, or the complete private execution
journals. Hashes identify retained evidence; they do not make unavailable logs
independently replayable. This report also does not establish a model ranking,
an unattended success rate, real-browser behavior, or visual equivalence.

## Prepared next attempt and remaining work

Attempt 011's manifest is
`4d922f196fb9176a3ff0de466f5f1b454a4b600de298773adf181f733495948b`.
The independent launch audit verified all 162 frozen files, model and image
pins, the full recovery chain, the exact retained smoke suite, the unfrozen
independent draft, and the restored regression-suite addition. Two self repairs
remain recorded as spent. No previous verification pass is credited to the
new attempt.

The run was held before model dispatch when the GPU was needed elsewhere. Owned
model backends stopped and unloaded. Publishing this report did not start a new
GPU run.

The remaining sequence is:

1. Start the prepared attempt when the GPU is available; rerun its compile,
   reference, and candidate gates on the recovered source.
2. Have the independent author correct the coverage draft, then pass syntax,
   fixture, coverage, and reference validation before freezing it.
3. Verify the candidate against that suite and make any required native repairs.
4. Review every source chunk independently and resolve all blocking findings.
5. Pass final smoke and all 14 restored regression cases, repairing the current
   UI defects without dropping cases or suppressing failures.
6. Rerun full persistence on the final same-attempt artifact, audit all required
   receipts, and verify delivered file hashes.

Only that completed chain can support a successful end-to-end claim. A later
public code port of the local workflow/adapter fixes is separate from this
documentation publication and still needs its own scoped review and validation.
