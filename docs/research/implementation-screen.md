# How often did the models produce working Python?

Qwen 27B MTP and Gemma 31B QAT each solved **12 of 18 independent attempts (67%)**. Qwen solved each of the six exercises at least once. Gemma passed every draw on four exercises and none on the parser or resumable log. Their identical headline rates conceal a useful difference when choosing a second model to review a draft.

## Six exercises, three fresh attempts each

The model saw a specification and a visible smoke check. Each attempt had to pass that check, every hidden check for the exercise, and the stated restrictions on imports and evaluation. Grading ran the submitted code in isolation. There was no feedback, repair or selection of the best answer. The six exercises had 475 hidden checks between them. A complete attempt gets one vote, so the parser’s 127 checks do not outweigh the async exercise’s 15.

Each exercise column below gives full successes out of **three** draws. Percentages are rounded. The accompanying data file records the finish reason, whole-attempt outcome and available hidden-check counts for the 198 single-model draws in these two tables (11 configurations × 18 attempts). A blank hidden score means no numeric grade was recorded. [Per-attempt measurements](implementation-attempts.csv)

| Model and package | Scheduler | Expiring LRU | Query parser | Resumable log | Pricing | Bounded async | Whole-attempt pass rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| Qwen 27B MTP Q4_K_M | 1 | 3 | 1 | 3 | 3 | 1 | 12/18 (67%) |
| Gemma 31B QAT | 3 | 3 | 0 | 0 | 3 | 3 | 12/18 (67%) |
| Qwen 27B GSQ IQ3_S, native direct | 1 | 3 | 1 | 3 | 3 | 0 | 11/18 (61%) |
| Gemma 26B A4B MTP Q4_K_M | 1 | 1 | 0 | 0 | 1 | 1 | 4/18 (22%) |
| Gemma 12B QAT | 3 | 1 | 0 | 0 | 0 | 0 | 4/18 (22%) |
| Gemma 12B Q4_K_M | 3 | 0 | 0 | 0 | 0 | 0 | 3/18 (17%) |
| Qwen3-Coder 30B A3B Q4_K_M | 0 | 0 | 0 | 0 | 0 | 0 | 0/18 (0%) |
| Qwen3-Coder-Next UD-IQ1_M | 0 | 0 | 0 | 0 | 0 | 0 | 0/18 (0%) |
| GPT-OSS 20B MXFP4, high | 0 | 0 | 0 | 0 | 0 | 0 | 0/18 (0%) |

These runs used 16,384 context, an 8,192-token output limit, temperature 0.2, top P 0.95 and top K 40. Qwen MTP and Gemma used thinking off. The two Coder packages used native generation. GSQ ran through its native direct path. GPT-OSS used high reasoning with the same output allowance. A later correction allowed different valid worker start orders in the async grader. It changed one partial score and left the full-pass counts intact.

### What the hidden checks caught

| Model | Observed reason for a missed pass |
|---|---|
| Qwen 27B MTP | All nine cache, log and pricing attempts passed. Scheduler and parser each had two unsuccessful draws. One async draw hit the output limit. |
| Gemma 31B QAT | Two parser modules had invalid Python. The third mishandled Unicode escapes and invalid syntax (104/127). Each log solution passed 22/24 checks but accepted an unexpected record field and a boolean limit. |
| Qwen GSQ | All 18 calls finished normally and returned code. Its async draws passed 14/15, 11/15 and 9/15 checks. None met the entire async contract. |
| Gemma 26B | Two cache outputs omitted required methods. Its best parser (124/127) accepted leading-zero integers and non-ASCII whitespace. Two pricing draws raised `KeyError` for a coupon missing its kind when the contract required `ValueError`. |
| Gemma 12B Q4_K_M | Each cache solution passed 89/90 checks and accepted a boolean capacity. That distinction matters in Python, where `bool` subclasses `int`. Parser and log submissions also had syntax or escaping errors. |
| Gemma 12B QAT | Six returned code strings contained invalid Python. All three pricing attempts passed 115/117 checks and accepted booleans in two coupon fields that required exact integers. |
| Qwen3-Coder 30B | All three schedulers retained the worker-selection bug. Two cache attempts reached 89/90. One async solution created 2,000 internal workers despite the concurrency bound. |
| Coder-Next IQ1 | Scheduler worker selection, distinguishing a cached `None` from absence, and async result ordering or completion each cost passes. Its best async draw reached 14/15. The later Build role was assessed with an approved plan, independent tests and a separate repair stage. [Build comparison](coding-comparisons.md) |
| GPT-OSS high | All 18 responses spent the entire 8,192-token allowance on reasoning and returned empty final content. There were zero executable candidates. One subsequent 65,536-context probe with a 32,768-token output limit did the same. The other 17 planned probes were never sent. |

