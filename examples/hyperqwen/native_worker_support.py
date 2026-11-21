"""Extracted native bridge bindings for the public OpenCode worker only."""
from copy import deepcopy
import json
from pathlib import Path
from native_import_guard import assert_public_runtime
assert_public_runtime()
from orchestrator.project_opencode import _PROVIDER
from orchestrator.project_opencode_whole import OpenCodeProjectWorker
from native_launch_profile import BACKEND, CONTEXT, MODEL, KEY_ENV, KEY_REFERENCE, SAMPLING
NATIVE_SAMPLING = SAMPLING

class HyperQwenWholeProjectWorker(OpenCodeProjectWorker):
    def identity(self):
        identity = {**super().identity(), "backend": BACKEND, "backend_identity": deepcopy(self._backend_identity),
                    "sampling": dict(NATIVE_SAMPLING)}
        if self.role == "planner":
            identity["planning_policy"] = "task-only-no-tools-v1"
        return identity

    def _verify_backend(self, observed):
        if (self.bridge.endpoint != self.endpoint or not isinstance(observed, dict)
                or observed.get("name") != BACKEND or observed.get("model") != MODEL
                or observed.get("context") != CONTEXT
                or any(observed.get(name) != value for name, value in self._backend_identity.items())):
            raise ValueError("The live HyperQwen backend differs from the approved profile")

    def _resident(self):
        self.bridge.wait_cleanup()
        self._verify_backend(self.bridge.resident())
        return {"name": MODEL, "digest": self._manifest, "context_length": CONTEXT, "backend": BACKEND}

    def _unload(self):
        # The launch owner stops the exact Docker container if the transport
        # cannot settle. A clean bridge drain lets the workflow reuse its lease.
        self.bridge.wait_cleanup()

    def _project_environment(self, state, settings, editable, system, schema, *, tool_policy=None):
        if any(settings.get(key) not in (None, value) for key, value in NATIVE_SAMPLING.items()):
            raise ValueError("HyperQwen requires the approved sampling profile")
        env = super()._project_environment(state, {**settings, **NATIVE_SAMPLING}, editable, system, schema,
                                           tool_policy=tool_policy)
        path = Path(env["OPENCODE_CONFIG"])
        config = json.loads(path.read_text(encoding="utf-8"))
        provider = config["provider"][_PROVIDER]
        provider["name"] = "Workbench HyperQwen"
        provider["options"]["apiKey"] = KEY_REFERENCE
        if self.role != "planner" and tool_policy is None:
            config["permission"]["task"] = "deny"
        # Preserve literal substitution syntax in all supplied content. Only
        # the provider's apiKey can resolve the private process credential.
        serialized = json.dumps(config).replace("{file:", r"\u007bfile:").replace("{env:", r"\u007benv:")
        field = f'"apiKey": {json.dumps(KEY_REFERENCE)}'
        serialized = serialized.replace(field.replace("{env:", r"\u007benv:"), field, 1)
        path.write_text(serialized, encoding="utf-8")
        env[KEY_ENV] = self.bridge.api_key
        return env
