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

An explicitly reviewed serial server patch can expose the `serial-gen-v1`
contract. To consume it, set `native_attempts_observable` to true and include
`"native_attempts_policy": "serial-gen-v1"` in the frozen
`approximation_switches`. The identity probe then requires `/v1/status` to
report `native_single_attempt: true` and the same policy. Bind the patched
server/configuration fingerprints to the approval and verify its provenance
separately. Stock servers remain valid only with attempts unknown.

For this opt-in profile, every terminal completion chunk must provide a strict
integer `strata_native_attempts: 1`. Missing, zero, boolean, string, or multiple
counts fail the protocol without a terminal success or automatic retry. Error
counts and partial bytes remain evidence rather than a successful one-attempt
result. When observability is false, any counter received from the backend is
ignored and the saved attempt count remains null.

Receipts also retain bounded native transport bytes. On disconnects, malformed
frames or rejected counters, `StrataError.raw_response` contains the private
partial receipt. Runtime archives it as a nonterminal transport artifact before
filtering diagnostics. No token count or successful text output substitutes for
the native dispatch counter. Synthetic tests do not establish real cancellation,
RAM fit or the patched server's live compatibility.
