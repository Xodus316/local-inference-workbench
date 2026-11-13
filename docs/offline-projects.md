# Offline project Builds

The optional `offline-project-v1` API profile builds larger applications using
ordered file groups. Ordinary Python Builds retain their existing limits,
mutation gate and execution behavior. This profile is disabled by default and
is intended for an operator-provisioned offline toolchain image.

Enable `build.offline_project_enabled`, configure an exact preinstalled image,
then prepare a request with `mode: "build"` and
`build_profile: "offline-project-v1"`. Preparation freezes the profile's complete
contract and the immutable image ID into the approval digest. It starts no model.
The configured planner, independent test author, implementer and repairer retain
their configured native generation profiles and the existing workflow deadline.

An optional operator-provided [image environment contract](project-environments.md)
freezes required application paths, available packages/tools and URL/server
semantics into approval and every stage's inputs. Its static preflight rejects
clear compatibility conflicts before implementation without executing tests.

| Component | UTF-8 source bytes | Files |
| --- | ---: | ---: |
| Complete implementation | 131,072 | 128 |
| Independent tests | 16,384 | 15 |
| Assembled project | 163,840 | 143 |
| One implementation group | 24,576 | 16 |
| One selected repair | 32,768 | 16 |

The assembled limit is an outer ceiling; separate implementation and test limits
always apply. Paths are normalized, portable and distinct. Source manifests
contain text files only; provision dependency caches, toolchains and other binary
assets in the image. No model writes to the independent `tests/` directory.

The planner first defines the acceptance criteria. A separate model authors
Python `unittest.TestCase` black-box acceptance tests and declares all application
paths. After test validation, the planner partitions those paths into at most
32 ordered groups with explicit interfaces, earlier dependencies and selected
source files. The group plan and suite are frozen before implementation.
Every file explicitly named in `context_files` must belong to a group named
directly in `depends_on`. Rejections
name the consuming group, disallowed paths, their earlier owners and the missing
direct dependencies so a correction can preserve the selected source explicitly.

If a normally completed group-plan response fails JSON, schema or complete plan
validation, the planner may make at most two explicit format corrections. Each
correction receives the same brief, approved plan, declared interfaces, commands
and schema, plus the precise error and complete bounded rejected response. IDs
and paths are never normalized into validity. A plan is frozen only after all
partition, path and dependency checks pass. The contract binds this two-call
budget. Admission failures, missing or uncertain native receipts, non-stop
completions and unrelated exceptions never authorize a correction. Earlier raw
responses remain unchanged.

Each implementation call receives the complete original brief, approved plan and
frozen tests, the cross-file contract and at most 24,576 bytes of selected prior
source. It returns exactly its group's paths. The runtime validates and retains
the complete accumulated implementation after every group; it does not send that
growing source tree to every model call.

The full frozen suite runs after assembly. A conclusive failed execution can
trigger at most the configured three repairs. First the repairer diagnoses the
failure and selects existing paths using their byte sizes. It then receives only
those complete source files, up to 32,768 bytes, alongside the complete brief,
plan and frozen suite. Its response must replace exactly those paths. All other
files and all tests remain unchanged, and the complete suite runs again.
Overlarge selections are rejected before the repair call. No failed or uncertain
native completion is automatically replayed.

Diagnostic feedback contains explicitly labeled UTF-8 tails with original and
included byte counts, truncation flags and the full retained execution receipt's
digest. The original receipt stays in the journal. Source, tests and the brief
are never shortened for this feedback policy.

Before each model call, the profile checks the complete serialized message
contents, schema and framing against that role's frozen context using the
conservative one-byte-per-token policy. It never truncates source, tests or the
brief. The native provider retains its own final admission check. Large or
unusual manifests can still exceed context; storage limits are not a guarantee
that every possible selection fits a model.

The Docker sandbox keeps `/workspace` read-only, networking disabled and the
existing cleanup checks. This profile provides 16,384 MiB memory and 2,048 MiB
temporary space with explicit `noexec` without changing the ordinary Build's
temporary mount options. Its approval-bound limits are 1,024 open files and
256 processes; ordinary Builds retain 256 open files and 128 processes. A
trusted image entry point can stage application sources into temporary storage,
build with preinstalled offline dependencies, start a disposable database on
container loopback and run the frozen tests from the read-only original source.
The image supplies `APP_PROJECT_DIR` for its writable application copy and
`API_BASE_URL` for black-box HTTP tests; frozen tests remain at `/workspace/tests`.
These conventions are included in every model stage's frozen execution contract.
Keep test files outside the writable app copy. Runtime provisioning failures,
timeouts and uncertain cleanup require review rather than model repair.

