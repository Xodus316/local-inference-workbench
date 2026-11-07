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

All four used thinking enabled, a 32,768-token context, no separate output cap, zero presence penalty and matched sampling within each family. The checks and source material were fixed before dispatch. The reviewer scored anonymous outputs before opening timing and identity. Each cell represents one answer. The elapsed times include model loading and transport. Gemma Q4 retained the archived-record exclusion on extraction and handled the other two exercises in the least combined time. The two 7/8 extractions got the corrected count right but left out which records it covered. All four FIFO answers gave the correct removals and final queue. All four synthesis answers resolved the conflicting drafts from the original notes, including the archived-record exclusion.

### Six individual exercises

Each package answered the same six prompts once. A full pass required every rubric item for that exercise. The total weights each item equally, so the eight checks for extraction count more toward it than the three for reviewing the conforming code. These six cases are the ones with [public per-call measurements](measurements.csv).

| Package | Full passes | Checks met | Research queries | Defective code | Conforming code |
|---|---:|---:|---:|---:|---:|
| Gemma 12B Q4_K_M | 4/6 (67%) | 30/32 (94%) | 3/4 | 4/5 | 3/3 |
| Gemma 12B Q8_0 | 4/6 (67%) | 30/32 (94%) | 3/4 | 5/5 | 3/3 |
| Qwen 9B Q4_K_M | 4/6 (67%) | 30/32 (94%) | 4/4 | 4/5 | 3/3 |
| Qwen 9B BF16 | 4/6 (67%) | 28/32 (88%) | 4/4 | 4/5 | 0/3 |

In the defective Python function, deleting a negative-valued entry while iterating the dictionary itself changes its size and raises `RuntimeError`. Every package found the defect. Only Gemma Q8 gave the requested concrete input, `{"a": -1}`. The other three described the condition and lost the witness point. The conforming control iterated `list(items)`, a snapshot of the keys, so deletion was safe. Both Gemma packages and Qwen Q4 accepted the control. Qwen BF16 spent 744.8 seconds generating 31,917 tokens after an 851-token prompt and reached the 32,768-token context boundary with empty final content. Its 0/3 records an undelivered assessment. The receipt records the length stop. It contains no diagnosis of the long generation.

Gemma's Q4 and Q8 packages used the same renderer and parser. The two Qwen packages used different template and parser paths. Treat the Qwen results as a comparison of deployable packages. Sampled allocations were 8.52 GiB and 12.82 GiB for Gemma Q4 and Q8, and 5.84 GiB and 15.83 GiB for Qwen Q4 and BF16 respectively. Each model fit in GPU memory on its individual measured calls.

## Balanced and research

Balanced gives the same material independently to Gemma 12B and Qwen 9B, then asks Gemma to synthesize. On the complete extraction chain, the Q4 pair scored 7/8 in 92 seconds. The Q8/BF16 pair scored 8/8 in 209 seconds. In the Q4 chain Qwen recorded the exclusion correctly. Gemma included an accurate quotation but omitted the exclusion from the answer itself. A practical synthesis check is to compare the scope of each claim in the final answer with the scope in its supporting passage.

For research-query drafting, Qwen Q4 and BF16 both met four of four checks, taking 21.0 and 102.7 seconds. Both Gemma packages met three. They produced relevant queries and kept private identifiers out of those queries, but omitted the explicit preference for official documentation. This supports Qwen Q4 as a query writer when an external search provider is enabled. This experiment scored the proposed queries. Search-result relevance and the accuracy of an answer after retrieval require separate measurements.

## Verified and Deep

Verified adds a decision between the two answers. The local PPLX decider can select A, select B, request synthesis, or send the request to the Deep model. Keeping this decision explicit lets an operator inspect which answer actually reached the caller. The configured Deep role uses Gemma 31B QAT for difficult general reasoning.

### Model precision in the complete chain

Three briefs exercised corrected-source extraction (8 checks), dependency scheduling (8) and review of conforming Python code (6). Each model produced an independent candidate from the same inputs. PPLX then selected a candidate or requested another stage. Grading checked factual claims and their source references. The all-checks pass rate counts a brief only if its final answer met every item.

| Sequential pair | Answers delivered | All-checks passes | Final checks met | Wall time, three briefs |
|---|---:|---:|---:|---:|
| Gemma 12B Q4 + Qwen 9B Q4 | 3/3 | 2/3 (67%) | 21/22 (95%) | 284.5 s |
| Gemma 12B Q8 + Qwen 9B BF16 | 3/3 | 2/3 (67%) | 21/22 (95%) | 587.7 s |

The Q4 pair lost its extraction point for omitting the archived-record exclusion. The higher-precision pair lost a code-review point when PPLX picked Qwen's 5/6 answer over Gemma's 6/6 answer. Qwen correctly explained in-place mutation but omitted whether the function returned the **same dictionary object**. That answer took its evidence from the correct code line. The line alone did not explain the return contract.

Across all seven delivered answers in this campaign, three pairs had unequal candidate scores. PPLX chose the higher-scoring candidate twice. On one schedule it selected Gemma's 8/8 over Qwen's 5/8. Qwen gave the right times but misidentified the ascending order of the IDs. On the Q4 code review it selected a 6/6 answer over one that ended its example mid-sentence despite returning valid JSON. The third unequal pair was the higher-precision code review described above.

The original parallel-requested Q4 condition delivered 1/3 answers. For each missing answer one reader completed and its sibling timed out waiting for model admission. Neither missing answer reached PPLX. The service subsequently allowed a compatible sibling to wait within the approved workflow deadline.

### Scheduling after the admission fix

A fresh comparison reran the same three briefs once per Q4 scheduling condition, in rotating order. Two preliminary full-chain smoke runs are excluded. Both readers used thinking at 32,768 context with uncapped output. PPLX used its 32K decision profile.

| Q4 condition | Answers delivered | All-checks passes | Corrected notes | Schedule | Code review | Final checks met | Wall time |
|---|---:|---:|---:|---:|---:|---:|---:|
| Sequential | 3/3 | 2/3 | 7/8 | 8/8 | 6/6 | 21/22 | 233.4 s |
| Parallel requested | 3/3 | 2/3 | 8/8 | 7/8 | 6/6 | 21/22 | 271.9 s |

On corrected notes, sequential PPLX selected Gemma's 7/8 answer over Qwen's 8/8. In the parallel-requested condition it called for synthesis, and Gemma retained Qwen's archived-record exclusion. On scheduling the latter final answer derived the right start and finish times but cited the wrong line for a dependency. Both code-review decisions chose Qwen's 6/6 answer. One Gemma candidate overlooked the same-object return, and the other incorrectly implied that the dictionary size stayed constant during deletion. Across these six decisions PPLX selected the first reader three times, the second reader twice and synthesis once.

All three scored parallel requests began with the decision model resident. One sibling waited 32.14 seconds for admission and then completed. The samples showed zero overlap in admitted generation for those three runs. One extra synthesis cost 26.7 seconds, and a later sequential call benefited from warm weights. These observations support sequential as the default on this machine. With one run per brief and condition, timing includes routing, output length and model warmth.

Across the seven delivered answers in the earlier campaign and the six in the follow-up, PPLX sent **0/13** to Deep. That measures routing frequency on these briefs. Direct answer quality for the incumbent Gemma 31B QAT Deep role was unmeasured here. A blinded comparison on difficult prompts at matched context would be needed to establish whether the smaller 31B quantization can replace it.
