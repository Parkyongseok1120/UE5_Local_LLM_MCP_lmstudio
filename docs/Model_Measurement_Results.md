# Model Measurement Results

[English](#english) | [한국어](#korean)

## English

These results are internal UE 5.8 RAG/MCP/UBT workflow measurements. They are not public standardized model benchmarks and must not be described as general Claude, GPT, or Qwen model rankings.

The current product is **1.3.3**, with Direct Model Mode as the supported default. A v1.3.2 release-operator live E2E workflow with Qwen 3.8 27B completed active-project discovery, repeated RAG/search/read rounds over a real Unreal project, a large architecture report, and follow-up continuity without reproducing the prior mid-session JSON truncation. That functional run is not a paired scoring artifact. Every score on this page remains a historical v1.2.5 baseline until a new measurement artifact is published. Do not attribute these values to the Beta/RC control-plane work or to the stable Direct-mode, provenance, file-version, build-process, and context-continuity changes. No score on this page measures those effects.

**Qwen 3.8 27B is highly recommended** as the primary validated operating model for this stack. It does not yet have a paired numeric 1.3.3 score on this page; the current basis is the v1.3.2 qualitative live E2E result below. Qwen 3.5, the community fine-tuned Qwen 3.6 27B checkpoint, and GPT-OSS are not current recommendations; their saved values remain historical evidence only. Muse Glimmer is under testing and is not yet a validated recommendation.

## v1.3.2 Qwen 3.8 27B live E2E result

| Field | Observed result |
|---|---|
| Model | Qwen 3.8 27B in LM Studio |
| Project | Real Unreal project selected through `unreal_get_active_project` |
| Workload | Repeated RAG searches plus directory, file, range, and symbol reads over a large enemy-AI code surface |
| Synthesis | Completed an exact FSM/architecture report and continued into a follow-up turn |
| Recovery | Recovered from early invalid search-mode and model tool-call-generation attempts within the same session |
| Context continuity | PASS — the prior mid-session break and `Unterminated string in JSON` symptom did not recur |
| Recommendation | **Highly recommended** for the current Direct stack, subject to machine-specific quantization, context, and memory validation |

This is release-operator functional evidence, not a standardized benchmark or a substitute for a controlled paired scorecard.

The detailed 2026-07-11 post-stabilization write-up is retained only in the historical evaluation archive.

### Latest 36-Case Live Holdout

| Model loaded in LM Studio | Profile | Artifact | Live time | Pass@K | Pass@1 | Notes |
|---|---|---|---:|---:|---:|---|
| `qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max` | `qwen3_6_27b` | `20260709-144441-pass1-target` | ~33m 37s | 36/36 | 36/36 | Community fine-tuned Qwen 3.6 27B local model; best saved v1.2.5 run. `wrong_file_edits=0`, `build_cs_false_positives=0`, `no_op_edits=0`. |
| `qwen3.5-9b-deepseek-v4-flash` | `qwen3_5_9b_deepseek_v4_flash` | `20260711-090534-qwen35-9b` | ~26m 34s | 36/36 | 35/36 | Post scoped-write-stabilization compact run. Pass@1 miss: `local_component_registration_missing_include`. Recovered prior `local_lnk2019_missing_cpp_definition` failure. |
| `qwen3.5` | `qwen3_5_9b` | `20260709-153021-qwen35-9b` | ~27m 22s | 35/36 | 33/36 | Prior compact baseline. Failed `local_lnk2019_missing_cpp_definition`; single-file compile-fix tier was the weak point. |

### Tier Breakdown

| Model | Module Fix | Multifile Refactor | Editor Runtime Boundary | Single-File Compile Fix | UHT / Reflection |
|---|---:|---:|---:|---:|---:|
| Qwen 3.6 27B | 10/10 Pass@1 | 12/12 Pass@1 | 1/1 Pass@1 | 9/9 Pass@1 | 4/4 Pass@1 |
| Qwen 3.5 9B (20260711) | 10/10 Pass@1 | 12/12 Pass@1 | 1/1 Pass@1 | 8/9 Pass@1, 9/9 Pass@K | 4/4 Pass@1 |
| Qwen 3.5 9B (20260709) | 10/10 Pass@1 | 12/12 Pass@1 | 1/1 Pass@1 | 6/9 Pass@1, 8/9 Pass@K | 4/4 Pass@1 |

### Notes

- The 27B result used a community fine-tuned Qwen 3.6 27B model loaded in LM Studio, not a base Qwen release.
- The Qwen 3.6 27B community fine-tune was the primary measured profile for the historical unattended live compile-fix validation; that measurement status is not a current recommendation.
- The historical Qwen 3.5 9B runs showed that a compact profile could cover module fixes, editor-runtime guards, reflection, and deterministic/static-autofix paths, but Qwen 3.5 is not currently recommended.
- The historical Qwen 3.5 9B run (`20260711-090534-qwen35-9b`, deepseek-v4-flash profile) reached **36/36 Pass@K** and **35/36 Pass@1** after scoped write stabilization; its remaining Pass@1 gap was one include-registration case with validation-reject retries.
- Qwen 3.5 9B's prior historical gap (`local_lnk2019_missing_cpp_definition`) cleared in the 20260711 run.
- Average attempts must be read carefully. In this harness, static autofix successes can appear as `attempts=0`, so `avg_attempts=0.389` means many cases were solved before any LLM edit attempt. It does not mean the model used "less than one reasoning attempt" in a general benchmark sense.

## Korean

이 결과는 UE 5.8 RAG/MCP/UBT 워크플로 내부 측정입니다. 공개 표준 벤치마크가 아니며, Claude/GPT/Qwen의 일반 성능 순위로 해석하면 안 됩니다.

현재 제품은 Direct Model Mode를 기본 지원 경로로 사용하는 **1.3.3**입니다. v1.3.2 릴리스 운영자가 Qwen 3.8 27B로 실제 Unreal 프로젝트의 active-project 탐지, 반복 RAG/search/read, 대규모 architecture 보고서, 후속 대화 연속성을 확인했으며 이전의 중간 JSON truncation은 재현되지 않았습니다. 이 기능 E2E는 paired scoring artifact가 아닙니다. 새로운 측정 artifact가 공개되기 전까지 이 문서의 모든 점수는 historical v1.2.5 baseline입니다. 이 값을 Beta/RC control-plane 작업이나 stable Direct-mode, provenance, file-version, build-process, context-continuity 변경의 품질 향상 수치로 사용하면 안 됩니다. 이 문서의 점수는 해당 효과를 측정하지 않습니다.

**Qwen 3.8 27B를 이 stack의 현재 주 검증 operating model로 매우 추천합니다.** 아직 paired numeric 1.3.3 점수는 없으며 현재 근거는 아래의 v1.3.2 정성적 라이브 E2E 결과입니다. Qwen 3.5, community fine-tuned Qwen 3.6 27B checkpoint, GPT-OSS는 현재 추천 대상이 아니며 저장된 값은 historical evidence로만 유지합니다. Muse Glimmer는 테스트 중이며 아직 검증된 추천이 아닙니다.

## v1.3.2 Qwen 3.8 27B 라이브 E2E 결과

| 항목 | 확인 결과 |
|---|---|
| 모델 | LM Studio의 Qwen 3.8 27B |
| 프로젝트 | `unreal_get_active_project`로 선택한 실제 Unreal 프로젝트 |
| 작업량 | 대규모 적 AI 코드 영역에 대한 반복 RAG 검색과 directory/file/range/symbol 읽기 |
| 종합 결과 | 정확한 FSM·architecture 보고서를 완료하고 후속 턴까지 연속 진행 |
| 복구 | 초반의 잘못된 search mode와 모델 tool-call 생성 시도를 같은 세션에서 자체 복구 |
| Context continuity | PASS — 이전의 중간 단절과 `Unterminated string in JSON` 증상이 재발하지 않음 |
| 권고 | 실제 machine의 quantization·context·memory 검증을 전제로 현재 Direct stack에 **매우 추천** |

이는 릴리스 운영자의 기능 확인 근거이며 표준화된 benchmark나 통제된 paired scorecard를 대체하지 않습니다.

2026-07-11 stabilization 이후 상세 기록은 과거 평가 archive에만 보존합니다.

### 최신 36-case Live Holdout

| LM Studio 로드 모델 | Profile | Artifact | Live 시간 | Pass@K | Pass@1 | 비고 |
|---|---|---|---:|---:|---:|---|
| `qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max` | `qwen3_6_27b` | `20260709-144441-pass1-target` | 약 33분 37초 | 36/36 | 36/36 | LM Studio에 로드한 community fine-tuned Qwen 3.6 27B local model; 저장된 v1.2.5 최고 결과. `wrong_file_edits=0`, `build_cs_false_positives=0`, `no_op_edits=0`. |
| `qwen3.5-9b-deepseek-v4-flash` | `qwen3_5_9b_deepseek_v4_flash` | `20260711-090534-qwen35-9b` | 약 26분 34초 | 36/36 | 35/36 | scoped write stabilization 이후 compact run. Pass@1 miss: `local_component_registration_missing_include`. 이전 `local_lnk2019_missing_cpp_definition` 실패 회복. |
| `qwen3.5` | `qwen3_5_9b` | `20260709-153021-qwen35-9b` | 약 27분 22초 | 35/36 | 33/36 | 이전 compact baseline. `local_lnk2019_missing_cpp_definition` 실패; single-file compile-fix tier가 약점. |

### 세부 Tier 결과

| 모델 | Module Fix | Multifile Refactor | Editor Runtime Boundary | Single-File Compile Fix | UHT / Reflection |
|---|---:|---:|---:|---:|---:|
| Qwen 3.6 27B | 10/10 Pass@1 | 12/12 Pass@1 | 1/1 Pass@1 | 9/9 Pass@1 | 4/4 Pass@1 |
| Qwen 3.5 9B (20260711) | 10/10 Pass@1 | 12/12 Pass@1 | 1/1 Pass@1 | 8/9 Pass@1, 9/9 Pass@K | 4/4 Pass@1 |
| Qwen 3.5 9B (20260709) | 10/10 Pass@1 | 12/12 Pass@1 | 1/1 Pass@1 | 6/9 Pass@1, 8/9 Pass@K | 4/4 Pass@1 |

### 해석 주의

- 27B 결과는 base Qwen release가 아니라 LM Studio에 로드한 community fine-tuned Qwen 3.6 27B 모델을 사용했습니다.
- Qwen 3.6 27B community fine-tune은 historical unattended live compile-fix 검증의 주 측정 profile이었습니다. 이 측정상 지위는 현재 추천을 뜻하지 않습니다.
- Historical Qwen 3.5 9B run은 module fix, editor-runtime guard, reflection, deterministic/static-autofix 경로를 compact profile로 처리할 수 있음을 보였지만 Qwen 3.5는 현재 추천 대상이 아닙니다.
- Historical Qwen 3.5 9B run (`20260711-090534-qwen35-9b`, deepseek-v4-flash profile)은 scoped write stabilization 이후 **36/36 Pass@K**, **35/36 Pass@1**을 기록했습니다. 남은 Pass@1 gap은 include-registration 1건의 validation-reject retry였습니다.
- 이전 Qwen 3.5 9B 약점이었던 `local_lnk2019_missing_cpp_definition`은 20260711 historical run에서 회복됐습니다.
- Average attempts는 조심해서 해석해야 합니다. 이 harness에서는 static autofix로 해결된 케이스가 `attempts=0`으로 기록될 수 있습니다. 따라서 `avg_attempts=0.389`는 많은 케이스가 LLM 편집 시도 전에 deterministic 경로로 해결됐다는 뜻이지, 일반 벤치마크에서 모델이 "0.389번만 생각했다"는 의미가 아닙니다.
