# Choosing models for reasoning and research

These measurements come from development experiments on a single RTX 3090 with 24 GB of VRAM. The aim was to choose which model should answer immediately, when a second reader earns its latency, and how to resolve a disagreement. The measurements describe the system used to select the roles. Re-running the public implementation with a different model release, prompt or runtime can produce different results.

## Fast and Auto

Gemma 12B Q4_K_M is the first reader and summary writer. Auto currently chooses Fast. For a corrected set of notes, the model needs to use the latest figure, preserve exclusions and attribute its answer to the correct source. For FIFO reasoning, it needs to reconstruct the event order. For synthesis, it must reconcile conflicting accounts.

| Model | FIFO, out of 4 | Extraction, out of 8 | Synthesis, out of 8 | Summed seconds, three calls |
|---|---:|---:|---:|---:|
| Gemma 12B Q4_K_M | 4 | 8 | 8 | 66.9 |
| Gemma 12B Q8_0 | 4 | 7 | 8 | 91.1 |
| Qwen 9B Q4_K_M | 4 | 7 | 8 | 107.0 |
| Qwen 9B BF16 | 4 | 8 | 8 | 246.0 |

All four used thinking enabled, a 32,768-token context, no separate output cap, zero presence penalty and matched sampling within each family. The checks and source material were fixed before dispatch. The reviewer scored anonymous outputs before opening timing and identity. Each cell represents one answer. The elapsed times include model loading and transport. Gemma Q4 retained the archived-record exclusion on extraction and handled the other two exercises in the least combined time. The [measurements](measurements.csv) include elapsed time, finish reason and generated tokens for each submission.

Gemma's Q4 and Q8 packages used the same renderer and parser. The two Qwen packages used different template and parser paths. Treat the Qwen results as a comparison of deployable packages. Sampled allocations were 8.52 GiB and 12.82 GiB for Gemma Q4 and Q8, and 5.84 GiB and 15.83 GiB for Qwen Q4 and BF16 respectively. Each model fit in GPU memory on its individual measured calls.

## Balanced and research

Balanced gives the same material independently to Gemma 12B and Qwen 9B, then asks Gemma to synthesize. On the complete extraction chain, the Q4 pair scored 7/8 in 92 seconds. The Q8/BF16 pair scored 8/8 in 209 seconds. In the Q4 chain Qwen recorded the exclusion correctly. Gemma included an accurate quotation but omitted the exclusion from the answer itself. A practical synthesis check is to compare the scope of each claim in the final answer with the scope in its supporting passage.

For research-query drafting, Qwen Q4 and BF16 both met four of four checks, taking 21.0 and 102.7 seconds. Both Gemma packages met three. They produced relevant queries and kept private identifiers out of those queries, but omitted the explicit preference for official documentation. This supports Qwen Q4 as a query writer when an external search provider is enabled. This experiment scored the proposed queries. Search-result relevance and the accuracy of an answer after retrieval require separate measurements.

## Verified and Deep

Verified adds a decision between the two answers. The local PPLX decider can select A, select B, request synthesis, or send the request to the Deep model. Keeping this decision explicit lets an operator inspect which answer actually reached the caller. The configured Deep role uses Gemma 31B QAT for difficult general reasoning.

A matched run used corrected notes, dependency scheduling and a short code review. Each brief was run once per scheduling condition, in rotating order. All completion roles used thinking at 32K with uncapped output and a repetition guard. The decider used its 32K decision profile. Two preliminary smokes exercised the full chain. The six scored runs gave the following results.

| Q4 pair | Completed | Final checks | Total wall time |
|---|---:|---:|---:|
| Sequential | 3/3 | 21/22 | 233.4 s |
| Parallel requested | 3/3 | 21/22 | 271.9 s |

One sibling waited 32.14 seconds for admission and then completed. All three scored parallel requests began with the decision model resident. An exclusive inference already held the shared lease when the sibling arrived. The samples showed zero overlap in admitted model generation for those three runs. One extra synthesis cost 26.7 seconds, and a later sequential call benefited from warm weights. Sequential is the more useful starting schedule on this machine. The 38.5-second difference includes these routing and loading effects.

PPLX selected A three times, B twice and synthesis once. On corrected notes it once chose the 7/8 answer over the 8/8 answer. In both code reviews it selected the secondary answer, which explained why deleting from a dictionary while iterating a separate list of keys was safe. A scheduling answer reached the correct times but cited the wrong source line for a dependency. These cases explain why the system retains candidate answers and checks the evidence returned by a judge.

Zero scored decisions selected the Deep fallback. The current choice of 31B QAT is an incumbent choice, with weaker direct evidence for general reasoning than the small models have for summarization. The comparison that would settle a replacement is a direct, blinded Deep run on difficult reasoning prompts with the smaller 31B quantization at the same context, followed by inspection of both accuracy and loading time.
