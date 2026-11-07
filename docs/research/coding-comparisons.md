# Which models planned, implemented and repaired working programs?

Gemma 26B A4B MTP Q4_K_M produced eight ready plans in eight submissions across four blinded comparisons of the same two briefs. Coder-Next produced the fastest first implementation in the matched combat comparisons, while Qwen 27B xhigh more often passed the independent checks on its first attempt. Those observations led to different roles for planning, initial implementation and repair. The independent checks that changed those decisions are described below.

## Blind planning on two briefs

One brief allocated integer credits by proportional weight. Floor division allocated the initial credits, and the largest fractional remainders determined who got the leftover credits, with ties broken by ID. The other brief calculated usage from out-of-order meter readings. A plan had to validate every event, keep the last input at each meter and timestamp, and state a provisional rule for a meter reset. Multiple reset policies were acceptable if the examples followed the stated rule.

Each model produced one plan per brief per comparison. Every plan in the table returned usable structured output. Reviewers scored five dimensions from zero to two, including independently checking the proposed numerical examples, before seeing model identity or timing. A ready plan needed valid structured output, at least 8/10 points, credit in every dimension and no blocking defect. Each pair ran on a 24 GB RTX 3090 with thinking on, a 32,768-token context and no separate output cap. The elapsed column sums model-call wall time for both briefs, including model loading where applicable.

| Comparison | Planner | Allocation /10 | Meter /10 | Plans ready | Elapsed |
|---|---|---:|---:|---:|---:|
| Three Gemmas | Gemma 12B Q8_0 | 7 | 8 | 1/2 (50%) | 180.7 s |
| Three Gemmas | Gemma 26B A4B MTP Q4_K_M | 10 | 9 | 2/2 (100%) | 138.6 s |
| Three Gemmas | Gemma 31B QAT | 8 | 9 | 1/2 (50%) | 314.1 s |
| 26B quantization | Gemma 26B A4B MTP Q4_K_M | 10 | 9 | 2/2 (100%) | 205.4 s |
| 26B quantization | Gemma 26B A4B UD-Q5_K_M | 10 | 8 | 2/2 (100%) | 224.2 s |
| 26B versus 31B, first pair | Gemma 26B A4B MTP Q4_K_M | 10 | 9 | 2/2 (100%) | 162.4 s |
| 26B versus 31B, first pair | Gemma 31B IQ4_XS | 10 | 8 | 2/2 (100%) | 135.1 s |
| 26B versus 31B, repeat | Gemma 26B A4B MTP Q4_K_M | 10 | 8 | 2/2 (100%) | 193.1 s |
| 26B versus 31B, repeat | Gemma 31B IQ4_XS | 7 | 9 | 1/2 (50%) | 175.2 s |
| GLM and Nemotron | GLM-4.7-Flash Q4_K_M | 3 | 9 | 1/2 (50%) | 136.4 s |
| GLM and Nemotron | GLM-4.7-Flash Q5_K_S | 3 | 6 | 0/2 (0%) | 107.1 s |
| GLM and Nemotron | Nemotron 3.5 Lightning IQ4_XS | 5 | 4 | 0/2 (0%) | 68.0 s |

**Gemma 12B Q8_0** described the correct remainder calculation but gave an incompatible expected result. For ten credits and weights one and two, the floors are three and six, and the second ID has the larger remainder. The required result is three and seven. Its proposed four and six would reject a correct program. Its meter plan passed, although the duplicate example would return zero whether the first or last duplicate won.

**Gemma 31B QAT** cleared the numerical threshold on allocation. An explicit assumption allowed accepting the first object in a stream of two concatenated JSON objects. The CLI contract required rejecting the entire input. **31B IQ4_XS** handled the first pair well. On the repeat it awarded ten credits to an ID with zero weight while also saying that zero total weight invalidates the input. Its single failing example was consequential because the proposed test would teach the implementer to accept invalid data.

