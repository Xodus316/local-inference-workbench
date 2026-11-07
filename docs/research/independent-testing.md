# Independent test authorship on a combat simulator

Three Gemma models each received one attempt to write a Python test suite from the same accepted plan and command-line specification. **Zero of the three delivered an accepted suite.** Given four specific coverage gaps and 14 existing tests, Gemma 26B later caught **8 of 9 known faults**. Gemma 12B Q8 and Qwen 9B BF16 eventually caught all nine with different kinds of assistance.

## What counted as a pass

The program reads two ships from JSON, simulates combat, and prints the winner or a draw. Weapons start ready and recharge after firing. Both ships fire simultaneously, shields absorb damage before hull, and a temporary pause for recharge must allow combat to continue.

A submission had to contain executable tests, valid paths and a mapping from acceptance criteria to actual test methods. A control is a separately verified implementation. The two controls here came from Coder-Next followed by Qwen repair, and from Qwen 27B. Both passed ten independent combat checks. For each of nine deliberately faulty variants, a separate input had already been verified to distinguish it from correct behavior. Test authors did not receive the control code or those inputs.

**Control pass rate** below counts test methods that passed on each control. **Fault detection rate** counts faulty variants rejected by an assertion, out of nine, once the suite has passed both controls. A red test on correct code invalidates that score. Generation and evaluation used separate stages, so an invalid file manifest could prevent any program execution.

## First submission from the plan alone

All three Gemmas used thinking, a 32,768-token context, no separate output cap, temperature 1, top P 0.95 and top K 64. Each native completion reached a normal stop. Times cover the model calls, including loading.

| Test author | Accepted suites | Model time | First blocking defect |
|---|---:|---:|---|
| Gemma 12B Q4_K_M | 0/1 | 255.0 s plus 118.7 s for coverage repair | Omitted coverage entries for criteria 6–10. The repair supplied an implementation file among the new test files. The combined test source was within its size limit. |
| Gemma 26B A4B MTP Q4_K_M | 0/1 | 217.5 s | Seven executable methods existed. Every coverage entry omitted the test class, so the runner could not resolve the declared references. |
| Gemma 31B QAT | 0/1 | 537.1 s | Emitted the metadata field name `coverage.catches` as a file path outside the tests directory. |

A separate evaluation qualified Gemma 26B's coverage names and executed its original assertions. **5/7 tests passed (71%) on each control.** A shallow copy made both ships share a nested weapons dictionary. The shield-transition case inadvertently gave both ships zero damage. The recharge case gave them identical weapons with zero recharge. Both battles correctly ended in a draw, while the assertions expected a win. All nine faulty variants also produced failures. The two false failures on correct code made that mutation result inconclusive.

## Adding tests for four known gaps

Gemma 26B received feedback about its original fixtures and coverage. It returned 14 tests in 141.4 seconds. The workflow rejected a renamed stalemate method. After a reviewer confirmed that the method still checked the original behavior, the saved tests ran unchanged. Both controls passed **14/14**, and the suite caught **5/9 faults (56%)**. It missed dropped shield overflow, firing only the first weapon, allowing an already-destroyed ship to fire, and declaring a draw while ship 2 could still recharge.

Those 14 tests became the fixed starting suite for the following additions. The prompt described the four behavioral gaps. Existing files were frozen. Each arm had a 32,768-token window and no output cap. Gemma and Qwen used thinking. Devstral Small 2 used its native mode without a thinking switch. The control column reports the same observed result on each of the two controls.

| Test author | Control tests passed | Faults detected | Outcome and cause |
|---|---:|---:|---|
| Gemma 26B Q4, one additions round | 18/18 (100%) | 8/9 (89%) | Three new cases exposed the relevant fault. The fourth gave the unarmed ship just one hull point. Its opponent killed it on the first shot, so a quiet recharge turn never occurred. Model call took 111.3 s. |
| Devstral Small 2 24B Q4_K_M | 17/18 (94%), diagnostic | Unscorable | Repeating the harness in four new files brought the suite to 16,282 bytes, 282 over the limit. In a separate run of the unchanged assertions, one expected ship 1 to win when simultaneous lethal volleys correctly produced a draw. Model call took 80.5 s. |
| Magistral Small 24B Q4_K_M, thinking | No suite to execute | Unscorable | Both full submissions ended with empty final content. A runtime change passed two short final-answer probes, but the second full request again returned zero final bytes after generating 6,419 tokens. The two full calls took 87.9 s and 149.2 s. |
| Qwen 3.5 9B BF16, first additions submission | No suite executed | Unscored | Returned two files but referenced two more absent files in the coverage map. |
| Qwen 3.5 9B BF16, fresh call on the same input | 18/18 (100%) | 7/9 (78%) | Missed first-weapon-only firing and premature stalemate during recharge. A case written for recharge happened to catch shield overflow. The dedicated overflow case let that fault pass. Model call took 89.1 s. |
| Gemma 12B Q8_0, official package | No suite executed as submitted | Unscored as submitted | Put four new methods in a replacement for the frozen original file. Its 14 existing methods were intact, but the additions had to be delivered in a new file. |

An alternate Gemma Q8 package stopped in its setup probe. The model never loaded, the runtime could not establish a terminal generation, and the full comparison was never sent. This package has no authorship result.

## What the two 9/9 results required

**Gemma 12B Q8_0.** The evaluator extracted the four new methods from the saved response into a new file. The method bodies stayed byte-identical, the original 14 tests stayed frozen, and the evaluator remapped four coverage paths. With no further inference, **18/18 tests passed each control and 9/9 faults (100%) were detected**. Its initial-destruction case gave the already-dead ship a lethal volley. Allowing it to fire turned the correct ship 2 win into a draw. The assertions had measured value even though their packaging prevented an automatic pass.

**Qwen 3.5 9B BF16.** After its 7/9 result, a three-call guided follow-up first produced Python with syntax errors. The second call supplied numeric examples but still scored 7/9. The prompt had described firing every turn, while the actual remaining recharge fault declared a draw during a quiet turn. The final call addressed that distinction. The harness packaged Qwen's multiple-weapon example from the rejected Python and its corrected quiet-turn example into executable assertions. **20/20 tests passed each control and 9/9 faults (100%) were detected.** The three follow-up calls together took about 19 minutes.

The quiet-turn example shows what changed. Ship 1 has two hull points and cannot attack. Ship 2 deals one damage, then skips one turn to recharge. On turn one, ship 1 survives with one hull point. On turn two, the faulty simulator declares a draw. The correct simulator waits, and ship 2 wins on turn three. Checking that winner distinguishes the programs using only the public CLI.

The nine faults also covered early firing, sequential damage application, an arbitrary turn cutoff, firing from the wrong arc, and accepting malformed JSON. A 9/9 score describes this fixed corpus. The single initial submission per model and the different follow-up protocols give no estimate of how often either assisted 9/9 would repeat on a new specification.