Acceptance requires explicit successful execution of every referenced unittest
case; zero tests, skipped referenced cases and truncated discovery evidence do
not pass. Python wrappers may exercise HTTP endpoints or invoke offline frontend
checks. A frontend build demonstrates compilation, not UI behavior. Add executable
DOM checks when available and report remaining coverage limits.

Coverage repair returns only new test files and coverage for the requested
criterion indexes. Existing test source, assertions, references and commands stay
unchanged. A normally completed but invalid addition receives its exact validation
diagnostic and rejected response on the next permitted coverage call. Every such
call consumes the existing `max_test_coverage_repairs` budget; exhausted budgets
stop before implementation. Strengthening uses its own existing bound. Dispatch,
admission and unknown native-completion failures never authorize another call.
Path collisions, incorrect criterion indexes, absent test methods and
undiscoverable references are checked before an addition is accepted. A new
Python file with a syntax error may enter the structurally merged draft only
after path, criterion and unittest-reference grammar checks pass. Its exact
parser diagnostics then go to the existing syntax-repair stage and separate
budget; the suite cannot freeze until syntax and references validate. Invalid
addition feedback retains structured parser diagnostics when available. A
coverage addition may address a subset of the missing criteria; remaining gaps
still consume the same bounded coverage budget.

Frontend coverage must reference a discoverable Python `Class.test_method` that
actually invokes the supplied offline DOM tools with a bounded subprocess timeout
and asserts the result. Standalone TypeScript/Vitest files are not discovered by
the frozen unittest command. A Python wrapper can embed its temporary DOM test and
configuration rather than submitting unreferenced support files. These static
checks establish executable references, not correct UI assertions or coverage.

Before freezing tests, this profile runs narrow static checks under
`unittest-http-fixtures-v1`. They flag class credentials left at `None` when one
test writes an instance token and a different instance consumes it as a Bearer
credential, and unconditional JSON decoding in a urllib response helper used by
a DELETE test. Valid empty HTTP 204 bodies must be handled without suppressing
malformed nonempty JSON. Independent setup must initialize its own credentials;
tests cannot share instance state through execution order.

The approval-bound contract permits at most two fixture repair calls to the
independent test author before implementation. Only diagnosed helper or setup
bodies may change; an absent standard `setUp` or `setUpClass` may be added. Every
test method's full syntax tree, all assertion expressions, imports, unrelated
code, test names, coverage and commands remain unchanged. New unittest skip or
expected-failure markers are rejected. Unresolved diagnostics or an uncertain
model completion stop the build before tests are frozen. Ordinary Builds retain
their existing fixture policy and budget. These checks catch specific fixture
mistakes; they do not establish helper correctness or complete test quality.

A trusted host can import validated completed stages into a fresh approval using
the [project checkpoint API](project-checkpoints.md). This never resumes an old
run or retries an uncertain inference; frozen tests and source provenance remain
bound to the new approval, and sandbox acceptance runs again.

The result reports `unittest-acceptance-v1` and
`mutation_verification: "not_run"`. Passing acceptance tests does not establish
complete requirement coverage or semantic correctness. This profile does not
claim Python mutation verification for another implementation stack.

## Offline projects with independent code review

Select `build_profile: "offline-project-reviewed-v1"` to use the same offline
execution limits plus exact test compilation and independent source review.
Configure the `reviewer` role with an explicit context and a model distinct from
the implementer and repairer. The approved bundle binds the profile, role,
generation settings, image, review bounds and blocking severities. The original
`offline-project-v1` profile can still be selected.

For each cross-group dependency, the planner selects the source files defining
the interface and names the exported methods, payload fields and routes the
consumer will use. When a group names dependencies and selects no files, the
reviewed profile supplies complete files from direct dependencies first, followed
by their upstream dependencies. Each addition must fit the existing source and
prompt limits. The implementation stage records the supplied paths.

An image environment can declare `compile_checkpoints`. Each checkpoint names
a source directory and the compiler checks expected from the pinned image.
The runner schedules it immediately after the last group that owns files in
that directory. It runs `workbench-compile-checkpoint <name>` with only that
component's source mounted read-only. The image must provide a matching
compiler receipt. Final acceptance still runs on the assembled project.

A conclusive compiler failure sends the diagnostics to the configured repairer.
It may replace only existing files in that component, including files from
earlier groups. The runner recompiles after each repair and allows up to two
repairs per checkpoint. Later implementation groups receive the corrected
source. An unresolved compiler failure or inconclusive receipt stops the run
before another implementation group. Imported unchanged prefixes repeat these
checks. A prefix changed by compiler repair can reuse the frozen plan and
regenerate the implementation groups.

