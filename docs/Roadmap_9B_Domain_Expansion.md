# 9B Domain Capability Expansion Roadmap

[English](#english) | [한국어](#korean)

**Related:** [Live Validation Results — 2026-07-11](Live_Validation_Results_20260711.md) · [Eval Regression Workflow](Eval_Regression_Workflow.md) · [Project Overview](Project_Overview.md)

## English

Internal workflow roadmap for Qwen 3.5 9B compact track domain expansion. Not a public benchmark claim.

### Baseline (regression floor)

| Field | Value |
|---|---|
| Run ID | `20260711-090534-qwen35-9b` |
| Model | `qwen3.5-9b-deepseek-v4-flash` |
| Profile | `qwen3_5_9b_deepseek_v4_flash` |
| Pass@K | **36/36** |
| Pass@1 | **35/36** |
| Quality counters | wrong-file **0**, Build.cs FP **0**, no-op **0** |
| Artifact | `data/baseline/live_holdout/20260711-090534-qwen35-9b/kpi.json` |

Generate KPI summary:

```powershell
python scripts/report_eval_kpi.py --kpi data/baseline/live_holdout/20260711-090534-qwen35-9b/kpi.json
```

### Acceptance floor (9B compact)

```text
Pass@K >= 36/36
Pass@1 >= 35/36  (target 36/36 after P0 include fix)
wrong-file = 0, Build.cs FP = 0, no-op = 0
write validation p95 <= 2s
```

### P0 miss analysis — `local_component_registration_missing_include`

Live holdout (2026-07-11): **4 attempts**, Pass@K pass, Pass@1 miss.

| Attempt | Observed behavior |
|---:|---|
| 1–3 | Pre-apply validation rejected patches (no actionable missing-include template; generic feedback) |
| 4 | Successful `#include "Components/BoxComponent.h"` in referencing `.cpp` |

Root cause (code): static gate emitted only advisory `MISSING_CPP_SYMBOL_INCLUDE`; taxonomy routed C2027 to generic compile fix; autofix listened for orphan code `MISSING_CONCRETE_COMPONENT_INCLUDE`.

**Fix shipped:**

- `COMPONENT_REGISTRATION_MISSING_INCLUDE` subkind + route
- `scripts/include_resolver.py` → `COMPONENT_REGISTRATION_INCLUDE_MISSING` error finding
- Resolver-driven autofix + retry fingerprint escalation (`inject_include_template`)
- `fixEvidence` on `unreal_agent_plan` for compact models

### Phase 0.5 — RAG stale analysis loop guard

- Capability-split staleness (`indexUsable`, `analysisCanProceed`, `directSourcePreferred`)
- Essential mode: no hidden `unreal_rag_refresh` recommendation
- `read_query_history.py` repeat guard (2nd identical query suppresses full context)
- Analysis markers → `inspect_only` + `project_source_analysis` tool policy
- Smoke: `python scripts/smoke_cinematic_analysis.py`

### Domain expansion status

| Phase | Status | Key artifacts |
|---|---|---|
| fixEvidence | Done | `domain_planner.build_fix_evidence`, plan JSON field |
| Component | Done | `domainKind=component`, 2-slice plan, preflight validators, `config/rag_eval_component_domain.local.json` |
| Subsystem | Done | lifetime selector, lifecycle validators, eval config |
| Plan slices | Done | `plan_slice_state.py`, wrapper auto-progression |
| Small refactor | Done | `allowSmallRefactor` in 9B profiles |
| Replication / GAS / Animation | Scaffold | domain validators + eval configs |
| Architecture | Done | plan-only ambiguity gate |
| Profile A/B | Harness | `scripts/profile_ab_harness.py` |

### Regression gates

```powershell
python scripts/run_9b_regression_gate.py
python scripts/run_9b_regression_gate.py --live   # requires LM Studio
python scripts/eval_pass_at_k.py --autofix-only --case-ids local_component_registration_missing_include
```

### Regression (post domain-expansion, 2026-07-11)

| Run | Pass@K | Pass@1 |
|---|---:|---:|
| `20260711-post-9b-domain` (live) | **36/36** | **36/36** |
| Component case x5 live | 5/5 Pass@1 | — |

Artifact: `data/baseline/live_holdout/20260711-post-9b-domain/kpi.json`

### Remaining limits

- Live 36-case re-run after include fix requires LM Studio + UBT (Tier B).
- Domain eval suites are separate from compile-fix headline KPI.
- Architecture mode is plan-only; no UBT acceptance metric yet.

---

## Korean

9B compact 트랙 도메인 확장 내부 로드맵입니다. 공개 벤치마크 주장이 아닙니다.

### 기준선

- Run ID: `20260711-090534-qwen35-9b`
- Pass@K **36/36**, Pass@1 **35/36**
- 유일한 Pass@1 miss: `local_component_registration_missing_include` (4 attempts)

### P0 miss 분석

컴포넌트 등록 include 누락 — validator/taxonomy/autofix 불일치로 1–3회 validation reject. resolver + error subkind + fixEvidence + retry fingerprint escalation으로 수정.

### 회귀 게이트

`python scripts/run_9b_regression_gate.py` — pytest, cinematic smoke, component autofix-only, node check. `--live` 시 LM Studio live eval 추가.

자세한 acceptance criteria는 [Eval Regression Workflow](Eval_Regression_Workflow.md) 참고.
