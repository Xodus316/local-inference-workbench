# Using Local Inference Workbench

## PC control panel

From a Python environment with the package installed, launch the desktop controls.

```sh
inference-workbench-panel
```

The panel uses Tk. On Linux, install the distribution's Tk package for the Python interpreter that launches the panel. To select a configuration, storage directory and port explicitly, run:

```sh
python -m orchestrator.desktop --config config.json --data workbench-data --port 8787
```

1. Open **Configuration** to set the Ollama address, exact installed model names per role and absolute paths for repositories you want to work with. The editor validates the document before saving. Saved changes take effect when the service starts.
2. Click **Start**. The panel launches a child process using its own Python environment. The status line shows when the loopback API responds.
3. Use **Open workflows** or **Open chat**. The desktop view displays recent runs, model calls for the active or latest run, and the model context and GPU allocation reported by Ollama. With NVIDIA tools installed it also shows board-wide GPU memory, utilization, temperature and power.
4. Click **Stop** when work is idle. The panel closes its control pipe and the service shuts down. If the panel discovers a service started from another terminal, it can display status and open both web views. Use the launching terminal to stop that service.

By default the desktop panel stores configuration in `~/.local-inference-workbench/config.json` and service data in the same directory. The standalone server and panel accept the same `--data` option. Use the same directory and configuration when switching between them to see the same history.

## Workflows

Open **Workflows**, choose a mode and enter a request. Add text under **Supporting text** to make a source available for citations. Balanced mode also offers public research when the operator has enabled and configured it. Click **Prepare for review**, then inspect the frozen inputs and roles. **Approve and run** starts the work described in that record. Click a saved run in the browser, or double-click one in the desktop panel, to see individual calls, the result and errors.

For **Code plan** and **Repository edit**, enter an approved absolute repository path and list the relative file paths to include. For an edit, select any configured tests to run after application. The prepared record identifies the files and commands. Review the proposed diff and independent review when generation finishes. **Apply reviewed changes** binds approval to that exact proposal and writes into a separate Git worktree. The result records the worktree path and test receipts.

## Chat

Open **Chat**, select an installed model and send a message. Chat streams directly from Ollama and shares inference admission with the workflow worker. Conversations, messages, model settings and attachment data persist in the service data directory. The sidebar supports searching, selecting and deleting conversations. Use **Settings** to adjust context, sampling and advertised thinking settings for the selected model.

**Stop** requests cancellation. **Edit** replaces a selected message and its following replies. **Regenerate** requests another answer for the last user message. Reloading the page reconnects to an active stream. After a server interruption the partial conversation is available for inspection and an explicit regeneration.

Attach text, code, CSV, JSON, HTML, XML, DOCX or images from the composer. The parser uses a bounded child process. The conversation stores original attachment bytes alongside previews and extracted text. Image-bearing conversations require a model with vision support. To enable text extraction for PDF uploads, install the PDF extra from the project checkout on the computer running the service.

```sh
python -m pip install '.[pdf]'
```

For Windows Command Prompt, use double quotes around `.[pdf]`.
