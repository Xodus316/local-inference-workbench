# Planning, editing and independent checks

## Standalone planning

Gemma 26B A4B MTP Q4_K_M is the planning choice. A planning response must be implementable. A wrong numerical example can make a correct implementation look defective when the example becomes a test.

Two fixed briefs required exact behavior. One distributed an integer credit pool by proportional floors and fractional remainders, including a tie at the final credit. The other aggregated per-meter usage from an unordered stream, retaining the last occurrence of each timestamp after validating every event. It also required the planner to identify a product decision about resets and state a provisional rule. Reviewers checked the arithmetic in the proposed examples before seeing model identity or time. Readiness required at least 8/10, some credit in every dimension and no blocking defect.

| Repetition | Model | Credit allocation | Meter usage | Ready | Combined wall time |
|---|---|---:|---:|---:|---:|
| First | Gemma 26B Q4_K_M | 10/10 | 9/10 | 2/2 | 162.44 s |
| First | Gemma 31B IQ4_XS | 10/10 | 8/10 | 2/2 | 135.12 s |
| Second | Gemma 26B Q4_K_M | 10/10 | 8/10 | 2/2 | 193.13 s |
| Second | Gemma 31B IQ4_XS | 7/10 | 9/10 | 1/2 | 175.19 s |

Both arms used 32,768 context, thinking enabled, temperature 1, top P 0.95, top K 64 and no separate output cap. The 31B model began the first repetition warm. Both models paid loading costs in the second, and sampled allocations fit in VRAM. The 31B model generated 5,300 tokens across its second pair, compared with 16,936 for 26B. Its shorter output helped it finish faster.

In the second allocation response, 31B assigned all ten credits to an ID with zero weight even though the same plan correctly called a zero total invalid. That contradiction blocked readiness. The 26B meter plan omitted the policy for extra event keys. Both meter plans gave internally consistent reset examples while leaving confirmation of the product decision implicit. Four qualified 26B plans across two repetitions supported keeping it. This comparison covers two distinct briefs.

Increasing 26B to Q5 also had a cost. On a separate matched run, both quants qualified twice. Q4 scored 19/20 in 205 seconds and Q5 scored 18/20 in 224 seconds. A further one-shot planning screen produced 12/20 for GLM-4.7-Flash Q4_K_M, 9/20 for its Q5_K_S package, and 9/20 for Nemotron 3.5 Lightning IQ4_XS. None qualified on both briefs. All three ran entirely on GPU in the 32K fit samples. In the allocation exercise, errors included ranking by the wrong remainder and awarding multiple residual credits to the same ID. Successful loading alone tells us very little about plan quality.

## Preparing changes to existing code

The edit workflow separates investigation, patch generation and independent review. Gemma 12B Q4 reads the selected code and describes the change. Qwen 27B MTP prepares the patch. Gemma 31B QAT reviews the proposed result. This gives the reviewer a different model and keeps a reproducible diff available before an operator accepts the change.

In a small live experiment a repository contained an addition function that returned the wrong value. All three calls ran sequentially at 32K, with thinking disabled, temperature zero and an 8,192-token output ceiling. Gemma investigation took 6.09 seconds (357 input tokens, 65 output). Qwen patch generation took 15.64 seconds (421 in, 77 out). Gemma review took 21.71 seconds (433 in, 44 out). The patch changed one file. Positive and negative addition checks passed on the resulting code. The reviewer predicted passing results before execution. The subsequent process exit and assertions established the result.

These measurements establish that the roles can complete an edit together. A single small repair gives little evidence about multi-file refactoring. In a separate local Python allocation CLI experiment, Qwen 27B at xhigh fixed a name validator that admitted a trailing newline. The repair took 386 seconds for 21,900 output tokens including reasoning. Its code passed 26 frozen tests, 31 held-out inputs and detected six sampled defects. This supports selectively spending more inference time on a reproduced defect. It does not estimate the average benefit of xhigh on ordinary edits.

## Why model output needs independent checks

A dense Gemma 31B QAT model at 32K was observed at 91% GPU / 9% CPU on the RTX 3090. A 26B A4B Q4 planner at the same context was fully GPU resident. Their measured native generation rates on one matched coding brief were 18.69 and 103.23 tokens/s. The 26B model produced 8,276 tokens versus 1,860, so the complete calls took 97.21 and 118.21 seconds respectively. Placement, architecture and quantization all differed. The elapsed time cannot be attributed solely to CPU offload.

Model-written tests have two distinct failure modes. An incorrectly constructed success input can reject a correct program. A weak assertion can accept a broken one. For example, to test all-or-nothing stock allocation, request an available item followed by an insufficient one in the same order. Then submit a later order that needs the first item. Check its fulfillment and the exact remaining stock. A sequence of independent, single-item orders cannot expose a partial deduction within one order.

A useful evaluation sequence is to run the tests on a trusted control, try a small set of witnessed changes that violate the contract, freeze the accepted suite, and run the implementation on separate held-out inputs. Track manual fixture repairs, model calls and elapsed time independently. Passing generated tests alone cannot tell whether the program or its expected results were correct.