**Gemma 26B Q4 and Q5** both handled the allocation and tie cases in their matched comparison. The Q5 meter plan left the extra-key rule implicit. Both quantizations gave a consistent reset example but omitted the requested confirmation of the reset policy. Q5 took about 19 seconds longer across the pair and yielded no additional ready plan. Across the two direct comparisons with 31B IQ4_XS, Q4 produced four ready plans in four submissions and IQ4_XS produced three in four. Those four submissions repeated the same two briefs. IQ4_XS was faster in both pairs and began the first pair already loaded.

**GLM Q4** used a faulty remainder update and supplied an incorrect expected allocation. Its meter plan passed with correct examples. **GLM Q5** based its remainder ranking on credits left after the initial distribution. Proportional remainders depend on the original credit pool. Its meter plan also shared one previous-reading variable across meters, so a reading from one meter could affect another meter's usage. **Nemotron** could award multiple residual credits to the same ID and omitted exact allocation examples. Its meter plan left aggregation steps unspecified and included malformed input examples. All six model calls in this comparison finished normally, including the five plans that missed readiness. Nemotron started its first call resident after a fit probe, and GLM Q5's calls were adjacent.

## How much did more thinking help on the combat plan?

An earlier single-brief experiment asked for a turn-based combat CLI with shield damage, firing arcs, recharge and a termination rule. Here the recorded rate measures whether the planner returned a usable structured plan. A later reviewer checked the proposed rules and examples with model identities visible. This experiment had no blind readiness grade or executed-code pass rate.

| Configuration | Usable plans | End-to-end time | What the reviewer found |
|---|---:|---:|---|
| Gemma 31B QAT, thinking on | 1/1 | 106.0 s | Specified simultaneous firing but left eligibility by firing arc unresolved. Its recharge example only required a weapon to skip some turns. |
| Gemma 26B Q4, thinking off | 1/1 | 18.2 s | Switched between targeting the front shield and matching arcs by index. Omitted a definite result for a battle where neither ship could progress. |
| Qwen 27B MTP Q4_K_M, thinking off | 1/1 | 41.2 s | Required positive weapon damage while its own stalemate fixture depended on zero damage. |
| Qwen 27B, medium | 1/1 | 68.8 s | After 30 damage against 50 shield, 20 shield remains. A second 40-damage hit should leave 80 of the original 100 hull. The plan expected 70. |
| Gemma 12B Q4 + Qwen 9B Q4, PPLX selection and Gemma synthesis, thinking off | 1/1 | 74.6 s | The selected plan predicted two hull damage from 12 damage against four successive five-point shields. Its own shield rule absorbs all 12. Synthesis retained that error. |
| Gemma 26B Q4, thinking on, original cap | 0/1 | 120.3 s | Reached the 12,288-token output limit with empty final content. |
| Same committee, thinking on, original cap | 0/1 | 192.8 s | The Gemma candidate hit the limit without returning a final answer. The completed Qwen candidate alone could not finish the workflow. |
| Qwen 27B xhigh, separate context-only comparison | 2/2 | 413.6 s at 32K and 411.2 s at 96K | Both calls returned the identical final plan. Its recharge and overflow examples were internally consistent. It introduced unnecessary input aliases and still allowed a finite battle to end as a draw at a cutoff. |

The original capped series returned five usable plans from seven attempts. In an uncapped 32K repeat, both thinking-on configurations that had failed again filled the window without a final plan. A later concise prompt and sampling change produced final plans from the committee in 112.1 seconds and the 26B model in 105.6 seconds. The committee's winner fixture omitted the opponent's weapons and shields. The 26B plan said a recharge of one prevents firing for one turn, yet its example fired every turn. The later settings improved completion on this brief. Several factors changed together, and neither output established executable correctness.

## First implementation versus repaired implementation

