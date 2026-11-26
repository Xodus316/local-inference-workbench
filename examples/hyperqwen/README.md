# Pinned HyperQwen / OpenCode workflow bindings

The [General coding workflow](../../docs/general-coding-workflow.md) provides a
reusable `general_workflow.py prepare/start/resume` launcher and `general_audit.py`.
Use project configuration and a prompt instead of copying a Commerce or Snake
trial launcher. It binds the normal Build API to the shared recovery engine.

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

`mixed_models.profile(qwen_pin, inkling_pin, executable, source_bytes=...)` explicitly assigns only the application repair role to Inkling 2 Mini W4A16 g128 GPTQ through pinned HyperInkling with DSpark enabled. Qwen retains planning, implementation, smoke/independent test authoring and review. Every role still uses OpenCode with compaction. The core approval records each role's model, manifest, context and isolated session; it makes no claim that all roles share weights. Test-source corrections remain with the isolated Qwen test author.

`mixed_models.Backends` creates two stopped, exactly owned containers and activates one model at a time. Before switching it requires the previous transport to drain and its exact owned process to stop. A failed or uncertain handoff blocks further activation. Review acquires its Qwen lease through the same manager. Pause attempts to drain and stop both backends; restart uses new leases and the unchanged approval. Native usage and schema checks, CPU tokenizer admission and all compiler/acceptance gates remain required. The Inkling pin supplies the explicitly provisioned host checkpoint path; its image, DSpark/FP8 settings, metadata and source identities are frozen. Preparation does not start either model.

HyperInkling's canonical model and `local` alias identify the same pinned deployment. Its approval freezes both names, and model admission requires the exact alias inventory with matching root and context for every entry. Unknown, duplicate, missing or differently bound aliases are rejected. Existing HyperQwen deployments retain their single-model rule. [Run 19's terminal outcome and the compatibility fix](../../docs/run19-mixed-model-recovery-2026-11-23.md) distinguish the failed Commerce run from a separate successful synthetic repair probe.

[Pause and restart instructions](../../docs/workflow-pause.md) cover the Workflows controls, API and external pause command. The included `run_workflow.py` runs or resumes an already prepared bundle with matching local configuration and deployment inputs. It waits through **pausing**, saves a durable **paused** acknowledgement after draining transport and stopping its owned container, and reconstructs the approved workers when resumed in a new process. Wait for **paused** and launcher cleanup before shutting down.

Run the synthetic CPU-only checks from the repository root:

```powershell
python -m unittest discover -s examples/hyperqwen/tests -v
```

These checks launch no model, GPU container or real inference request. They validate all role configurations, compaction protocol boundaries, reviewer delegation, identity guards, schema handling and settled-versus-unknown evidence. The repository-relative import guard replaces the experiment's checkout-specific path. The exported example has not had a new live end-to-end trial; [the development summary](../../docs/development-summary-2026-11-21.md) reports the actual local outcomes and remaining gaps.
