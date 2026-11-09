# Strata backend

Strata is an opt-in local backend. Ollama remains the default. Start and verify
one Strata model before starting the Workbench; the Workbench never downloads,
launches, restarts, loads or unloads Strata.

Set `backend` to `strata` and add a `strata` object to the configuration. Every
digest is lowercase SHA-256. The API key itself stays in the environment variable
named by `api_key_env` and is never included in a prepared bundle.

```json
{
  "backend": "strata",
  "strata": {
    "url": "http://127.0.0.1:18080",
    "api_key_env": "STRATA_API_KEY",
    "identity": {
      "model": "exact-loaded-model-id",
      "engine": "0.1.58",
      "context": 32768,
      "slots": 1,
      "runtime_sha256": "<64 lowercase hex characters>",
      "model_manifest_sha256": "<64 lowercase hex characters>",
      "template_sha256": "<SHA-256 of /props chat_template UTF-8 text>",
      "model_path_sha256": "<SHA-256 of /props model_path UTF-8 text>",
      "config_sha256": "<SHA-256 of the reviewed run configuration>",
      "memory_mode": "resident_complement",
      "native_attempts_observable": false,
      "approximation_switches": {
        "route_tail_skip": 0,
        "drafting": "off"
      }
    }
  }
}
```

Use the same exact loaded model ID for each role used by a run. Generation
settings must use the launch context exactly. Strata supports `none`, `low`,
`medium` and `high`; the adapter rejects `xhigh` because mapping it to `high`
without an explicit compatibility decision would change an approved profile.

At preparation, approval and immediately before every dispatch, the adapter reads
`/v1/status`, `/v1/models` and `/props`. It verifies the service, loaded model,
engine version, context, one-slot concurrency, template fingerprint and model-path
fingerprint. Operator-verified runtime, model-manifest and run-configuration
digests are also frozen in the approval bundle. A change requires a new prepared
run.

The transport uses `/v1/chat/completions` with streaming, no tools or MCP, and no
automatic client retry. An output cap of `null` omits `max_tokens`. Structured
schemas are placed in the prompt instead of enabling Strata's buffered
`response_format`, so the unmodified final text and separate reasoning channel
can be journaled before local validation.

Strata v0.2.12 can repeat a request internally after non-finite logits before the
first token. Its stock response does not expose that attempt count. Keep
`native_attempts_observable` false unless a separately reviewed server revision
provides an authoritative receipt. A benchmark requiring exactly one native
attempt must reject such a profile before inference; transport success alone is
not an eligibility receipt.
