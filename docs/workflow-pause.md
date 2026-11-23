# Pause a workflow and resume after a restart

The runtime also saves [automatic checkpoints after completed workflow steps](workflow-checkpoints.md), which support recovery after a process interruption. Pause remains the control for a planned shutdown.

Choose **Pause after current operation** in Workflows. The run changes to **pausing** while its current model session, test or other journaled operation finishes. Keep the computer on until the run says **paused**. The runtime first saves the operation's receipt and workflow evidence, checks that no operation is unresolved, and runs the provider's pause cleanup hook. The HyperQwen example drains its transport and verifies that its owned GPU container is stopped before acknowledging the pause.

You can then close the service and shut down. After restarting, launch the same service or custom launcher with the **same configuration and data directory**, select the saved paused run, and choose **Resume saved run**. Resume verifies the approval, configuration and backend identities. The workflow rebuilds its state from its durable journal and reuses completed model and sandbox receipts instead of dispatching them again. Repair counters and frozen source/test identities follow the same replay rules. A finite runtime allowance keeps only the time remaining at pause; time spent shut down does not consume it, and resume does not grant a new full allowance.

Pause is cooperative. A long OpenCode session can take time to finish. Closing the machine while it still says **pausing**, losing power or interrupting an active model stream is not a completed pause. Unknown native completion, unresolved sandbox execution, changed configuration/backend/source identities or failed cleanup block safe resume. Cancel remains a separate action that ends the trial. This feature cannot turn the already-cancelled Run 17's unresolved dispatch into a resumable paused run.

## Service API

```text
POST /api/runs/RUN_ID/pause
{}

GET /api/runs/RUN_ID
# Wait for status == "paused"; pausing is still active.

POST /api/runs/RUN_ID/resume
{"digest": "THE_SAVED_APPROVAL_DIGEST"}
```

Pause requests and acknowledgements survive a service restart. Saved runs expose the latest pause time, remaining runtime allowance and cleanup receipt. Completed calls remain in the original journal. Neither pause nor resume changes the task, application, tests, approval scope or model generation settings. Pause all other active workflows before shutting down the computer.

## A standalone launcher

A launcher without an HTTP control server can receive a durable operator request from another terminal:

```powershell
python -m orchestrator.workflow_pause pause --data PATH_TO_STATE --run-id RUN_ID
python -m orchestrator.workflow_pause status --data PATH_TO_STATE --run-id RUN_ID
```

These commands do not open a second workflow owner or start a model. They write an approval-bound control request and read the saved status. `pause_acknowledged` refers only to the selected run; pause other workflows and stop any separate chat generation before shutdown. Wait for **paused** and for the launcher to release its owned resources before shutting down. The launcher must keep polling while status is **pausing**; it must not mistake a pending pause for terminal completion.

The optional [HyperQwen launcher](../examples/hyperqwen/run_workflow.py) demonstrates reopening the same state directory and reconstructing the approved OpenCode workers/tokenizers after restart. It consumes an already prepared bundle and matching local deployment/configuration; model weights, deployment manifests and private verifier fixtures are not included:

```powershell
python examples/hyperqwen/run_workflow.py resume --config CONFIG.json --data PATH_TO_STATE --pin PIN.json --opencode PATH_TO_OPENCODE --run-id RUN_ID --digest SAVED_DIGEST
```

The runner allocates a new owned backend lease for the resumed process, retains the frozen model/provider identities, and closes that lease after pausing or reaching a terminal result. Do not use an old frozen experimental launcher that exits on any status other than running/queued; use a pause-aware launcher. The default service and the included example understand both **pausing** and **paused**.
