# Evaluation Risk Register

This project should not claim that a local model is globally "Sonnet 4-grade" from the current internal eval results. The safer claim is narrower:

> Qwen 27B itself is not proven to be Sonnet 4-grade. Inside this RAG/MCP/UBT validation loop, some UE C++ compile-fix and project-review tasks showed practical stability near upper Sonnet 3.7 to lower Sonnet 4 range.

The forward project target is now a Sonnet 4.5-oriented local Unreal workflow. That is a target for future validation, not a current claim.

## Main Risk Classes

The current risks fall into three broad classes:

- Measurement error
- Interpretation error
- Real-world application error

## Ten Specific Risks

1. Benchmark overfitting

Tier C cases such as `missing_generated_h`, `missing_gameplaytags_dep`, `cpp_header_signature_mismatch`, `editor_only_include_runtime`, `raw_uobject_without_uproperty`, and `missing_enhanced_input_dep` are representative Unreal errors. Passing them matters, but it only proves strength on those known error families, not on every UE C++ failure.

2. Pass@K illusion

If Core live passes 3/3 but one case takes up to 4 attempts, the system is good at eventually fixing but still unstable at first-shot fixes. Report Pass@1 separately from Pass@3/Pass@K.

3. Evaluation loop assistance

`eval_project_review.py` gives retry feedback after failures, including forbidden patterns, claim failures, and required mentions. This is useful as a product guardrail, but it means live project-review scores are not pure model scores.

4. Regex/rule scoring false positives and false negatives

Project-review scoring uses regex checks for `mustDetect`, forbidden patterns, and citations. Negation handling helps, but correct answers can fail and wrong answers can pass.

5. Internal KPI interpretation error

`claim9_0` and `estimatedGradeOutOf10` are internal scorecard fields. They are not external benchmark percentages and do not mean "9.6/10 equals objectively 96% of Sonnet 4."

6. Test-case leakage

Eval fixtures, failure memory, example answers, and benchmark docs must stay out of the production RAG index used for live measurement. Otherwise the model may see answer-like material instead of solving the issue.

7. Real project complexity gap

Graduation projects and production projects mix problems that are more complex than isolated fixtures:

- plugin module conflicts
- circular includes
- engine version drift
- C++ classes referenced only by Blueprint
- missing DataAsset, AnimMontage, Material, or animation links
- Editor module and Runtime module mixing
- Config/Input/Enhanced Input mapping problems

8. Blueprint/Material scope confusion

The repository can index editor-exported asset metadata and best-effort graph summaries. Direct Blueprint node rewiring, Material expression rewiring, AnimBP graph mutation, Montage edits, Notify edits, and Sequencer edits still require Unreal Editor automation.

9. MCP/wrapper bugs

Path limits, patch application, snapshot diffs, no-change blockers, Build.cs checks, `safe_output_path`, `normalize_bundle`, and `enforce_edit_limits` are rule-based. They improve safety but can still block correct edits or allow wrong ones in edge cases.

10. Sonnet 4 comparison is not head-to-head

The current comparison is based on internal Tier results, observed behavior, and prior model experience. It is not a same-fixture, same-budget, same-tool head-to-head run against Sonnet 4.

## Required Next Validation

Collect 20 unseen errors from the real graduation project and measure:

- Pass@1
- Pass@3
- final Pass@K if more attempts are allowed
- failure category
- whether the RAG index contained any eval fixture or answer-like material

If unseen real-project Pass@1/Pass@3 reaches roughly 70-80% with no leakage, then the system can be described as practically strong for its target UE workflow.

## v1.2.5 pipeline risks (mitigated)

| Risk | Mitigation |
|------|------------|
| Partial LLM apply left disk dirty after patch/static-gate failure | Transactional `apply_bundle` + static_gate restore |
| Autofix wrote drift but UBT still ran | `autofix_ubt_allowed` / drift-code gate |
| `golden/` indexed into RAG | `collect_unreal_projects` + wrapper `IGNORED_PROJECT_DIRS` skip `golden/` |
| Dry-run / autofix-only / live KPI conflation | Separate eval tiers documented in `Evaluation_Claim_Guardrail.md` |
| MCP write persisted after validation failure | Pre-write snapshot restore in `server.js` |

## Public Wording

Avoid:

> Qwen 27B is Sonnet 4-grade.

Use:

> Qwen 27B itself is not proven to be Sonnet 4-grade, but inside this RAG/MCP/UBT validation loop, some UE C++ compile-fix and project-review tasks showed lower Sonnet 4-like practical stability.
