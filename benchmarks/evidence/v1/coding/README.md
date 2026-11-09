# Synthetic Python coding evidence

This archive preserves authored programming exercises and the recorded outcomes used to select coding roles. It contains no live task replay, authenticated API envelope, model reasoning, operator path or credential.

## Original single-model screen

[results.json](results.json) contains all 198 outcomes from the [published CSV](../../../../docs/research/implementation-attempts.csv): eleven model/effort configurations, six contracts and three fresh attempts per contract. Every exported whole-attempt outcome, finish reason and available hidden-check count was checked against its retained per-attempt record. A complete pass requires the visible smoke, all historical hidden checks for that contract and the stated restrictions; checks from different contracts are not pooled into an accuracy score.

[contracts/](contracts/) contains the exact frozen prompts, visible smokes, response schema and supplied support files. Source bytes use LF, including original trailing blank lines. Settings, prompt hashes, request/response/source hashes and recorded runtime versions are in the JSON. Model artifact hashes are explicitly null where the compact record did not retain an immutable weight or renderer fingerprint. A recorded model tag is not a reproducible artifact pin.

The original 475-check hidden grader, expected cases and original reference implementations are unavailable in the current source checkout. These are archived historical grades; this pack cannot independently recompute them. The later November 9 grader independently derived from these contracts is a different benchmark. It must not substitute for the original grader or have its pass rates pooled with these results.

GPT-OSS medium and low use larger context/output budgets than the original high-effort cohort. The original CSV grades use the original async checker; two additional exact-integer checks are separately reported in the research narrative. Model output-limit failures and unavailable infrastructure outcomes must retain their disclosed meanings.

## Review and rewriting

[review-results.json](review-results.json) records 36 planned author/reviewer pairs, including the one excluded infrastructure outcome and known output-limit failure. It includes retained-pass, fixed, regressed and still-failing outcomes; the matched 17-pair comparison; original and supplemental-async grades; and hashes and paths for 68 exact source submissions in [review/candidates/](review/candidates/).

The [review template](review/review-template.txt) asks for issues **and a complete replacement module**. Reviewers received the original synthetic contract and exact draft, without execution feedback. This measures a review-and-rewrite pipeline. It does not establish approval/rejection accuracy for Workbench's deployed verdict-only Edit reviewer. Same-model self-review and additional independent sampling were not controls in this experiment.

The [supplemental worker](review/supplemental-worker.py) contains only the two extra async exact-integer checks. It does not reconstruct the original grader. The original insufficient-context review batch is excluded from the final scores.

## Transaction stress test

[transactions/](transactions/) includes the frozen synthetic transaction contract, response schema and recorded grades for fifteen candidates. The historical unit is an entire operation history: 202 histories containing 47,020 commands. Two independent controls agreed on expected results, and the final uniformly audited validator corrected its treatment of legal sentinels and helper calls.

The original transaction grader, controls and expected histories are also unavailable here. This is archival evidence rather than a locally reproducible historical score. The November 9 six-contract grader is unrelated to this transaction experiment.

## Handling submissions

Generated submissions are untrusted source artifacts. Some are invalid Python or intentionally fail their contracts. Never import or execute them on the host, discover them as repository tests, repair their source, or infer correctness merely from a normal model stop. Code execution belongs in disposable, network-disabled containers with bounded time and writable temporary space. The [interval archive](../interval/README.md) has a complete original scoring corpus for an offline score replay.
