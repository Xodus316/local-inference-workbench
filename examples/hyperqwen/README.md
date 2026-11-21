# Pinned HyperQwen / OpenCode workflow bindings

This optional integration example runs the planner, implementer, repairer, test author and reviewer through the same pinned OpenCode executable, with isolated role sessions and auto compaction enabled. It publishes the reusable native bindings used in the November 20–21 Commerce experiments. It does not include an application, reference implementation, evaluation fixtures, model weights, credentials, run journals or a ready-made benchmark approval.

The core workbench still uses its configured default provider. These modules are an explicit custom-provider integration, not an automatic backend switch. The model/image/context settings in `native_launch_profile.py` are an allowlist for the measured Windows/Docker deployment; provision the named model/cache volumes and supply your own validated model manifest before executing them. The local trial used OpenCode 1.19.0, Qwen 3.8 27B W4A16 AutoRound-fast and a 98,304-token context. Every profile change requires new identities and approval. Nothing starts a model merely by importing modules or constructing a preview profile.

From the repository root, add this directory to the custom launcher's import path:

```python
from pathlib import Path
import sys

sys.path.insert(0, str(Path("examples/hyperqwen").resolve()))
from native_adapter import Backend, Worker, ReviewerProvider, profile
from role_routes import validate_opencode_routes

# pin is the validated manifest for an explicitly provisioned local deployment.
# executable is the exact installed OpenCode executable, not a shell command.
bindings = profile(pin, executable, source_bytes=source_bytes)
validate_opencode_routes(bindings)
```

A custom launcher must bind the returned worker, tokenizer and provider identities into its prepared approval, freeze its own task/environment/verifier inputs, construct `ReviewerProvider(backend, pin, executable)`, and serialize GPU access through the workflow inference lock. It must own and close the exact `Backend` instance. `Worker` covers all five roles; the four non-review workers are exposed in `whole_project_workers`, while the reviewer worker is part of the provider identity. The reviewer performs its exact one-use CPU prompt admission before invoking OpenCode. There is no separate direct model-call fallback.

The bridge verifies role, session, pinned model, sampling, reasoning/completion limits, schema and terminal native usage. Structured/no-tool decisions permit one primary response and at most eight stopped compaction requests. Only the pinned CLI's summary protocol can be treated as an auxiliary compaction; it cannot replace the final schema-bound response. Unknown execution remains a barrier. Compaction manages context and does not enlarge a single response's completion ceiling.

Run the synthetic CPU-only checks from the repository root:

```powershell
python -m unittest discover -s examples/hyperqwen/tests -v
```

These checks launch no model, GPU container or real inference request. They validate all role configurations, compaction protocol boundaries, reviewer delegation, identity guards, schema handling and settled-versus-unknown evidence. The repository-relative import guard replaces the experiment's checkout-specific path. The exported example has not had a new live end-to-end trial; [the development summary](../../docs/development-summary-2026-11-21.md) reports the actual local outcomes and remaining gaps.
