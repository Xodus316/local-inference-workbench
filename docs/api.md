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
