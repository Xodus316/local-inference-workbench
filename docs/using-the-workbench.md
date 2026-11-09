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

1. Open **Configuration** to set the backend, exact installed model names per role and absolute paths for repositories you want to work with. Ollama is the default. Optional backends require the immutable identities described in the [Strata guide](strata.md) or [llama.cpp guide](llama-cpp.md). The editor validates the document before saving. Saved changes take effect when the service starts.
2. Click **Start**. The panel launches a child process using its own Python environment. The status line shows when the loopback API responds.
3. Use **Open workflows** or **Open chat**. The desktop view displays recent runs, model calls for the active or latest run, and the model context and GPU allocation reported by Ollama. With NVIDIA tools installed it also shows board-wide GPU memory, utilization, temperature and power.
4. Click **Stop** when work is idle. The panel closes its control pipe and the service shuts down. If the panel discovers a service started from another terminal, it can display status and open both web views. Use the launching terminal to stop that service.

By default the desktop panel stores configuration in `~/.local-inference-workbench/config.json` and service data in the same directory. The standalone server and panel accept the same `--data` option. Use the same directory and configuration when switching between them to see the same history.

## Workflows

Open **Workflows**, choose a mode and enter a request. Add text under **Supporting text** to make a source available for citations. Choose **Internet Research** for a search with source citations, or select optional public search in **Balanced** mode. [Enable a provider and inspect the results](internet-research.md) Click **Prepare for review**, then inspect the frozen inputs and roles. **Approve and run** starts the work described in that record. Click a saved run in the browser, or double-click one in the desktop panel, to see individual calls, the result and errors.

For **Code plan** and **Repository edit**, enter an approved absolute repository path and list the relative file paths to include. For an edit, select any configured tests to run after application. The prepared record identifies the files and commands. Review the proposed diff and independent review when generation finishes. **Apply reviewed changes** binds approval to that exact proposal and writes into a separate Git worktree. The result records the worktree path and test receipts.

### Build a Python project

Build starts from a written specification. It produces a project with Python source and an independently authored standard-library test suite. The container runs `python3 -m unittest discover -s tests -v`. Start Docker with Linux containers enabled and install the configured image on the computer running the service. With the example configuration, run the following under the same account that starts the workbench.

```sh
docker info
docker pull python:3.13-alpine
docker image inspect python:3.13-alpine
ollama list
```

Check the four model names in `roles` against the installed tags. The example uses Gemma 26B Q4 for `planner`, Gemma 31B IQ4_XS for `test_author`, Qwen3-Coder-Next for `implementer` and Qwen 27B xhigh for `repairer`. If your local tag differs, enter it in **Configuration**, save, then restart the idle service. The example enables thinking for both Gemma roles with a 32,768-token context. The coding roles use 98,304. Coder-Next uses its native generation mode and Qwen repair uses xhigh. The Build profiles have no separate output cap. See the [example configuration](../config.example.json) for the exact settings.

The example tag `gemma4:31b-iq4_xs` is a local alias. Point `test_author` to your installed IQ4_XS package or alias with thinking support. Use a different model for test authorship than for implementation and repair. The repair package must support the configured reasoning effort.

1. Choose **Open Build** in the desktop panel, or open the [Build form](http://127.0.0.1:8787/panel#mode=build) on the default port. Try this specification.

   > Write a single-file Python CLI called `solution.py`. Read one nonempty JSON array of integers from stdin. Print a JSON object with exactly two fields, `minimum` and `maximum`. For `[7, -2, 7]` the result is `{"minimum": -2, "maximum": 7}`. For invalid JSON, an empty array or any non-integer element (including a boolean), exit nonzero with empty stdout. Use only the standard library.

2. If you have a known-correct implementation, select its files under **Trusted reference implementation**. Choose top-level files matching the public interface, such as `solution.py`. These files are used to execute the tests before implementation. **Supporting text** is model-visible and can hold a specification or usage examples.
3. Select **Prepare for review**. Inspect the frozen specification, role assignments, generation settings, execution limits and Docker image identity. **Approve and run** authorizes those inputs. Build uses a disposable project workspace and needs no repository selection.
4. **Model activity** identifies the current stage and model. The test author can repair syntax, fixtures and missing acceptance coverage before the suite freezes. Once the implementation runs, a failing baseline can send diagnostics to the repair model. Each implementation attempt is checked with the same frozen tests.
5. Read **Verification receipts** for execution results and mutation outcomes. A surviving mutation means a sampled code change still passed the tests. By default the test author can add tests once to expose surviving behavior. Existing test files stay frozen. The expanded suite must pass the baseline and mutation checks. An inconclusive result needs operator review. Review the expected behavior and the assertions before integrating generated code.
6. On a completed Build, select a path in **Verified files**. **Copy contents** copies that file. **Download selected file** saves it by its basename. **Download project manifest** saves all verified implementation and test files as JSON, preserving their relative paths and source text.

The [independent test-author results](research/test-authorship.md) show how generated assertions behaved on known-correct and deliberately faulty programs. The [combat repair study](research/independent-testing.md) shows what changed after specific gaps were fed back to the test author.

## Chat

Open **Chat**, select an installed model and send a message. Chat streams directly from Ollama and shares inference admission with the workflow worker. Conversations, messages, model settings and attachment data persist in the service data directory. The sidebar supports searching, selecting and deleting conversations. Use **Settings** to adjust context, sampling and advertised thinking settings for the selected model.

**Stop** requests cancellation. **Edit** replaces a selected message and its following replies. **Regenerate** requests another answer for the last user message. Reloading the page reconnects to an active stream. After a server interruption the partial conversation is available for inspection and an explicit regeneration.

Attach text, code, CSV, JSON, HTML, XML, DOCX or images from the composer. The parser uses a bounded child process. The conversation stores original attachment bytes alongside previews and extracted text. Image-bearing conversations require a model with vision support. To enable text extraction for PDF uploads, install the PDF extra from the project checkout on the computer running the service.

```sh
python -m pip install '.[pdf]'
```

For Windows Command Prompt, use double quotes around `.[pdf]`.
