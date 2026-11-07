# One-shot coding screen: GLM Flash and Nemotron Lightning

Can a fast local model implement a precise contract and independently write tests that distinguish correct behavior from plausible bugs? Each model received one implementation prompt and one independent test-writing prompt. Test authors received the contract alone. All six calls used thinking enabled, 32,768 context and no separate output cap. All six returned a native stop on the first call.

## Common brief

Implement `merge_ranges(rows)` using the Python standard library. Each row is a two-element list of integers defining an inclusive interval. Return the sorted union. Merge overlaps and adjacency, support negative and arbitrarily large endpoints, and leave the input unchanged even on errors. Reject invalid types, booleans, floats, reversed ranges and malformed pairs with `ValueError`. Runtime must depend on the number of intervals.

For example, `[[4, 5], [1, 3]]` becomes `[[1, 5]]`. There is no missing integer between 3 and 4. A test expecting two output intervals would fail a correct implementation.

## Method and results

Implementations ran in isolated Docker against 12 valid and 17 invalid cases. Independent suites ran against two correct implementations, then against seven deliberate faults if they accepted both controls. Each row below represents two model calls. Time includes model loading where applicable.

| Model | Implementation checks | Generation time | Correct controls accepted by authored tests | Deliberate faults caught | Test generation time |
|---|---:|---:|---:|---:|---:|
| GLM-4.7-Flash Q4_K_M | 16/29 | 42.26 s | 0/2 | Unscored | 56.40 s |
| GLM-4.7-Flash Q5_K_S | 29/29 | 59.92 s | 0/2 | Unscored | 52.37 s |
| Nemotron 3.5 Lightning IQ4_XS | 29/29 | 33.78 s | 2/2 | 6/7 | 45.03 s |

GLM Q4 handled all 12 valid inputs. It defined a pair validator but failed to call it, so only four of 17 invalid-input checks passed. Q5 implemented the contract successfully. Both GLM test authors independently produced a false expectation for adjacent intervals. Running mutation tests after that would count failures already present against correct code.

Nemotron passed implementation in the shortest observed time. Its 18 tests caught failures involving adjacency, gaps, containment, boolean endpoints, reversed intervals and singletons. A mutation that sorted the caller's list in place survived. The nonmutation check began with already-sorted input, so the side effect was invisible. An unsorted witness, a deep copy made before the call and an equality assertion afterward would discriminate it.

This screen earns Nemotron a further implementation and test-design comparison. Q5 remains an interesting implementation alternate. Q4's validation omissions and the false failures from both GLM suites weaken their case for a routine role. One deterministic Python function and one submission per role are a small screen. Generalizing to repository patch generation would require a matched edit comparison with actual diffs and regression checks.
