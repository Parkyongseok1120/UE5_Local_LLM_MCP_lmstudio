# Live Validation Results — 2026-07-11

[English](#english) | [한국어](#korean)

Internal workflow validation only. Not a public benchmark claim.

**Related docs:** [Project Overview](Project_Overview.md) · [Model Measurement Results](Model_Measurement_Results.md) · [Version Performance History](Version_Performance_History.md) · [Evaluation Claim Guardrail](Evaluation_Claim_Guardrail.md)

## English

This page records validation after **Scoped Write Stabilization** landed on the working tree:

| Layer | What ran | Outcome |
|---|---|---|
| Unit / regression | `pytest` (695 passed, 2 skipped) + `node --check` on MCP server | **Pass** |
| Real-project smoke | Project_MJS scoped write validator (read-only) | **Pass** (2 targets) |
| Live holdout | 36-case UE 5.8 local live holdout with LM Studio + UBT on fixture temp copies | **36/36 Pass@K**, **35/36 Pass@1** |

### What changed (Scoped Write Stabilization)

Scoped write validation no longer blocks normal Public/Private `.h`/`.cpp` edits when the write scope is incomplete:

- **P0 write path:** module-relative Public/Private pairing, full-module include index in scoped mode, write-mode include checks (engine/plugin skip), brace-filtered class headers, skip `cpp_definitions_missing` when no paired `.cpp` in scope.
- **P1 accuracy:** raw-string masking, `#ifndef`/`#else`/nested `#if` editor-safe regions, timer 2-pass, delegate receiver chain, replication per-offset class, UPROPERTY comment masking, denylist safe patterns for spaced members.
- **P2 UX / bootstrap:** bootstrap cache isolation on project/workspace change, evaluate-before-write, atomic cache save, `scanMode`/`elapsedMs` in write success payloads.

Key files: `scripts/validate_project_sources.py`, `scripts/unreal_static_validate.py`, `scripts/cpp_parse_utils.py`, `lmstudio-unreal-agent-mcp/src/bootstrap-cache.js`, `lmstudio-unreal-agent-mcp/src/context-ux.js`.

Artifacts:

| Pass | Location |
|---|---|
| Live 36-case KPI | `data/baseline/live_holdout/20260711-090534-qwen35-9b/kpi.json` |
| Per-case wrapper runs | `data/baseline/live_holdout/20260711-090534-qwen35-9b/<case-id>/wrapper_run/` |
| Eval log | `data/baseline/live_holdout/20260711-090534-qwen35-9b/eval.log` |

---

### A. Project_MJS scoped write smoke

Command pattern:

```powershell
python scripts/validate_project_sources.py `
  --project-root "C:\Users\sster\Documents\Git\Project_MJS" `
  --write-target "<relative Source path>" `
  --json
```

| Write target | exit | `hasBlockingErrors` | `scopedFileCount` | `elapsedMs` | Notes |
|---|---:|---|---:|---:|---|
| `Source/Project_MJS/Public/Character/Player/Component/TargetingComponent.h` | 0 | false | 3 | 482 | 4 advisory warnings only (includes, delegate teardown, timer header hint) |
| `Source/Project_MJS/Private/Character/Player/Component/SkillComponent.cpp` | 0 | false | 5 | 515 | Paired header + includes in scope; 40 deferred `CPP_DEFINITION_MISSING` from scoped headers, not blocking |

**Acceptance gate:** both targets exit 0 with `hasBlockingErrors=false` — **passed**.

Compared to pre-stabilization Project_MJS scoped writes (~349ms / ~318ms with blocking), normal Public/Private `.h`/`.cpp` edits no longer roll back on incomplete pair/index/class-map scope.

---

### B. 36-case live holdout (Qwen 3.5 9B)

| Field | Value |
|---|---|
| Date/time | 2026-07-11 ~09:05–09:32 KST |
| LM Studio model | `qwen3.5-9b-deepseek-v4-flash` |
| Sampling profile | `qwen3_5_9b_deepseek_v4_flash` |
| UE version | 5.8 |
| UBT | `C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe` |
| Config | `config/rag_eval_real_project_holdout_cases.local.json` (36 cases) |
| Wall-clock | ~26m 34s |
| Command | `python scripts/eval_pass_at_k.py --live --require-live --config ... --model qwen3.5-9b-deepseek-v4-flash --ubt-path ... --wrapper-timeout 1800 --artifact-dir data/baseline/live_holdout/20260711-090534-qwen35-9b` |

#### Headline scores

| Metric | This run (`20260711-090534-qwen35-9b`) | Prior 9B (`20260709-153021-qwen35-9b`) |
|---|---:|---:|
| **Pass@K** | **36/36 (100%)** | 35/36 (97.2%) |
| **Pass@1** | **35/36 (97.2%)** | 33/36 (91.7%) |
| Average attempts | 0.472 | — |
| Wrong-file edits | 0 | 0 |
| Build.cs false positives | 0 | 0 |
| No-op edits | 0 | 0 |

#### Tier breakdown (Pass@1 / Pass@K)

| Tier | Pass@1 | Pass@K |
|---|---:|---:|
| module_fix | 10/10 | 10/10 |
| multifile_refactor | 12/12 | 12/12 |
| uht_reflection | 4/4 | 4/4 |
| editor_runtime_boundary | 1/1 | 1/1 |
| single_file_compile_fix | 8/9 | 9/9 |

#### Pass@1 miss (Pass@K still passed)

| Case | Attempts | Notes |
|---|---:|---|
| `local_component_registration_missing_include` | 4 | Passed on retry; 3 validation-rejected / pre-apply no-op attempts before successful include patch |

#### Notable fixes vs prior 9B baseline

| Case | Prior run | This run |
|---|---|---|
| `local_lnk2019_missing_cpp_definition` | Failed Pass@1 | **Pass@1 (attempt 1)** |
| `local_component_registration_missing_include` | (contributed to 35/36 Pass@K gap) | Pass@K; Pass@1 miss only |

#### Attempt histogram

| Attempts | Cases |
|---:|---:|
| 0 (static autofix / deterministic path) | 22 |
| 1 | 13 |
| 4 | 1 |

### C. Automated regression (pre-live gate)

```powershell
pytest
node --check lmstudio-unreal-agent-mcp/src/server.js
```

| Check | Result |
|---|---|
| `pytest` | **695 passed**, 2 skipped |
| MCP `server.js` syntax | **pass** |

New/extended test coverage includes MJS-like Public/Private scoped write fixtures, bootstrap cache isolation, cpp preprocessor/raw-string parsing, and context-UX write payloads.

---

### Claim status

Internal UE 5.8 RAG/MCP/UBT workflow measurement only. Does not prove general model equivalence to Claude, GPT, or other Qwen releases.

---

## Korean

Scoped Write Stabilization 적용 후 실행한 **내부 검증** 결과입니다. 공개 벤치마크 주장이 아닙니다.

**관련 문서:** [Project Overview](Project_Overview.md) · [Model Measurement Results](Model_Measurement_Results.md) · [Version Performance History](Version_Performance_History.md) · [Evaluation Claim Guardrail](Evaluation_Claim_Guardrail.md)

| 계층 | 실행 내용 | 결과 |
|---|---|---|
| 단위/회귀 | `pytest` (695 passed, 2 skipped) + MCP `node --check` | **통과** |
| 실프로젝트 smoke | Project_MJS scoped write validator (read-only) | **통과** (2 targets) |
| Live holdout | LM Studio + UBT 36-case live | **Pass@K 36/36**, **Pass@1 35/36** |

### 변경 요약 (Scoped Write Stabilization)

scoped write가 Public/Private `.h`/`.cpp` 정상 편집을 불완전 pair/index/class-map scope 때문에 rollback하지 않도록 수정:

- **P0:** 모듈 상대 Public/Private pairing, scoped mode에서 full include index, write-mode include 검사, brace 기반 class header, paired `.cpp` 없으면 `cpp_definitions_missing` skip.
- **P1:** raw string 마스킹, preprocessor editor-safe region, timer/delegate/replication 정확도, UPROPERTY comment masking, denylist spaced member safe pattern.
- **P2:** bootstrap cache project/workspace 격리, evaluate-before-write, atomic save, write payload에 `scanMode`/`elapsedMs`.

### A. Project_MJS scoped write smoke

| Write target | exit | blocking | scoped files | elapsed |
|---|---:|---|---:|---:|
| `TargetingComponent.h` | 0 | 없음 | 3 | 482ms |
| `SkillComponent.cpp` | 0 | 없음 | 5 | 515ms |

정상 Public/Private `.h`/`.cpp` 편집이 scoped write rollback에 걸리지 않음 — **통과**.

### B. 36-case live holdout (Qwen 3.5 9B)

| 항목 | 값 |
|---|---|
| 모델 | `qwen3.5-9b-deepseek-v4-flash` |
| Profile | `qwen3_5_9b_deepseek_v4_flash` |
| 소요 | 약 26분 34초 |
| Artifact | `data/baseline/live_holdout/20260711-090534-qwen35-9b/` |

| Metric | 이번 | 이전 9B baseline |
|---|---:|---:|
| **Pass@K** | **36/36** | 35/36 |
| **Pass@1** | **35/36** | 33/36 |

Pass@1 유일 miss: `local_component_registration_missing_include` (4 attempts, Pass@K는 성공).

이전 baseline에서 실패했던 `local_lnk2019_missing_cpp_definition`은 이번 run에서 **Pass@1** 달성.

품질 카운터: wrong-file 0 · Build.cs false positive 0 · no-op 0.

#### Tier breakdown (Pass@1 / Pass@K)

| Tier | Pass@1 | Pass@K |
|---|---:|---:|
| module_fix | 10/10 | 10/10 |
| multifile_refactor | 12/12 | 12/12 |
| uht_reflection | 4/4 | 4/4 |
| editor_runtime_boundary | 1/1 | 1/1 |
| single_file_compile_fix | 8/9 | 9/9 |

#### Attempt histogram

| Attempts | Cases |
|---:|---:|
| 0 (static autofix / deterministic path) | 22 |
| 1 | 13 |
| 4 | 1 |

### C. 자동 회귀 (live 전 게이트)

| Check | Result |
|---|---|
| `pytest` | **695 passed**, 2 skipped |
| MCP `server.js` syntax | **pass** |

### Claim status

내부 UE 5.8 RAG/MCP/UBT workflow 측정입니다. Claude, GPT, 다른 Qwen 릴리스와의 일반 모델 동등성을 증명하지 않습니다.