### GPT-OSS at two other effort settings

These draws used the same exercises and grader with **65,536 context and 32,768 output tokens**. Medium delivered 16 complete code responses, including two cache modules with literal backslash-n characters instead of line breaks. Two other responses exhausted the output allowance. Low delivered valid Python all 18 times. Both modes accepted boolean coupon values and boolean log limits. The median request time was 49.7 seconds for medium and 6.6 seconds for low.

| GPT-OSS 20B MXFP4 | Scheduler | LRU | Parser | Log | Pricing | Async | Whole-attempt pass rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| Medium | 2 | 1 | 2 | 0 | 0 | 2 | 7/18 (39%) |
| Low | 0 | 3 | 0 | 0 | 0 | 0 | 3/18 (17%) |

Two supplemental async checks exercised subclasses of `int` where an exact integer was required. All six GPT-OSS async candidates missed them. With those checks included, medium fell to **5/18 (28%)** and low stayed at **3/18 (17%)**. The table and per-attempt file use the original grader so that results remain comparable.

## When a second model reviewed the draft

The reviewer received the brief and verbatim draft without execution feedback. It could change a passing answer. Authors used 16K context and reviewers used at least 32K. Both models used thinking off and an 8,192-token output cap. These were fresh drafts in each direction.

| Author → reviewer | Original drafts passing | After review | After review including the two extra async checks |
|---|---:|---:|---:|
| Qwen 27B MTP → Gemma 31B QAT | 8/17 (47%) | 15/17 (88%) | 12/17 (71%) |
| Gemma 31B QAT → Qwen 27B MTP | 13/18 (72%) | 12/18 (67%) | 9/18 (50%) |

One Qwen draft had an unresolved infrastructure failure and was excluded from its 17-pair comparison. On the matched 17 pairs, the two directions finished at 15/17 and 12/17 under the original grader. Qwen's review of one passing Gemma parser claimed reserved words were valid field names. Applying its change admitted `true=1` and `and=1`, taking the revised parser from 127/127 to 125/127. This is a reason to rerun the checks after a review even when the reviewer gives a confident diagnosis.

## Two stress tests with different failure modes

**Transactional database.** Five models each supplied three independent candidates. All 15 returned normally with valid outer JSON, and **each model scored 0/3 complete solutions**. There were 202 held-out histories covering 47,020 operations. A single wrong output, leaked state or mutable alias failed that entire history. Two independent controls agreed on every expected result. This screen used temperature 1.0, 16,384 context and an 8,192-token output limit. The grader initially rejected legal sentinel objects and calls to local helper functions. The final grader allowed both and evaluated all 15 saved programs without regenerating code.

| Package | Complete histories passed in draws 1 / 2 / 3 | Example failure |
|---|---|---|
| Qwen 27B MTP Q4_K_M | 11/202 · 14/202 · disallowed import | Exposed internal record wrappers and read values outside the transaction snapshot. |
| Gemma 12B Q4_K_M | 13/202 · syntax error · 0/202 | Omitted output for unknown transactions. The third candidate had no return statement. |
| Qwen3-Coder 30B Q4_K_M | disallowed global state · same · 2/202 | The third program raised `UnboundLocalError` updating its version counter. |
| Coder-Next IQ1 | 1/202 · 12/202 · syntax error | Confused a stored `None` with a missing value. The final draw ended with an empty loop body. |
| Qwen 27B GSQ IQ3_S | disallowed import · 0/202 · 8/202 | One program returned a dictionary instead of the required list. Another aliased mutable input values. |

**Larger xhigh budget.** In a partial six-exercise rerun at 131,072 context and a 65,536-token reasoning-plus-output allowance, Qwen 27B MTP passed **4/13 resolved attempts**. Its four code answers passed three scheduler draws and one cache draw. Seven other attempts failed in transport or the backend, and two reached the output limit with no final code. One interrupted request and four undispatched requests were excluded. The experimental Qwen GSQ xhigh wrapper passed **1/4 resolved attempts**. One scheduler solution passed, two requests were cancelled after transport failures, and one cache request reached its deadline. Fourteen were never sent. These rates include service failures and cover different exercises in each arm. The direct-mode table above answers a different question.
