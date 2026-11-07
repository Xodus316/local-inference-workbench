# Local Inference Workbench

Run a quick summary, compare two independent answers, or prepare a code change using models on your own machine. The workbench pairs a Python/LangGraph workflow engine with Ollama, a PC control panel and a streaming chat interface. SQLite stores approved inputs, model calls and results so you can inspect how an answer was produced.

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

## Workflows

| Mode | What happens |
|---|---|
| Auto / Fast | One reader summarizes or reasons over the prompt and supplied sources. Auto currently selects Fast. |
| Balanced | Two readers inspect the same sources independently. The first model synthesizes their findings. |
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

To enable external research, configure `research.enabled` and set `BRAVE_SEARCH_API_KEY` in the service environment. The operator must also enable research on the individual Balanced request. Search queries are sent to Brave. The model receives bounded search excerpts.

Add absolute repository paths to `repositories` in your configuration to enable planning and edits. Inspect the selected file contents and proposed diff before approving changes. A separate worktree keeps the proposed files available for testing and review. Approved test commands run with the service account’s operating-system permissions. Use trusted repositories and commands. A Git worktree shares the host filesystem and network.

### State and API

The service listens on loopback. The default state directory is `~/.local-inference-workbench`; use `--data` to choose another directory or `--port` to choose another listener. Model responses, submitted source excerpts and conversation history are stored locally. Use the service under your own operating-system account. Other local programs able to access the listener have access to the workflow API.

For scripts, `POST /api/runs` prepares a run and returns its identifier and digest. Send that digest to `POST /api/runs/{id}/approve`, then read `GET /api/runs/{id}`. An edit produces a second digest for `POST /api/runs/{id}/apply`. Use `GET /api/status` for queue and inference state. Keep operator credentials in the environment and private state outside Git.

## How the models were chosen

The [benchmark results](docs/research/README.md#benchmark-results) include pass rates for 198 coding attempts, independent test suites against correct controls and seeded defects, and the failure cases that shaped the Build roles.

The [research guide](docs/research/README.md) covers the RTX 3090 experiments, model roles and their limits. It includes measured quality and wall time for the small-model pair, arbitration outcomes, the effect of GPU residency, numerical errors in candidate plans, and checks on proposed code. The [per-call data](docs/research/measurements.csv) lets you compare quality and time on the same exercises.

## Development

From the checkout, install the package and run `python -m unittest discover -s tests -v`. The offline suite exercises the workflow boundaries with deterministic model responses. A real generation requires the relevant local models. The runtime lives in `orchestrator/`; `graph.py` defines routing, `runtime.py` handles approvals and execution, `store.py` owns the durable journal, and `chat/` implements the interactive path.

A [runnable API example](docs/api.md) shows preparation, approval and polling with the Python standard library.

See the [operator guide](docs/using-the-workbench.md) for desktop controls, model settings, attachments and chat history.
