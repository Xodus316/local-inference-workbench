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

On Windows, launch `.\.venv\Scripts\python -m orchestrator.desktop --config config.local.json` from the checkout. The panel can start and stop its service, open the workflow and chat interfaces, edit local configuration and show recent runs, resident models and NVIDIA GPU measurements. The desktop interface uses Tk. Starting the service from a terminal is also supported.

### Optional capabilities

Verified requires a compatible local System One endpoint and decision model. The adapter sends typed choices to `/v1/systemone`; the example decider name is a local alias. Confirm your runtime supports this protocol before enabling Verified. Ordinary generation uses Ollama's native streaming chat endpoint.

For internet access, set `research.enabled` and choose `research.provider`. The example selects Exa’s hosted search, which works without an account key. Ollama Web Search and Brave Search are also supported. Select **Internet Research** for a required lookup or enable optional search on a Balanced run. [Windows setup and provider comparison](docs/internet-research.md)

Add absolute repository paths to `repositories` in your configuration to enable planning and edits. Inspect the selected file contents and proposed diff before approving changes. A separate worktree keeps the proposed files available for testing and review. Approved test commands run with the service account’s operating-system permissions. Use trusted repositories and commands. A Git worktree shares the host filesystem and network.

### State and API

The service listens on loopback. The default state directory is `~/.local-inference-workbench`; use `--data` to choose another directory or `--port` to choose another listener. Model responses, submitted source excerpts and conversation history are stored locally. Use the service under your own operating-system account. Other local programs able to access the listener have access to the workflow API.

For scripts, `POST /api/runs` prepares a run and returns its identifier and digest. Send that digest to `POST /api/runs/{id}/approve`, then read `GET /api/runs/{id}`. An edit produces a second digest for `POST /api/runs/{id}/apply`. Use `GET /api/status` for queue and inference state. Keep operator credentials in the environment and private state outside Git.

<a id="how-the-models-were-chosen"></a>

## Model choices by role

These are the defaults we selected for a 24 GB RTX 3090. Each score names the exercise and what was counted. The [example configuration](config.example.json) gives the exact model tags and generation settings.

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
| Repository patch reviewer (`reviewer`) | Gemma 31B QAT, thinking off | In a separate experiment that allowed revisions, Gemma review raised passing Qwen drafts from 8/17 to 15/17 on the original grader. | Reversing the roles lowered passes from 13/18 to 12/18 on different drafts. This supports Qwen writing and Gemma reviewing. In Edit, Gemma issues a verdict on the proposed diff. [Review comparison](docs/research/implementation-screen.md#when-a-second-model-reviewed-the-draft) |
| Build independent test author (`test_author`) | Gemma 31B IQ4_XS, thinking on | On the expiry cache, all 8 tests passed on both correct controls and caught 10/10 seeded defects on the first submission. | Qwen 9B BF16 discovered zero tests and Gemma 12B Q8 failed to import. IQ4_XS fit fully in VRAM at 32K. Its inventory suite still needed fixture and coverage corrections. [Test-author results](docs/research/test-authorship.md) |
| Build first implementation (`implementer`) | Qwen3-Coder-Next UD-IQ1_M, native mode | First combat draft matched 946/1,000 reference outcomes in 30.3 s. Qwen 27B xhigh matched 1,000/1,000 in 326.7 s. | We chose the faster draft with repair available. In a later matched run, Coder-Next followed by Qwen repair took 395.3 s vs 520.1 s for Qwen from the start. Both final programs matched 1,000/1,000. [Build comparison](docs/research/coding-comparisons.md#first-implementation-versus-repaired-implementation) |
| Build diagnosis and repair (`repairer`) | Qwen 27B MTP Q4_K_M, xhigh | One inventory repair passed 26/26 frozen tests and 31/31 held-out inputs. The suite caught 6/6 sampled defects in the repaired code. | Coder-Next had accepted names ending in a newline. Qwen fixed the validator in one 386 s call once a regression reproduced it. This lets Build spend xhigh time on a demonstrated failure. [Repair results](docs/research/coding-comparisons.md#first-implementation-versus-repaired-implementation) |
| Interactive chat (user-selectable) | Gemma 12B Q4_K_M initially | A history-preserving switch to Gemma returned a reply in 6.31 s. Open-ended chat quality has no comparative score. | Its 20/20 reader checks and low response time support it as a starting model. Qwen 9B remains an option for documents and Qwen 27B for coding. [Chat observations](docs/research/chat-and-operation.md#what-we-measured) |

The [benchmark index](docs/research/README.md#benchmark-results) links to the full comparisons, including per-attempt results, failure examples and assisted repairs.

## Development

From the checkout, install the package and run `python -m unittest discover -s tests -v`. The offline suite exercises the workflow boundaries with deterministic model responses. A real generation requires the relevant local models. The runtime lives in `orchestrator/`; `graph.py` defines routing, `runtime.py` handles approvals and execution, `store.py` owns the durable journal, and `chat/` implements the interactive path.

A [runnable API example](docs/api.md) shows preparation, approval and polling with the Python standard library.

See the [operator guide](docs/using-the-workbench.md) for desktop controls, model settings, attachments and chat history.
