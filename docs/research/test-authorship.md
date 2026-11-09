# How often did the generated tests work on fresh specifications?

For the timestamped cache, **Gemma 31B IQ4_XS wrote eight tests on its first attempt, passed both correct implementations, and detected all ten seeded defects.** On the inventory specification the same model submitted Python that could not be imported. Nemotron's interval suite detected six of seven defects, but its inventory suite errored on both correct programs. These reversals are the reason Build checks the test author independently of the implementation model.

### Reading the results

A **control** is an independently verified, correct implementation. A suite must discover real tests and pass every test on each required control before we credit it for exposing defects. A **mutation** is a deliberately faulty program with an input known to expose its fault. A 9/10 detection rate means one of those ten faults escaped. A missing score means the run could not establish a valid detection rate. For example, a suite that fails to import will fail on every mutant and every correct control.

The [Build evidence archive](../../benchmarks/evidence/v1/build/README.md) includes available synthetic specifications, controls, fault definitions, saved suites and compact outcome records. Inference used the RTX 3090; cache and inventory scoring ran in Docker on an ARM64 Mac, with container architecture not separately recorded. A score replay records its own image and architecture instead of claiming an identical historical environment. First submissions, syntax-only corrections and guided or manual changes retain separate labels.

The occupancy and cache comparisons used the same brief, output schema and grading corpus within each exercise. Only the public specification and test-writing instructions went to the author. Each first-pass row represents one generated suite. All used thinking, a 32,768-token context and no separate output cap. Times measure submission latency and can include loading. Docker execution and reviewer time are separate.

## Occupancy over a reporting window

The CLI clips reservations to a time window and reports occupied duration, peaks and alert spans. One correct control swept time events and the other worked with segments. The preflight cross-checked 1,389 inputs. The ten faulty versions included off-by-one boundaries, merging errors and type-validation mistakes.

| First submission | Control outcome on each implementation | Faults detected | Time |
|---|---|---:|---:|
| Gemma 31B QAT | Both passed 9/9 tests | 10/10 (100%) | 320.8 s |
| Gemma 12B Q8_0 | Both passed 10/10 tests | 9/10 (90%) | 542.6 s |
| Qwen 3.5 9B BF16 | Coverage rejected. Diagnostic discovery ran zero tests on both controls. | Unscored | 79.0 s |
| Gemma 31B IQ4_XS | Invalid Python. Neither control could import it. | Unscored | 144.8 s |
| Gemma 31B Q4_K_S | Full benchmark withheld after the fit probe offloaded 1.20 GB. | No submission | — |

Gemma 12B Q8 missed the version that accepts a boolean endpoint as an integer. Its negative example sent the literal characters `True` inside JSON, which makes the input invalid JSON. Both a correct parser and the faulty one rejected it before endpoint validation. Sending valid JSON `true` would have isolated the bug. The QAT suite did expose this fault. It still left some unseeded behavior, including whether the program emitted extra output keys, unchecked.

Qwen's 12 methods belonged to a class that did not inherit from `unittest.TestCase`. Its coverage table also omitted two criteria. In the IQ4_XS submission, three characters from the outer JSON, `"},`, ended up after `unittest.main()` in the Python file. In a separately recorded cleanup, removing exactly those three bytes left the assertions intact. **Both controls then passed 11/11, and the suite caught 10/10 faults** without another model call. That measures the saved assertions after syntax cleanup. The first submission still required assistance. IQ4_XS was fully GPU-resident in all 27 occupancy observations at 32K. QAT and IQ4_XS also differed in package provenance and generation length.

## Timestamped cache with expiration

The CLI had to process equal-time operations in the specified order, expire a key exactly at its deadline, and implement deletion and overwrites. Before grading, the two controls were checked against an independent time oracle on 2,500 generated inputs, alongside 13 explicit cases and 29 invalid inputs. Each of the ten seeded faults had a verified witness.

| First submission | Control outcome | Faults detected | Time to first submission |
|---|---|---:|---:|
| Gemma 31B IQ4_XS | Both passed 8/8 tests. No edits or repair calls. | 10/10 (100%) | 147.4 s |
| Gemma 12B Q8_0 | An unmatched closing brace prevented import on both controls. | Unscored | 381.9 s |
| Qwen 3.5 9B BF16 | The coverage map referenced nonexistent tests. Diagnostic discovery ran zero on both controls. | Unscored | 173.1 s |

