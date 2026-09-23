# Regression and runtime report

## Offline and package validation

Gate 0 through Gate 8 each ran the prescribed build/test sequence with exit 0. The final plugin suite reported 323 passed, 0 failed, 0 skipped; focused extracted-policy tests reported 16 passed, 0 failed. Unity suite: 34 passed, 4 skipped, exit 0. Unreal suite: 247 passed, 1 skipped, exit 0. Two final clean TypeScript builds produced identical source/dist fingerprints. git diff --check exit 0.

Unity skips were Windows filesystem cases (NUL/trailing-space names and DOS aliases) and two external Roslyn integration runs. Unreal skipped one POSIX-specific lock case on Windows. They are not counted as passes.

## Runtime

The loaded LM Studio model reported 38,912 context. Exact-template three-cycle fixture and 30-cycle synthetic fixture passed their bounded measurements. Actual-handler synthetic Git and non-Git reads completed with all A/B/C facts. Their batch trace includes a shared-budget refusal for a not-yet-dispatched request followed by successful replan/recovery.

The additional 380K cumulative-context stress probe stopped early and failed its requested completion condition. It reached only 15,300 exact input tokens on its first model call after compaction; that response stopped at maxPredictedTokensReached and had no user-facing final answer. A later diagnostic cycle is excluded. This does not identify a compaction/data-loss defect by itself: the direct-response measurement harness capped generation at 96 tokens and did not use the production delivery controller. No code was changed after the failure.

LM Studio also emitted unknown-channel channel 58 warnings during completed flows. Callbacks/results and final facts arrived in those runs, so no causal loss was observed. Installed GUI plugin/editor workflows were not exercised.

## Acceptance disposition

Architecture/policy and regression evidence are PASS as itemized in HANDOFF.md. SDK-03 is BLOCKED because the SDK does not expose the host-converted/model-normalized tool schema. The requested 380K success condition is FAIL / not reached. Overall requested runtime objective is therefore not PASS.
