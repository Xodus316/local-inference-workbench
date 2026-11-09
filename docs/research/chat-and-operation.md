# Chat and operating a single GPU

Chat is interactive. The operator chooses a model per conversation and can interrupt a reply or change the model while retaining history. A second analysis and adjudication would increase the delay between a question and the first visible text. The interactive path goes directly through the native streaming client and shares model admission with the workflow worker.

Gemma 12B Q4 is the suggested initial selection for text conversations. In the matched reasoning and summarization screen it gave complete answers quickly. Qwen 9B Q4 is a useful alternate for document questions, and Qwen 27B is available when the operator wants coding help. The selection is a usability choice informed by separate workflow experiments. Open-ended conversation quality still needs its own blinded comparison.

## What we measured

An earlier functional exercise against local models recorded fifteen chat jobs. Thirteen completed. A queued request and a generating request were each stopped deliberately. The checks covered model switching with existing history, answers derived from four attached documents, image input, editing, regeneration and explicit thinking settings. Selected examples give a sense of the observed end-to-end cost.

These synthetic functional checks ran on an earlier service implementation. They are historical behavior observations, not a benchmark of the current Workbench chat path or a matched comparison of conversational quality. The initial model choice is informed by the separate reader experiments; this trial does not establish that it is the best chat model.

| Interaction | Model | Reported elapsed time |
|---|---|---:|
| Short initial text reply | Qwen 9B | 3.43 s |
| Continue a conversation after switching models | Gemma 12B | 6.31 s |
| Answer known questions using four documents | Qwen 9B | 5.29 s |
| Recognize an image-only input | Qwen 9B | 0.64 s |
| Explicit thinking setting and conversation instructions | Gemma 12B | 6.85 s |

These five inputs differed in length, history, settings and model warmth. They demonstrate particular behaviors. Prompt processing rate, decoding speed and preference between two answers would require different measurements.

## Reading the control panel

An idle GPU can still have a resident model. Resident bytes describe weights and runtime allocation that remain loaded. GPU utilization describes activity during the sampling interval. A short utilization sample taken after generation can read almost zero even when the model fits entirely on GPU. Compare the native resident allocation with the portion reported in VRAM, and use the final call receipt for generated tokens and elapsed time.

On a 24 GB RTX 3090, the number of generated tokens including reasoning often outweighed the cost of loading the model. In the second two-brief planning comparison, the faster-decoding 26B planner generated over three times as many tokens as 31B IQ4_XS. The latter finished the pair sooner. Scoring the examples revealed an incorrect expected allocation, which changed the choice of planner. The [planning results](planning-and-edits.md) include both measurements.

For a local replication, record the installed model name, quantization, context, thinking setting, output cap, sampling parameters, starting residency and native completion reason. Measure cold loading and warm execution separately if latency is the decision criterion. Preserve invalid outputs and stopped calls in the accounting. If the runtime loses a response, establish whether the native call ended before considering another dispatch.
