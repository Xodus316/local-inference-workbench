# Run a source-grounded summary

Start the service with an installed `fast_primary` model. A prepared run records the prompt, sources and effective settings. Approval queues exactly the bundle identified by its digest. This example uses the Python standard library.

```python
import json
import time
from urllib.request import Request, urlopen

base = "http://127.0.0.1:8787"

def api(path, value=None):
    data = json.dumps(value).encode("utf-8") if value is not None else None
    request = Request(base + path, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)

prepared = api("/api/runs", {
    "mode": "fast",
    "prompt": "Summarize the corrected shipment count and explain which units it includes.",
    "sources": [{
        "id": "ledger",
        "text": "Initial count: 18 boxes including 2 returns.\nCorrection: shipped 16 boxes excluding returns."
    }]
})
path = "/api/runs/" + prepared["id"]
print(json.dumps(prepared["bundle"], indent=2))
if input("Approve this run? [y/N] ").strip().lower() == "y":
    api(path + "/approve", {"digest": prepared["digest"]})
    while True:
        run = api(path)
        if run["status"] not in {"queued", "running", "cancelling"}:
            print(json.dumps({"status": run["status"], "result": run.get("result"),
                              "error": run.get("error")}, indent=2))
            break
        time.sleep(1)
```

For `balanced` or `verified`, the readers first see the source packet independently. Answer citations refer to line spans in a source supplied with a stable ID. Source validation can establish that a quoted span exists. Checking whether the span supports the model's conclusion still requires reading it.

For `plan` or `edit`, add an absolute allowlisted `repository` directory and a list of relative `files`. Inspect the prepared scope. An accepted edit returns a proposed diff and an application digest. The application call uses that digest so it cannot authorize a different diff. Read the resulting worktree path and any execution receipts in the run result before integrating the patch.

The model journal records intent before dispatch and saves a returned completion before validating it. If a call ends in an uncertain state, the journal gives the operator a record to reconcile with the native runtime. Research requests may contact Brave when both the operator configuration and the run request authorize research. Model calls use the local Ollama endpoint.

## Build a standalone Python program

Complete the [Build setup](using-the-workbench.md#build-a-python-project) on the service machine. Use the `api` helper, approval and polling loop above with the following request.

```python
prepared = api("/api/runs", {
    "mode": "build",
    "prompt": (
        "Create solution.py using the Python standard library. Read one nonempty JSON "
        "array of integers from stdin. Print a JSON object with the minimum and maximum "
        "under the keys minimum and maximum. Exit nonzero with empty stdout on invalid "
        "JSON, an empty array, a non-array, booleans or any other non-integer value."
    ),
    "sources": []
})
```

`control_files` is optional. To test a suite on a trusted implementation before giving it to the implementer, include an array of objects with relative `path` and UTF-8 `content`. Use paths for implementation files outside `tests/`, such as `solution.py`. The service accepts up to 40 files totaling 16,000 UTF-8 bytes. The approval bundle contains the control source. Model prompts exclude it.

`GET /api/runs/{id}` lists model calls with stage, role, model and dispatch status. At completion, `result.output` contains the generated plan, frozen suite, suite digest, repair count and verification receipts. A completed Build returns the verified project in `result.output.artifact.files`. Each entry carries a relative `path` and its exact `content`. Use the panel to inspect and download these files. A failure or review decision can contain diagnostic receipts. Check the run status before treating any output as verified.

If a Build returns `review_required`, inspect `result.output.verification.strength` for the file, line, original expression, replacement and execution outcome for each sampled change. Some changes produce identical observable behavior for all valid inputs. A surviving change needs a human judgment about equivalence. To inspect the unverified candidate on the service machine, find the last completed `repair_N` in the run's call list, or `implement` if there was no repair. Its native response is in `artifacts/<run-id>/<stage>.raw.json` under the configured data directory. Parse the JSON string in `choices[0].message.content`; implementation responses contain `files` and repair responses contain `artifact.files`. The verified download is available only after the gate passes.
