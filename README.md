# Local Inference Workbench

Run a quick summary, compare two independent answers, or prepare a code change using models on your own machine. The workbench pairs a Python/LangGraph workflow engine with Ollama or an explicitly pinned local Strata backend, a PC control panel and a streaming chat interface. SQLite stores approved inputs, model calls and results so you can inspect how an answer was produced.

## Start on a PC

Install Python 3.11 or newer, Git, and Ollama. Start Ollama and use `ollama list` to find an installed model. The service and Ollama run on the same machine.

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install .
Copy-Item config.example.json config.local.json
.\.venv\Scripts\python -m orchestrator --config config.local.json
```

On macOS or Linux, use `python3 -m venv .venv`, `.venv/bin/python -m pip install .`, `cp config.example.json config.local.json` and `.venv/bin/python -m orchestrator --config config.local.json`.

Set `roles.fast_primary` in your configuration to a completion model installed in Ollama, then restart the service after configuration changes. Open [the workbench](http://127.0.0.1:8787/) and prepare a Fast run. Inspect the request and approve it to generate an answer. Open [Chat](http://127.0.0.1:8787/chat/) for an interactive conversation. Default role names in the example describe the research machine's model selection. Configure the roles used by the workflows you want to run.

Ollama is the default backend. The optional [Strata backend](docs/strata.md) and [llama.cpp backend](docs/llama-cpp.md) require a pre-launched loopback server and immutable runtime, model, template, path and configuration fingerprints. Prepared approvals are invalidated if the observed backend identity changes. llama.cpp supports a native non-thinking profile with explicit host launch verification and slot cleanup checks.

## Latest end-to-end result

The fresh browser Snake project completed on November 25 after audited checkpoint continuations. It passed 22 independent tests, five smoke tests, 15 trusted Chromium checks, mobile verification, code review with no findings, final acceptance and delivery. The last trial ran without intervention and completed one OpenCode compaction. See the [daily summary](docs/development-summary-2026-11-25.md#later-work-a-fresh-browser-snake-project) and [audited Snake report](benchmarks/reports/snake-recovery-2026-11-25.json).

## Workflows

See [model choices by role](#model-choices-by-role) for the lineup and benchmark scores.

| Mode | What happens |
|---|---|
| Auto / Fast | One reader summarizes or reasons over the prompt and supplied sources. Auto currently selects Fast. |
| Balanced | Two readers inspect the same sources independently. The first model synthesizes their findings. |
| Internet Research | Search the selected provider, synthesize retrieved excerpts locally and return an answer with source citations. |
| Balanced with research | If the answers leave an unresolved question and research is enabled, a model proposes queries. The search adapter returns bounded excerpts for synthesis. |
| Verified | Two readers produce candidates. A local decision model can select one, request synthesis or escalate to Deep. A low-confidence decision is returned for review. |
| Deep | A single configured model takes the difficult reasoning request directly. |
| Build | Create a standalone Python project. The planner defines acceptance criteria, an independent model writes tests, the implementer builds the program, and a repair model can fix failures against frozen tests. Docker runs the checks. |
| Plan | Supply an allowlisted Git repository and selected files. An investigator examines the scope and a separate planner produces a structured proposal. |
| Edit | Freeze selected files, generate replacements, review the proposed diff and approve application to a detached worktree. |
| Chat | Choose an installed model, stream a response, attach documents or images, edit a conversation or stop a generation. |

### Desktop control panel

To build a new program, choose **Open Build** in the desktop panel or **Build a Python project** in the workflow selector. The [Build walkthrough](docs/using-the-workbench.md#build-a-python-project) covers Docker setup, a sample request, verification receipts and downloading generated files.

The API also offers an opt-in [offline project profile](docs/offline-projects.md)
for larger applications in a preprovisioned toolchain image. It implements bounded
file groups and selected-file repairs while retaining independent frozen tests,
native call ownership and approval bindings. Operator enablement is required.

On Windows, launch `.\.venv\Scripts\python -m orchestrator.desktop --config config.local.json` from the checkout. The panel can start and stop its service, open the workflow and chat interfaces, edit local configuration and show recent runs, resident models and NVIDIA GPU measurements. The desktop interface uses Tk. Starting the service from a terminal is also supported.

### Optional capabilities

Verified requires a compatible local System One endpoint and decision model. The adapter sends typed choices to `/v1/systemone`; the example decider name is a local alias. Confirm your runtime supports this protocol before enabling Verified. Ordinary generation uses Ollama's native streaming chat endpoint.

For internet access, set `research.enabled` and choose `research.provider`. The example selects Exa’s hosted search, which works without an account key. Ollama Web Search and Brave Search are also supported. Select **Internet Research** for a required lookup or enable optional search on a Balanced run. [Windows setup and provider comparison](docs/internet-research.md)

To ground a reasoning run in local Markdown, start the optional Solr container, index the docs, and select **Search local docs**. Retrieved passages appear in the approval preview and can be cited in the answer. [Local document search setup](docs/local-document-search.md)

Add absolute repository paths to `repositories` in your configuration to enable planning and edits. Inspect the selected file contents and proposed diff before approving changes. A separate worktree keeps the proposed files available for testing and review. Approved test commands run with the service account’s operating-system permissions. Use trusted repositories and commands. A Git worktree shares the host filesystem and network.

### State and API

Completed workflow steps save durable, approval-bound checkpoints. See [step checkpoints and compiler recovery](docs/workflow-checkpoints.md) for restart behavior, dependency evidence and explicit continuations.

The [November 23 frontend recovery investigation](docs/frontend-recovery-2026-11-23.md) fixes reserve eligibility for newly exposed compiler errors, complete TypeScript diagnostics and chained continuation reconstruction. CPU controls verify repair dispatch and the unchanged compiler gates; a new live end-to-end result remains pending.

The [November 24 reference recovery investigation](docs/reference-recovery-2026-11-24.md) records Run 24's terminal result and connects rejected reference patches to shared agent correction, with retained stage evidence and the original validation gates.

The [Run 25 tool progress investigation](docs/worker-progress-recovery-2026-11-24.md) adds agent consultation for settled repeated-tool loops, prevents empty-worktree exploration and preserves completed work for an explicitly approved continuation.

The [November 25 development summary](docs/development-summary-2026-11-25.md) records Run 25's completed checkpoint continuation: all 49 independent cases, 14 supplemental cases, required verification profiles, review with no blocking findings and verified delivery passed without operator intervention. It covers the recovery changes, expanded 16/16/16 limits, retained nonblocking review notes and offline verification limits. The owned GPU backend was released.

The [November 24 development summary](docs/development-summary-2026-11-24.md) preserves the preceding paused state and earlier incomplete attempts. The [resume outcome](docs/run25-stage-restart-2026-11-24.md) and [metadata repair investigation](docs/metadata-repair-recovery-2026-11-25.md) trace saved-state recovery after reboot, the failures addressed between continuations and the final unattended completion.

Workflows support [durable pause and resume](docs/workflow-pause.md). **Pause after current operation** saves the current operation's result and acknowledges **paused** before you shut down. Reopen the same data directory after restarting and choose **Resume saved run** to reuse completed receipts and continue under the same approval.

The service listens on loopback. The default state directory is `~/.local-inference-workbench`; use `--data` to choose another directory or `--port` to choose another listener. Model responses, submitted source excerpts and conversation history are stored locally. Use the service under your own operating-system account. Other local programs able to access the listener have access to the workflow API.

For scripts, `POST /api/runs` prepares a run and returns its identifier and digest. Send that digest to `POST /api/runs/{id}/approve`, then read `GET /api/runs/{id}`. An edit produces a second digest for `POST /api/runs/{id}/apply`. Use `GET /api/status` for queue and inference state. Keep operator credentials in the environment and private state outside Git.

<a id="how-the-models-were-chosen"></a>

## Model choices by role

These are the defaults we selected for a 24 GB RTX 3090. Each score names the exercise and what was counted. The [example configuration](config.example.json) gives the exact model tags and generation settings.

These are workload-specific defaults from small development experiments. The [synthetic evidence bundle](benchmarks/evidence/v1/README.md) includes specifications, scoring criteria, selected submissions, per-attempt results and recorded settings. It distinguishes archived grades from assets available for offline regrading. Run `python scripts/verify-benchmark-evidence.py` to check the archive and the published measurement tables without calling a model or executing a submission.

| Role | Selected model | Score on the relevant test | Why we picked it |
|---|---|---|---|
| Fast / Auto, primary reader and synthesis (`fast_primary`) | Gemma 12B Q4_K_M | 20/20 checks across queue reasoning, extraction and synthesis in 66.9 s. | Qwen 9B BF16 also scored 20/20 but took 246 s. Gemma 12B Q8 took 91.1 s and missed the archived-record exclusion. [Reader results](docs/research/reasoning.md#fast-and-auto) |
| Balanced and Verified second reader (`fast_secondary`) | Qwen 3.5 9B Q4_K_M | In the sequential Verified comparison, the Gemma/Qwen Q4 pair delivered 3/3 answers and met 21/22 checks in 284.5 s. | The Q8/BF16 pair also met 21/22 and took 587.7 s. On the separate Balanced extraction, the higher-precision pair did better, 8/8 vs 7/8, but took 209 s vs 92 s. [Pair comparisons](docs/research/reasoning.md) |
| Research query writer (`fast_secondary`) | Qwen 3.5 9B Q4_K_M | 4/4 query checks in 21.0 s, including the request for official documentation. | Qwen BF16 met the same four checks in 102.7 s. Both Gemma 12B packages missed the source preference (3/4). [Query comparison](docs/research/reasoning.md#balanced-and-research) |
| Verified decision (`decider`) | PPLX 32K | Chose the higher-scoring answer in 2/3 cases where the readers earned different scores in the first comparison. | It can choose either reader or request synthesis. It recovered a complete scheduling answer in one case and picked an answer missing a return-value guarantee in another. Alternative deciders were unscored in this comparison. [Decision outcomes](docs/research/reasoning.md#model-precision-in-the-complete-chain) |
| Deep reasoning (`deep_reasoner`) | Gemma 31B QAT | Direct accuracy unmeasured. Verified routed 0/13 scored final answers to Deep. | Retained the existing model pending a direct difficult-reasoning comparison with 31B IQ4_XS. [Deep evidence](docs/research/reasoning.md#verified-and-deep) |
| Repository investigation for Plan and Edit (`fast_primary`) | Gemma 12B Q4_K_M | Investigated a faulty addition function in 6.1 s. The resulting patch passed the positive and negative addition checks. | The smaller reader had already scored 8/8 on source extraction. The edit trial established that it could hand the patch writer the needed context. Investigator alternatives were unscored on this edit. [Live edit](docs/research/planning-and-edits.md#preparing-changes-to-existing-code) |
| Plan and Build planner (`planner`) | Gemma 26B A4B MTP Q4_K_M | 8/8 plans ready across four comparisons repeating two briefs. In the two matched rounds against 31B IQ4_XS, readiness was 4/4 vs 3/4. | IQ4_XS finished sooner but gave a zero-weight entry all ten credits in one example. The 26B Q5 package also went 2/2 in its comparison with Q4 and took 19 s longer. [Planning scores](docs/research/coding-comparisons.md#blind-planning-on-two-briefs) |
| Repository patch writer (`editor`) | Qwen 27B MTP Q4_K_M, thinking off | Its live patch passed the positive and negative addition checks. In a separate coding screen it passed 12/18 attempts and solved all six exercises at least once. | Gemma 31B QAT also passed 12/18, concentrated in four exercises. Qwen covered a wider range of changes and could hand its draft to Gemma for independent review. [Coding screen](docs/research/implementation-screen.md#six-exercises-three-fresh-attempts-each) |
| Repository patch reviewer (`reviewer`) | Gemma 31B QAT, thinking off | In a separate experiment that allowed code rewrites, Gemma raised passing Qwen drafts from 8/17 to 15/17 on the original grader. | The complementary coding results motivated an independent Gemma reviewer. Edit returns an approval verdict rather than rewritten code; its false-approval and missed-defect rates remain unmeasured. [Review comparison](docs/research/implementation-screen.md#when-a-second-model-reviewed-the-draft) |
| Build independent test author (`test_author`) | Gemma 31B IQ4_XS, thinking on | On the expiry cache, all 8 tests passed on both correct controls and caught 10/10 seeded defects on the first submission. | Qwen 9B BF16 discovered zero tests and Gemma 12B Q8 failed to import. IQ4_XS fit fully in VRAM at 32K. Its inventory suite still needed fixture and coverage corrections. [Test-author results](docs/research/test-authorship.md) |
| Build first implementation (`implementer`) | Qwen 27B MTP Q4_K_M, xhigh | Its first combat draft matched 1,000/1,000 reference outcomes in 326.7 s; the earlier Coder-Next draft matched 946/1,000 in 30.3 s. | The current default uses Qwen from the first implementation. The earlier hybrid timing comparison remains historical evidence, with different first-pass accuracy and repair costs. [Build comparison](docs/research/coding-comparisons.md#first-implementation-versus-repaired-implementation) |
| Build diagnosis and repair (`repairer`) | Qwen 27B MTP Q4_K_M, xhigh | One inventory repair passed 26/26 frozen tests and 31/31 held-out inputs. The suite caught 6/6 sampled defects in the repaired code. | Coder-Next had accepted names ending in a newline. Qwen fixed the validator in one 386 s call once a regression reproduced it. Repair remains conditional on a frozen-test failure and uses the same xhigh profile as implementation. [Repair results](docs/research/coding-comparisons.md#first-implementation-versus-repaired-implementation) |
| Interactive chat (user-selectable) | Gemma 12B Q4_K_M initially | A history-preserving switch to Gemma returned a reply in 6.31 s. Open-ended chat quality has no comparative score. | Its 20/20 reader checks and low response time support it as a starting model. Qwen 9B remains an option for documents and Qwen 27B for coding. [Chat observations](docs/research/chat-and-operation.md#what-we-measured) |

The [benchmark index](docs/research/README.md#benchmark-results) links to the full comparisons, including per-attempt results, failure examples and assisted repairs.

The [November 18 Commerce completion report](docs/commerce-end-to-end-completion-2026-11-18.md) records a successful continuation with HyperQwen: 13/13 independent tests, 14/14 ordinary and production-mode regression cases, 4/4 final smoke tests, full API-restart persistence, review with no blocking findings and all 19 audit sections passed. It explains the changes required at each workflow step, including a production router defect ordinary tests and review missed. The owned GPU backend was released. This is an iteratively repaired checkpoint result with offline API/DOM coverage; real-browser coverage, retained P2/P3 notes and fresh-project generalization remain follow-ups. [Derived results and receipt hashes](benchmarks/reports/commerce-completion-2026-11-18.json). The [November 17](docs/commerce-workflow-evaluation-2026-11-17.md) and [November 16](docs/commerce-recovery-evaluation-2026-11-16.md) reports preserve the earlier incomplete milestones. That report published documentation rather than deploying the experimental runtime changes.

The [public whole-project runtime](docs/whole-project-builds.md) includes reference-first test qualification, bounded source repair, source-anchor review, additional verification profiles and exact-owner cleanup. `Runtime.prepare_whole_project` prepares a new plan, application and independently authored tests without importing a repaired checkpoint. The [November 19 fresh-run report](docs/commerce-fresh-evaluation-2026-11-19.md) covers eleven unattended launches, the automatic repairs they exercised, and focused verification of the failures that stopped them. The furthest launch passed smoke verification and reached independent test authoring.

The evidence does not establish that these are the best models for every role. The planning submissions repeat two briefs, Build comparisons have few independent samples, and assisted results are labeled separately. PPLX has no matched alternative-decider comparison here; Deep accuracy and comparative chat quality remain unmeasured. Model packages, generation budgets, loading state and scoring environments differ between experiments. [Evidence coverage and limitations](benchmarks/evidence/v1/README.md)

## Development

The [November 21 development summary](docs/development-summary-2026-11-21.md) records that day's fresh-run outcomes, shared recovery changes, selector accounting, all-role OpenCode compaction and the user-requested Run 17 cancellation. No new fresh end-to-end completion is claimed. Reusable [HyperQwen/OpenCode role bindings](examples/hyperqwen/README.md) and their CPU-only tests are included as an optional integration example.

From the checkout, install the package and run `python -m unittest discover -s tests -v`. The offline suite exercises the workflow boundaries with deterministic model responses. A real generation requires the relevant local models. The runtime lives in `orchestrator/`; `graph.py` defines routing, `runtime.py` handles approvals and execution, `store.py` owns the durable journal, and `chat/` implements the interactive path.

A [runnable API example](docs/api.md) shows preparation, approval and polling with the Python standard library.

See the [operator guide](docs/using-the-workbench.md) for desktop controls, model settings, attachments and chat history.
