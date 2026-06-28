# Real Project Validation Plan

Use this plan before making strong claims about model/system quality.

## Goal

Measure the system on 20 unseen errors from a real Unreal graduation project. The result should separate first-shot accuracy from eventual recovery.

## Case Selection

Collect failures that were not used in eval fixtures, docs, failure memory, or prompt examples.

Recommended mix:

- 3 plugin/module dependency failures
- 3 circular include or generated header failures
- 3 engine-version/API drift failures
- 3 Blueprint-only reference or reflection failures
- 2 DataAsset/AnimMontage/Material link failures
- 2 Editor module vs Runtime module failures
- 2 Config/Input/Enhanced Input failures
- 2 mixed or unknown failures

## Required Metrics

For each case, record:

- case id
- project path
- failing target
- initial error summary
- whether the case was unseen
- whether eval fixtures were excluded from the RAG index
- Pass@1
- Pass@3
- final pass within max attempts
- attempts used
- failure category
- root cause
- files changed
- whether UBT or Editor validation was actually run

## Interpretation

- High Pass@K with low Pass@1 means the recovery loop is useful but first-shot diagnosis is weak.
- High fixture scores with low real-project scores means benchmark overfitting or missing project context.
- A pass without UBT/Editor validation is a proposed fix, not a proven fix.
- A Blueprint/Material/AnimBP/Montage/Sequencer claim is only proven if Unreal Editor loaded, modified, saved, and validated the asset.

## Acceptance Bar

For a strong practical claim:

- 20/20 cases must be unseen.
- Eval fixtures and answer-like artifacts must be excluded from the live RAG index.
- Pass@1 should be reported separately.
- Pass@3 should reach roughly 70-80% or better.
- Failure types should be summarized instead of hidden.

## Output

Store the final result as:

```text
Reports/real_project_eval/<timestamp>/cases.json
Reports/real_project_eval/<timestamp>/summary.md
```