The first matched combat comparison reused a plan and eight generated tests. Both models used a 98,304-token context and no output cap. Qwen3-Coder-Next UD-IQ1_M used its native generation mode, while Qwen 27B MTP Q4_K_M used xhigh. Each model submitted one implementation.

| Author | Frozen tests | Hand-calculated battles | Seeded reference matches | Additional invalid inputs | Implementation time |
|---|---:|---:|---:|---:|---:|
| Coder-Next IQ1 | 8/8 | 8/11 (72.7%) | 946/1,000 (94.6%) | 4/8 | 30.3 s |
| Qwen 27B xhigh | 8/8 | 11/11 (100%) | 1,000/1,000 (100%) | 8/8 | 326.7 s |

Coder-Next decremented a newly started cooldown on the firing turn. A weapon supposed to skip the next turn fired a turn early. It also returned a draw after a fixed turn limit even when the next shot would win. None of the eight frozen fixtures distinguished these outcomes. The four additional invalid inputs that Coder-Next accepted involved boolean hull, negative shields, negative recharge and fractional damage. Some malformed-input expectations exceeded what the original brief specified, so they have their own column.

A later matched pair generated new implementations against the same strengthened suite of 14 tests. It compared two policies, starting with Coder-Next and paying for Qwen only on failure, or using Qwen from the start. Both arms reused the plan and suite. Their end-to-end times include testing and the mutation gate.

| Route | Initial implementation | Repairs | Final frozen tests | Independent checks on final code | Workflow time |
|---|---|---:|---:|---|---:|
| Coder-Next then Qwen xhigh | Failed the frozen suite, 23.8 s | 1 Qwen call, 338.6 s | 14/14 | 11/11 hand cases, 3/3 extra boundaries, 1,000/1,000 seeded battles | 395.3 s |
| Qwen xhigh from the start | Passed 14/14, 494.8 s | 0 | 14/14 | 11/11 hand cases, 3/3 extra boundaries, 1,000/1,000 seeded battles | 520.1 s |

The hybrid run took 124.8 seconds less in this pair. Its first-pass receipt establishes a failure but gives no count of failing tests. Qwen's diagnosis attributed the failure to treating the outer ship pair as one ship and identified an arbitrary turn limit. The reviewed repaired source parsed each ship separately and removed the cutoff.

The original mutation gates requested review on both final programs. Two more regression methods covered ships already destroyed at the start and a lone weapon waiting to recharge. A subsequent recheck executed the same saved code with all 16 tests and ran no inference. Hybrid passed 16/16 and exposed all six sampled mutations, five by assertion and one by reproducible timeout. Qwen passed 16/16 and exposed all four applicable mutations, three by assertion and one by reproducible timeout. Two additional Qwen mutations survived and were judged equivalent on the agreed input domain, including a clamp that assigned zero to a value already equal to zero. Each timeout repeated on the same mutant with passing controls before and after. The two programs had different sampled edits, so these totals measure the tests' sensitivity for each program.

**Inventory replication.** Coder-Next passed 23/23 reviewed tests and 31/31 held-out inputs in its first implementation. Two of six sampled mutations survived. One validated only the first stock entry, and another allowed zero item quantities. Reviewing the source also exposed an end-of-line regex that admitted an ID with a trailing newline. Three added regressions isolated these cases and passed on a trusted control. Qwen's single xhigh repair changed the validator to match the entire name. Its code passed 26/26 frozen tests, 31/31 held-out inputs and all six sampled mutations were detected by assertions. The 30.1-second first implementation and 386.0-second repair came after guided and manual work on the tests. This is an assisted Build result.

The resulting default uses 26B Q4 to plan, Coder-Next to draft and Qwen xhigh to repair reproduced failures. The initial eight-test comparison explains the dependence on independent checks. The stronger fourteen-test comparison shows the observed time and quality tradeoff for one complete pair. Results for the separate interval implementation and independent authorship screen are in the [interval-union study](interval-union.md).
