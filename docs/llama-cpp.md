# llama.cpp backend

The `llama-cpp` backend connects to one pre-launched local `llama-server`. It
does not download weights, start processes, change CUDA settings, or manage
Ollama residency. Ollama remains the default backend. A host embedding Workbench
must use its existing physical inference owner for both process transitions and
generation. The standalone Workbench serializes its own calls.

Configure `backend: "llama-cpp"` and a `llama_cpp` object. Use a loopback HTTP
URL without credentials, paths or redirects, normally `http://127.0.0.1:18081`.
Set `api_key_env` to the environment variable containing the key; the secret
itself is never stored in a prepared bundle. `cleanup_timeout_seconds` defaults
to two seconds and is bounded to thirty seconds.

The typed `LlamaCppIdentity` requires the exact model alias, engine/version,
source commit, `/props` build string, context, one slot, binary/package and model
manifest hashes, template and rendered-template hashes, model-path hash, reviewed
configuration hash and launch-attestation hash. It also binds KV types,
context-shift state, speculative mode, expert placement, global output cap and
native-attempt observability. All SHA-256 values use lowercase hexadecimal.
The initial profile supports native non-thinking only: set `think` to `null` or
`false`. It does not implement the separate System One decision protocol.

## Observable identity and operator verification

Preparation, approval and dispatch check `/props`, `/v1/models` and the output of
`/apply-template`. The adapter verifies the model alias, build string and commit
prefix, exact context, one slot, template/model-path hashes, awake state and an
enabled slots endpoint. The rendered-template probe is the UTF-8 prompt returned
for `[{"role":"user","content":"Workbench identity probe."}]` with
`add_generation_prompt: true`. Hash that exact response string.

These observations do **not** prove the executable or GGUF bytes, launch-only
flags, CPU/GPU placement, or a native-attempt count. Stock llama.cpp exposes no
weight hash. In v0.9.0, `/props` constructs fresh request defaults, so its
`n_predict: -1` alone cannot prove the server's global launch limit. The identity
therefore labels HTTP-observed fields separately from operator-verified fields.

The PC/host must verify an immutable launch receipt covering the actual process,
argv, executable/package hashes, effective settings and tensor placement, then
put that receipt's SHA-256 in `launch_attestation_sha256`. Set
`server_output_tokens: -1` only after verifying `--n-predict -1` in the real
launch. An unavailable verification is a deployment blocker, not a value to
invent. The frozen receipt is operator evidence; the client does not claim the
native HTTP API cryptographically attests it.

Use a canonical model manifest with `{"files":[{"name":"example.gguf",
"size_bytes":123,"sha256":"..."}]}`, sorted by filename. Hash UTF-8 JSON with
sorted keys, compact separators, `ensure_ascii=false` and non-finite numbers
forbidden. The host must hash the local files separately; a manifest declaration
is not a file-integrity check.

## Calls and receipts

Each generation uses one `/v1/chat/completions` request with no client retry,
resumable-conversation header, tools or Strata reasoning/recovery options. The
adapter sends a nested `response_format.json_schema` object for structured
output and explicit sampling overrides, mapping `repetition_penalty` to
llama.cpp's `repeat_penalty`. Uncapped generation sends `max_tokens: -1`; both
that request and the verified global launch cap must be uncapped.

Before dispatch, `/apply-template` and `/tokenize` check the complete rendered
prompt with the pinned tokenizer. The prompt and output allowance must fit the
configured context; input is never truncated silently. Positive output limits
are allowed in general use, but do not change the frozen launch context.

Successful receipts retain native `stop` versus `length`, separate final and
reasoning text, native usage, bounded base64 transport bytes, and confirmed slot
cleanup. Invalid final JSON remains unchanged for stage validation. A terminal
choice plus `[DONE]` and observed idle `/slots` are required for normal transport
success. Received bytes, partial content and cleanup state are also attached to
`LlamaCppError.raw_response` after failures. Runtime stores those under a private
`transport` artifact before safe error diagnostics; they never masquerade as a
terminal completion.

Closing a client socket is not proof of native cancellation. The adapter checks
the single native slot after closing the stream, up to the configured cleanup
deadline. A disconnect without an observed terminal choice stays unknown even
when cleanup is confirmed. Public Runtime's durable unresolved-call gate blocks
later inference across runs and restarts. Resolve native uncertainty through the
owning host's reviewed recovery procedure; there is no automatic replay or reset.

Keep `native_attempts_observable` false until the exact runtime/host supplies
authoritative one-attempt evidence. Fake-server tests establish protocol behavior
only, not Windows/CUDA compatibility, RAM fit, real cancellation or benchmark
eligibility.

The implementation targets the documented llama.cpp v0.9.0 server contract.
Check the installed build's actual metadata, schema dialect and startup flags
before using another version.