Before freezing the generated tests, the executor mounts the test source
read-only in the pinned image and compiles it using that image's Python
interpreter. This does not import or run generated Python. Syntax diagnostics
go to the independent test author within the existing repair budget. An optional
`python_version` in the image environment declares the expected major and minor
version. Static fixture checks also identify literal JavaScript object braces
that would become Python expressions in a fixture f-string. A brace repair can
escape only those diagnosed braces and must preserve test logic and assertions.

After verification passes, the reviewer receives the brief, approved plan and
contract, recorded image check statuses, and complete implementation files in
bounded chunks. An actionable verification failure goes directly to repair.
A settled but inconclusive run can still produce an advisory source review.
The reviewer returns severity, category, path, source line, excerpt, impact and
fix for each finding. The executor validates every excerpt and binds the review
to the exact candidate and project digests. It records every chunk's paths,
the structured findings, and deterministic warnings for compressed source.
Reviews may be limited in their ability to compare files in different chunks.
Review findings cite source and do not stand in for execution evidence.

The reviewer may copy the display prefix into a quote or cite the wrong line.
The executor can remove the prefix and resolve a verbatim quote to a unique
line in the cited file. If a citation is still invalid after the tests pass,
the reviewer gets one correction attempt with the same source chunk, within
its context limit. A valid retry retains the initial review's grounded findings.
The review records any unresolved rejection and continues with the remaining
chunks. Confirmed verification failures can still go to the repairer. Delivery
requires a passing verification and a complete review of the same candidate.

P0 and P1 findings use the existing selected-file repair budget. The next
candidate must pass verification and a fresh review. P2 and P3 findings are
reported for follow-up. Unknown execution outcomes, invalid review evidence,
or a context budget that cannot cover the full source cannot produce an
approved delivery. An implementation repair still cannot alter frozen tests.
A resource failure with confirmed cleanup can still produce source findings,
but never authorizes an implementation repair or delivery by itself.

When a verified image check fails, repair targets one currently failing check
or a case recognized by the local verifier adapter. The last observed failure
and its receipt digest remain available when a later check is skipped. When a
case's diagnostics change, the repairer also sees the prior observation and its
receipt digest. A verifier adapter can identify a bounded set of implementation
files to include during file selection and provide case-specific diagnostic
guidance. For a recognized failure with matching source and a proven repair
scope, the adapter can select the editable files directly. The runner records
that selection against the verified failure and exact source, and supplies any
related source as read-only context. New approvals use exact text edits for
these repairs and their compiler corrections. The runner matches each edit
against the original source and applies disjoint edits in memory before
compiling. A missing, ambiguous, overlapping, or out-of-scope edit is rejected.
One invalid edit response can be corrected within the same scope. Other cases
use ordinary file selection and replacement.
Skipped checks stay pending until the verifier confirms a pass.
Unrelated review findings stay out of that repair request. If the image declares compile
checkpoints, the workflow compiles the changed components before running full
verification again. A compiler correction keeps the original repair intent,
uses only the preceding patch's selected files, and has its own bounded retries.

Images may emit a final `OFFLINE_PROJECT_RESULT` JSON record with independent
stage results, per-stage failure excerpts, and unavailable checks. The reviewed
profile retains that record alongside the command output and sends relevant
diagnostics to repair. An operator can declare `verification.policy` and
`verification.required_checks` in the approved image environment. For these
projects, a passing unittest log cannot substitute for a matching image
receipt that marks every required check as passed. A frontend compile or DOM fixture gives no evidence
about pixel layout. Real browser checks require a trusted image with a browser
and executable interaction tests, or separately recorded browser verification.

## Whole-project OpenCode trial

A trusted host can bind `opencode-whole-project-v1` to a reviewed project
approval. The normal Workbench API offers the grouped profiles described above.
The whole-project path uses an approved flat inventory and one implementation
session. It then authors and runs implementer smoke tests, gives the implementer
one repair opportunity, and runs a separate suite authored by an independent
model. Application repair receives sandbox failures and keeps the independent
tests frozen. Delivery requires independent verification, source review and a
final run of the implementer suite against the same candidate.

The host supplies the three role-specific OpenCode workers. The tested
integration uses OpenCode 1.19.0 on Windows, Git and a local Ollama backend.
Approval binds the worker and model identities, context, thinking setting,
auto-compaction and source budget. The host can receive worker events for live
status, reasoning and tool activity. The worker verifies completion and cleanup
before handing its result back. A lost completion receipt stops further
inference until the backend is reconciled.

Each whole-project worker uses its approved model context as its output
ceiling. The configured context includes the prompt and generated tokens.
Auto-compaction can summarize earlier turns and allow subsequent turns to
continue. The default source budget is 7,680,000 UTF-8 bytes across up to 128
files of at most 60,000 bytes each. The host's run deadline and the approved
image, verification and test-suite constraints still apply.
