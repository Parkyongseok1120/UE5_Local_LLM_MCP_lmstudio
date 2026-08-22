# Live Test 개선 계획

이 문서는 compile-fix, 코드 생성, 구조 파악, 아키텍처, 리팩터링, 런타임 디버깅을 서로 다른 증거 등급으로 측정한다. 여러 영역을 하나의 “종합 AI 점수”로 합치지 않는다.

현재 기준:

- 기존 36-case compile-fix 실행기는 유지한다.
- `docs/release_evidence/qwen3_6-27b_lmstudio_eval.json`의 100-call 오케스트레이션 결과는 `NOT RUN`이다. 0 값은 측정치가 아니다.
- Windows에서만 수행한 결과로 Linux/macOS 동작을 인증하지 않는다.
- 모델 재학습과 컴퓨터 간 자동 동기화는 범위 밖이다.

## 실행 단계

| 단계 | 실행 대상 | 사례 수 | 합격 증거 |
|---|---:|---:|---|
| 0. 결정론적 preflight | 전체 테스트, Node MCP, repo doctor, 인덱스 integrity, manifest, 패키지 | 전체 | 모든 필수 gate green |
| 1. Compile fix | 기존 real-project holdout | 36 | static validation + UBT green |
| 2. Architecture | ownership, dependency, cycle, plugin source, stale graph, data/state candidate, proposal gate | 12 | 요구 source evidence 존재 + 금지 claim 없음 |
| 3. Semantic refactor | rename, move, extract, API migration, callsite/test update | 12 | UBT green + semantic oracle |
| 4. Runtime debug | build-green/behavior-red 사례 | 8 이상 | before red → after green + PIE/log/automation evidence |
| 5. Negative control | do-not-edit, Build.cs 금지, generated/binary 경로, fake API, unsafe deletion | 12 | forbidden write 0 |
| 6. UX/orchestration | intent·도구·인자·복구·context·cache matrix | 모델당 100 calls | 아래 품질 gate 충족 |

Stage 0의 repository 검증 명령:

```powershell
python scripts/verify_release.py --repo-only --skip-lmstudio --skip-wrapper-dry
python -m pytest -q
Set-Location lmstudio-unreal-agent-mcp
npm test
```

설치된 Windows 환경의 검증 명령:

```powershell
python scripts/verify_release.py
.\scripts\run_live_holdout.ps1 -Model "<LM Studio model id>"
```

`run_live_holdout.ps1`은 이제 실제 로드된 모델 ID에서 sampling profile을 선택한다. 별도 지정이 필요한 경우에만 `-ModelProfile`을 사용한다.

## 모델 실행 행렬

| 모델 | 실행 범위 | 반복 |
|---|---|---:|
| Qwen3.6 27B | Stage 1~6 전체 | deterministic 1회, stochastic suite 3 seeds |
| Qwen3.5 9B | Stage 1, 2, 5, 6 우선; 실패 시 전체 진단 | deterministic 1회, stochastic suite 3 seeds |
| 외부 reference 모델 | 사용자가 명시적으로 제공한 경우만 | 동일 fixture·oracle로 1회 이상 |

모델 이름, resolved model ID, sampling profile, quantization, context length, seed, LM Studio 버전, commit SHA를 각 run artifact에 저장한다. 서로 다른 mode/tier/config는 성능 회귀 비교 대상이 아니다.

## Release gate

수치의 단일 소스는 `config/live_test_quality_gates.json`이다.

- Routing accuracy ≥ 98%
- Tool selection accuracy ≥ 97%
- Argument validity ≥ 99%
- Error recovery ≥ 95%
- Wrong-file edit, Build.cs false positive, forbidden patch, same-error repeat, no-op edit: 각각 0
- 동일 suite에서 Pass@1 하락 허용폭 3%p, Pass@K 1%p
- 평균 시도 횟수 증가 허용폭 0.5
- MCP benchmark 실패 수 증가 0
- architecture cache 재호출은 `graphSource=memory`; source 수정 후에는 반드시 `rebuilt`
- compact architecture 응답에서도 proposal/write gate와 cycle 정보가 보존됨

다섯 번 연속 case 실패, 인덱스 integrity 실패, 잘못된 model/profile 매핑, oracle 자체 오류, forbidden write 발생 시 실행을 중단한다. 중단된 실행은 점수로 발표하지 않고 진단 artifact로만 보존한다.

## 추가 구현 백로그

### P0 — 다음 Live TEST 전에 필수

- `architecture_live_12.json`과 source-evidence oracle 구현
- `semantic_refactor_live_12.json`과 semantic oracle 구현
- `negative_control_12.json` 구현
- 100-call orchestration recorder가 routing/tool/args/recovery를 실제 호출별로 저장하도록 구현
- 모든 artifact에 commit/model/profile/suite identity 기록

### P1 — 점수 범위 확장

- runtime before-red/after-green harness 구현
- architecture cache cold/warm/source-changed latency benchmark 추가
- compact/standard/full 응답의 context character budget 측정
- 27B/9B 결과를 같은 schema의 독립 scorecard로 생성

### P2 — 플랫폼 인증

- Linux: 설치, LM Studio 연결, 인덱싱, MCP smoke, package verification
- macOS: arm64 설치, LM Studio 연결, 인덱싱, MCP smoke, package verification
- 플랫폼별 Unreal build/runtime fixture가 없는 경우 “installer smoke only”로 표시
- Windows 결과를 Linux/macOS 인증으로 재사용하지 않음

## 산출물 구조

```text
data/baseline/live_holdout/<timestamp>/
  run_meta.json
  kpi.json
  eval.log
  progress.log
  cases/<case-id>/

Reports/eval/
  latest.json
  latest.md
  history/<timestamp>.json
  deltas/<timestamp>.json
```

각 scorecard는 `compile_fix`, `architecture`, `semantic_refactor`, `runtime_debug`, `negative_control`, `orchestration_ux`를 별도 섹션으로 유지한다.
