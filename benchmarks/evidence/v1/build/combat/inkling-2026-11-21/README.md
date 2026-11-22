# Inkling 2 Mini on Starship

Both Inkling quants passed all 20 held-out checks on the first attempt and completed the Build without repair. On this RTX 3090, `UD-IQ4_XS` finished 35 seconds sooner and used 836 MiB less device memory for model weights.

| Model | Quant | Context | First-pass checks | Decode tok/s | Build time |
|---|---|---:|---:|---:|---:|
| Inkling 2 Mini | `UD-IQ4_XS` | 98,304 | 20/20 | 48.4 | 7:51 |
| Inkling 2 Mini | `UD-Q4_K_M` | 98,304 | 20/20 | 45.3 | 8:26 |
| Qwen 3.8 27B MTP | `Q4_K_M` | 98,304 | 20/20 | 52.7 | 6:58 |
| Qwen 3.8 Flash-Next | `IQ3_XXS` | 65,536 | 20/20 | 59.3 | 12:45 |
| Qwen 3.8 Flash-Next | `UD-IQ4_XS` | 65,536 | 20/20 | 18.6 | 47:30 |
| Qwen3-Coder-Next | `UD-IQ2_XXS` | 65,536 | 16/20 | 88.4 | 8:12 |
| Qwen3-Coder-Next | `UD-IQ1_M` | 65,536 | 3/20 | 112.1 | 11:46 |
| DeepSeek-R1-Distill-Qwen 32B | `Q4_K_M` | 32,768 | 18/20 | 12.2 | Failed |
| DeepSeek-R1-Distill-Qwen 32B | `IQ4_XS` | 32,768 | 16/20 | 30.9 | Failed |

Each Inkling row represents one scored run of the [synthetic CLI task](../task.txt) with the same frozen Build plan and tests. The independent [20-test suite](suite.json) evaluated each saved first implementation after generation. Both implementations passed the same checks again at final verification. The other rows are archived outcomes. They span different runtimes, context sizes and reasoning settings. Decode rates cover the initial implementation, including any generated reasoning. Build times run from approval to completion and include repairs where applicable. The two Coder-Next runs each used one Qwen repair.

Both Inkling quants used [Unsloth llama.cpp](https://github.com/unslothai/llama.cpp/releases/tag/b12588-mix-c0c3de8) on Windows with all 25 layers on the GPU, one slot, Q8_0 KV caches, and speculative decoding disabled. Peak board memory was 18.77 GiB for `UD-IQ4_XS` and 19.51 GiB for `UD-Q4_K_M`. The smaller quant started warm after a transport probe. The larger quant incurred a managed cold load. An earlier `UD-Q4_K_M` attempt reached the harness's 4 MB stream limit before returning an implementation. The scored runs used a 32 MB stream limit. This exercise measures implementation and supplies no Commerce test-authoring result. [Weight packages and quantization measurements](https://huggingface.co/unsloth/Inkling-2-Mini-GGUF)

[Machine-readable results](results.json) include weight revisions and hashes, the model runtime identity, the accepted Build-input digest, and the held-out suite's exact image ID. Both exported submissions reproduced 20/20 in the pinned Linux amd64 image. To regrade a saved implementation, materialize the chosen submission as `solution.py` and the five `files` entries in `suite.json` at their recorded paths in a disposable project. Run the recorded unittest command in the pinned Python Docker image with networking disabled, read-only project and root filesystems, resource limits, and an execution deadline. Archived source is stored as data.
