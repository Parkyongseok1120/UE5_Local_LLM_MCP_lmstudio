# Follow-up evaluation report

## Scope and evidence boundary

- Baseline commit: `897d7f76422bd15cd9b0af22f50e4b6d089c0a9e`.
- The repository build was exercised directly. No installed plugin or active chat was modified.
- The original 865-line transcript does not contain the raw tool payload stream or each final SDK model input. Its direct causal failure therefore remains unconfirmed.
- The live fixture is synthetic. It tests the same classes of pressure and reasoning error, but it is not a reconstruction of the original session.

## Reproduced product defects and minimal fixes

1. A normal verified line result and a bodyless result with the same source key were merged so the bodyless range became current raw input. The projection now maintains verified ranges separately and never promotes the unverified span.
2. Unreal `read_file` byte-window responses were interpreted as line ranges. They now use `utf8_byte`, validate the exact UTF-8 body length and cursor bounds, reject replacement-character bodies, and handle an exactly empty file separately.
3. The old evaluation equated every returned line with input overlap, hard-coded historical reacquisition to zero, and compared only a convenient projection entry. The independent evaluator now compares the actual result against the exact chat passed for the request-producing `modelInputId`, then against only prior verified history.
4. Applied compaction was not counted distinctly from a pressure decision. Each model-input measurement now records the number and modes of compactions that actually replaced history.

No planner, second judgment model, automatic retry, cache substitution, reread blocker, or automatic next-file selection was added. The existing objective-linking logic remains in place.

## Red/green and regression results

| Check | Before fix | After fix |
| --- | ---: | ---: |
| Product availability target | 11 pass / 4 fail | 15 pass / 0 fail |
| Independent evaluation target | 0 pass / 3 fail | 8 pass / 0 fail |
| Full repository regression | not used as the RED signal | 216 pass / 0 fail |

The four product RED failures were the same-key bodyless promotion plus three Unreal byte-window contract cases. The evaluation RED failures covered overlap/reacquisition arithmetic, all-entry oracle comparison, and negated keyword stuffing.

The deterministic pressure trace applied at least two real compactions, confirmed that the older source raw body was absent from the causal third input, and classified the 40-line reread as: returned 40, current-input overlap 0, historical reacquisition 40, new evidence 0.

## Live model measurements

Model: `swift-qwen3.8-27b`, Swift Qwen3.8 27B Q4_K_S, loaded context length 66,816, temperature 0. The fixture has eight versioned files, one deliberately failed old read, one current retained GameEvent raw result, and seven old raw sources that are evicted under pressure. Every live run confirmed at least one current full source and seven absent old sources after a real compaction. Product projections matched the independent oracle in every completed run inspected.

### Metadata A/B

The standalone pilot is preserved in `audit-pressure-pilot-20260921.json`.

| Group | Compactions | Generated calls | Call outcomes | Independent line metrics | Answer score | Run outcome |
| --- | ---: | ---: | --- | --- | ---: | --- |
| A — Observe | 1 | 22 | 0 success / 22 failed path reads | no verified returned range | 2/10 | completed, truncated bounded report |
| B — Inject | 1 | 6 | 6 success / 0 failed | returned 1,090; overlap 0; historical reacquisition 1,090; new 0 | 1/10 | malformed generated tool JSON after the reads |

The pilot proves that the corrected evaluator can observe real historical reacquisition rather than setting it to zero. It does not prove answer-quality improvement. B found the correct source paths and reacquired raw text, but did not finish a valid report.

A requested five-pair A/B run was stopped when the user asked to end it and move to A/C. The surviving console summaries and explicit interruption boundary are preserved in `audit-pressure-main-ab-interrupted-20260921.json`. They are not reported as a completed five-pair experiment. Among the completed main summaries, pair 1 was A: 22 calls, 2/10 and B: 11 calls, 1/10; pair 2 B was 7 calls, 1/10. This is insufficient to establish a reliable metadata effect.

### User-selected audit contract A/C

The exact one-pair exploratory result is preserved in `audit-pressure-ac-20260921.json`.

| Group | Compactions | Generated calls | Call outcomes | Answer score | Elapsed | Outcome |
| --- | ---: | ---: | --- | ---: | ---: | --- |
| A — Observe, base prompt | 1 | 22 | 0 success / 22 failed path reads | 2/10 | 150.870 s | partial answer |
| C — Observe + user-selected contract | 1 | 63 | 0 success / 63 failed path reads | 1/10 | 240.013 s | no visible answer at the 240 s boundary |

This exploratory pair shows no improvement from the audit contract. C took longer, made more failed calls, and produced no answer. The raw run returned without an exception exactly at the abort boundary, so the original JSON has `error: null`; this review treats it as a timeout-boundary outcome rather than a successful completion. The evaluator now records the abort signal explicitly for future runs.

The contract is an evaluation-time, user-selected prompt only. It is not connected to product configuration or automatically injected by the plugin.

## Review of the original 865-line transcript

- The Reset fact was found around the earlier evidence phase and later reopened without a newly identified contradiction.
- The AddMoney call path was also found, but later planning returned to the same question.
- A zero direct-C# search was expanded into an overly broad UI-absence judgment. Later discovery of `UICashPanel` and prefab serialized binding is valid new evidence and should be credited as a correction, not labeled a redundant reread.
- GameEvent reasoning changed between safe and unsafe formulations. The required distinction is self-removal of the current listener versus removal of a lower, not-yet-visited listener; only the former is generally tolerated by that reverse traversal.
- Several runtime causes were proposed without the exact runtime log, callback execution on the active instance, active-state observation, or UI assignment evidence. Those causes remain hypotheses.
- Repeated path-contract mistakes and expansion from `changed_files: 0` to worktree speculation are visible in the transcript, but the missing raw payloads prevent assigning one exact harness cause.

## Conclusion

The response-contract and measurement defects are reproduced, minimally fixed, and covered by regression tests. The independent evaluator now reports causal input overlap and historical reacquisition correctly.

The live experiments do **not** prove that Inject metadata or the supplied audit contract improves final report quality for this model. The A/B main study was explicitly interrupted and must not be presented as complete; the separate A/C exploratory pair was worse under C. The supported conclusion is measurement correctness plus an unproven behavioral benefit, not a claim that a more persistent audit harness solves the model behavior.
