# Model Measurement Results

[English](#english) | [한국어](#korean)

## English

These results are internal UE 5.8 RAG/MCP/UBT workflow measurements. They are not public standardized model benchmarks and must not be described as general Claude, GPT, or Qwen model rankings.

### Latest 36-Case Live Holdout

| Model loaded in LM Studio | Profile | Artifact | Live time | Pass@K | Pass@1 | Notes |
|---|---|---|---:|---:|---:|---|
| `qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max` | `qwen3_6_27b` | `20260709-144441-pass1-target` | ~33m 37s | 36/36 | 36/36 | Community fine-tuned Qwen 3.6 27B local model; current v1.2.5 best run. `wrong_file_edits=0`, `build_cs_false_positives=0`, `no_op_edits=0`. |
| `qwen3.5` | `qwen3_5_9b` | `20260709-153021-qwen35-9b` | ~27m 22s | 35/36 | 33/36 | Strong compact result. Failed `local_lnk2019_missing_cpp_definition`; single-file compile-fix tier was the weak point. |

### Tier Breakdown

| Model | Module Fix | Multifile Refactor | Editor Runtime Boundary | Single-File Compile Fix | UHT / Reflection |
|---|---:|---:|---:|---:|---:|
| Qwen 3.6 27B | 10/10 Pass@1 | 12/12 Pass@1 | 1/1 Pass@1 | 9/9 Pass@1 | 4/4 Pass@1 |
| Qwen 3.5 9B | 10/10 Pass@1 | 12/12 Pass@1 | 1/1 Pass@1 | 6/9 Pass@1, 8/9 Pass@K | 4/4 Pass@1 |

### Notes

- The 27B result used a community fine-tuned Qwen 3.6 27B model loaded in LM Studio, not a base Qwen release.
- Qwen 3.6 27B community fine-tune is the primary measured profile for unattended live compile-fix validation.
- Qwen 3.5 9B is viable as a compact profile, especially for module fixes, editor-runtime guards, reflection, and deterministic/static-autofix paths.
- Qwen 3.5 9B's main gap in this run was JSON/patch discipline around a missing `.cpp` definition case.
- Average attempts must be read carefully. In this harness, static autofix successes can appear as `attempts=0`, so `avg_attempts=0.389` means many cases were solved before any LLM edit attempt. It does not mean the model used "less than one reasoning attempt" in a general benchmark sense.

## Korean

이 결과는 UE 5.8 RAG/MCP/UBT 워크플로 내부 측정입니다. 공개 표준 벤치마크가 아니며, Claude/GPT/Qwen의 일반 성능 순위로 해석하면 안 됩니다.

### 최신 36-case Live Holdout

| LM Studio 로드 모델 | Profile | Artifact | Live 시간 | Pass@K | Pass@1 | 비고 |
|---|---|---|---:|---:|---:|---|
| `qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max` | `qwen3_6_27b` | `20260709-144441-pass1-target` | 약 33분 37초 | 36/36 | 36/36 | LM Studio에 로드한 community fine-tuned Qwen 3.6 27B local model; 현재 v1.2.5 최고 결과. `wrong_file_edits=0`, `build_cs_false_positives=0`, `no_op_edits=0`. |
| `qwen3.5` | `qwen3_5_9b` | `20260709-153021-qwen35-9b` | 약 27분 22초 | 35/36 | 33/36 | compact 모델치고 강한 결과. `local_lnk2019_missing_cpp_definition` 실패; single-file compile-fix tier가 약점. |

### 세부 Tier 결과

| 모델 | Module Fix | Multifile Refactor | Editor Runtime Boundary | Single-File Compile Fix | UHT / Reflection |
|---|---:|---:|---:|---:|---:|
| Qwen 3.6 27B | 10/10 Pass@1 | 12/12 Pass@1 | 1/1 Pass@1 | 9/9 Pass@1 | 4/4 Pass@1 |
| Qwen 3.5 9B | 10/10 Pass@1 | 12/12 Pass@1 | 1/1 Pass@1 | 6/9 Pass@1, 8/9 Pass@K | 4/4 Pass@1 |

### 해석 주의

- 27B 결과는 base Qwen release가 아니라 LM Studio에 로드한 community fine-tuned Qwen 3.6 27B 모델을 사용했습니다.
- Qwen 3.6 27B community fine-tune은 현재 unattended live compile-fix 검증의 주 측정 profile입니다.
- Qwen 3.5 9B는 module fix, editor-runtime guard, reflection, deterministic/static-autofix 경로에서 충분히 실용적인 compact profile입니다.
- Qwen 3.5 9B의 이번 약점은 missing `.cpp` definition 케이스에서 JSON/patch discipline이 흔들린 점입니다.
- Average attempts는 조심해서 해석해야 합니다. 이 harness에서는 static autofix로 해결된 케이스가 `attempts=0`으로 기록될 수 있습니다. 따라서 `avg_attempts=0.389`는 많은 케이스가 LLM 편집 시도 전에 deterministic 경로로 해결됐다는 뜻이지, 일반 벤치마크에서 모델이 "0.389번만 생각했다"는 의미가 아닙니다.
