# Sonnet 5 Gap Plan

This is not a claim that Qwen 3.6 27B equals Claude Sonnet 5.

Sonnet 5 is used here as a workflow target for long-context agentic coding behavior: durable project memory, better tool use, stronger retry judgment, and safer long-running edit loops. Local models should approximate those behaviors through external rails, not model-grade claims.

Suggested safe wording:

> This repository targets a Sonnet 4.5/4.6-style local Unreal C++ workflow today, and tracks Sonnet 5 as a gap-analysis target for future workflow improvements. It does not claim model-grade equivalence.

## Gap Areas

| Sonnet 5-style behavior | Local workflow rail |
|---|---|
| Long-context project memory | Persistent symbol graph and project summary artifacts |
| Better tool use | MCP Essential Tools, one-tool-per-turn policy, required reads |
| Retry judgment | Structured retry state, same-error detection, no-op edit detection |
| Build-system reasoning | Build.cs/module resolver and module graph evidence |
| Evaluation discipline | Pass@1, Pass@K, repeated-error, no-op, wrong-file, and time-to-green metrics |

## Phase 1 Scope

- Add a persistent symbol graph foundation without requiring clangd or Unreal Engine.
- Add taxonomy-driven action routing for common UHT, include/module, linker, Enhanced Input, and editor/runtime boundary errors.
- Add a lightweight Build.cs resolver that suggests dependencies but does not apply patches.
- Add retry-state helpers for repeated-error and no-op detection.
- Expand Pass@K KPI JSON with additional metrics while preserving current CLI behavior.
- Keep README wording conservative.

## Phase 2A Integration

- `agent_orchestrator.py` now attaches optional `errorRoute`, `moduleHints`, and `symbolGraphHints` to compile-fix plans.
- Error routes refine compile-fix RAG modes and add required-read / forbidden-action checkpoints without hard enforcement.
- `lmstudio_unreal_wrapper.py` writes `retry_state.json` after failed build attempts and feeds repeated-error or no-op warnings into the next prompt.
- Module resolver hints are included as evidence-only feedback when known Unreal headers or types suggest likely Build.cs dependencies.
- Symbol graph hints are optional and compact; existing workflows continue when `data/symbol_graph/symbol_graph.json` is absent.
- This remains a Sonnet 5 gap-analysis workflow target, not a Sonnet 5 equivalence claim.

## Phase 2B-1 Retrieval Sidecars

- `rag_search.py` now adds optional structured sidecars for compile/module/reflection fixes and refactor modes.
- Symbol graph sidecars use `data/symbol_graph/symbol_graph.json` when present and silently skip when absent.
- Module resolver sidecars add suspected Unreal module hints for known headers/types such as Enhanced Input, UMG, Niagara, AI, Navigation, and GameplayTags.
- Error route sidecars expose taxonomy routing hints and preferred RAG modes without enforcing patch targets.
- Sidecars are capped and compact; they bias retrieval context but do not replace normal FTS/RAG results.
- This is still a workflow gap-analysis improvement, not a Sonnet 5 equivalence claim.

## Phase 2B-2 Eval Telemetry

- Wrapper RAG assembly can write compact `rag_telemetry.jsonl` records when a run directory exists.
- Telemetry records count normal rows, sidecar rows, sidecar types, suspected modules, error routes, top sources/layers, and context size.
- `report_eval_kpi.py` summarizes KPI JSON, retry state, RAG telemetry, and optional baseline deltas.
- Telemetry is collected before hard enforcement so sidecar impact can be measured before adding constraints.
- Live UBT eval is required before claiming compile-fix workflow improvement.

## Phase 2B-3 Holdout Eval Foundation

- `config/rag_eval_real_project_holdout_cases.json` defines a public-safe `real-project-holdout-v0` fixture suite for Unreal compile/module/reflection failure patterns.
- `validate_holdout_cases.py` checks required fields, duplicate IDs, public path hygiene, taxonomy coverage, and module resolver coverage without requiring Unreal Engine, LM Studio, or network access.
- Holdout reports can now be labeled with `--suite-name` and `--suite-type` to separate fixture-only evidence from live UBT evidence.
- This phase improves evaluation credibility before allowedPatchTargets enforcement or stronger stop conditions.
- Fixture-only holdout success is not a live compile-fix success claim.

## Phase 2C Soft Enforcement

- Phase 2C starts after the first UE 5.8 local live holdout baseline.
- The first target is declaration/definition discipline for header/cpp signature mismatches and LNK2019 missing cpp definitions.
- Enforcement is warning/replan only: required-read reminders, forbidden-action hints, and Build.cs-first warnings when module evidence is absent.
- Module dependency routes still allow Build.cs fixes when include/module evidence exists.
- Hard `allowedPatchTargets` enforcement and stronger stop conditions remain deferred until another live baseline comparison.

## External Rails

The local Qwen workflow should improve through:

- persistent symbol graph
- taxonomy-driven routing
- structured retry memory
- Build.cs resolver
- expanded evals

These rails help approximate long-context agentic coding behavior around a local model, but they do not prove the model itself has Sonnet 5-grade capability.

## Deferred Phase 2

- Enforce taxonomy-driven required reads in the orchestrator.
- Integrate Build.cs resolver suggestions into `module_fix` prompts and wrapper retry context.
- Add a `refactor_r0-r4` multi-file eval suite.