One case distinguished the implementations at the exact expiry instant. A value inserted at time 10 with a lifetime of 10 must be absent at time 20. Gemma Q8's expected answer kept it alive. Its single syntax-repair call took another 58.8 seconds and included `solution.py` in a test-only manifest, which the workflow rejected. No repaired suite earned a control score. Qwen repeated the missing `TestCase` inheritance seen on occupancy. Its helper also mixed bytes and text.

IQ4_XS exposed expiry one tick late, treating a duration as an absolute time, a zero-duration immortal entry, reversed tie order, refreshing on read and other faults. The model remained fully GPU-resident in all 28 cache observations. The suite used `strip()` to check empty stdout, so whitespace-only output for invalid input could pass. The child-process timeout lacked explicit cleanup. Neither weakness was among the ten seeded faults.

## Atomic inventory allocation

Valid orders used lowercase letters only for IDs. Rejection had to preserve stock for the next order. For the paired first attempts, Nemotron received a reconstruction of the same Build test-author messages, approved 16-criterion plan and output schema used for Gemma's earlier run. The saved Nemotron request matched the reconstructed request. Its service checkout had reported local changes whose contents were not re-audited.

| First submission | Usable suites | Executable evidence | Model time |
|---|---:|---|---:|
| Gemma 31B IQ4_XS | 0/1 | Two malformed string literals blocked import. No official control or mutation score. | 176.1 s |
| Nemotron 3.5 Lightning IQ4_XS | 0/1 | Coverage referenced 7/16 criteria. In diagnostic execution, 0/34 tests passed on each of two controls, with 34 errors each. | 175.0 s |

Gemma used IDs such as `o1` in six fixtures that expected success. Correct programs rejected them. Its Python test data also used lowercase `true` and `false` as names. A transcription with only the two string delimiters fixed ran **9/16 passes, six failures and one error** against the reference. That copy differed in byte count from the original saved file, so these are diagnostic counts for the transcription.

Nemotron passed strings or dictionaries as `input` to a Python subprocess operating in binary mode. Every test errored before checking the CLI result. Source inspection also found flat stock pairs where the contract required a list of pairs, and orders expected to be fulfilled in the wrong priority order. The two control implementations independently passed 31 held-out inputs and nine additional defect witnesses. Scoring stopped after the candidate suite failed the controls. No mutations were run against Nemotron's submission.

**Assisted continuation.** Gemma's guided call fixed IDs and booleans but changed the frozen assumptions and two method names. A reviewer restored the interface, fixed quoting and timeout cleanup, and added cases for gaps such as rejecting an order *after* seeing an available SKU. The resulting **23/23 tests passed the trusted reference**. Two of six sampled faults still survived on the first implementation. Three further regressions covered an invalid second stock entry, zero quantity for a stocked SKU, and a trailing newline in a name. The final suite passed the control, and the repaired implementation passed 26/26 tests and 31/31 held-out inputs. All six sampled mutations then failed assertions. This records the value of the guided and human corrections separately from the initial author score. [Implementation and repair results](coding-comparisons.md)

## What the other fresh screen adds

For interval union, **GLM-4.7-Flash Q4_K_M and Q5_K_S each passed 0/2 controls as test authors**. Both expected adjacent inclusive intervals to remain separate in at least one fixture. GLM Q5 nevertheless passed **29/29 as the implementer**. Nemotron's tests passed **2/2 controls** and exposed **6/7 faults (86%)**. The survivor sorted its argument in place. Nemotron checked input preservation using an already sorted list, making the side effect invisible. This was one implementation call and one independent authorship call per package. [Interval-union screen](interval-union.md)

These results support keeping Gemma 31B IQ4_XS as the independent Build test author with reference checks when available. For choosing between models on the narrower job of *adding assertions to an existing suite*, use the combat comparison. There the models began with 14 frozen tests and descriptions of four known gaps. [Test strengthening and guided repairs](independent-testing.md)
