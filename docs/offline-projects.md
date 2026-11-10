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
temporary mount options. A
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

The result reports `unittest-acceptance-v1` and
`mutation_verification: "not_run"`. Passing acceptance tests does not establish
complete requirement coverage or semantic correctness. This profile does not
claim Python mutation verification for another implementation stack.
