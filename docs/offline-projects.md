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
existing cleanup checks. This profile provides 6,144 MiB memory and 2,048 MiB
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
Path collisions, incorrect criterion indexes, invalid syntax, absent test methods
and undiscoverable references are checked before an addition is accepted. A
coverage addition may address a subset of the missing criteria; remaining gaps
still consume the same bounded budget.

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
