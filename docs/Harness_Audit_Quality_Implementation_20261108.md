# 감사 품질 개선 구현 기록 (2026-11-08)

[수정 실행 계획](Harness_Audit_Quality_Revised_Plan_20261107.md)의 9단계를 `claude/harness-compactor-contract-audit-4fb294`에 구현한 기록입니다. 기준은 `0b089c6`입니다. 병합과 배포는 아직 하지 않았습니다.

## 1. 커밋

| 단계 | 커밋 | 내용 |
|---|---|---|
| 문서 | `3f4d2cf` | 수정 계획, 근거 JSON, 계약·수명 전면 검토 |
| H 측정 | `c16a1c6` | 선택된 floor 입력의 역할별 문자 수(디버그 출력일 때만), 투영 호출별 절감량과 투영하지 않은 이유 |
| F2·F1 한도 | `948bea4` | `ExecutionState`가 도구 허용 prediction 수와 조사 종료 사실을 보유하고 라운드 진입에서 검사. 한도 후 `TOOL_REPLAN` 거부, 최종 raw tool 보정 1회, 남은 라운드 안내 1회 |
| H1 fact 수명 | `d4a7b33` | `RUNTIME_FACTS.clearOn`과 `RuntimeFactStore`. 해제·교체된 사실이 남은 data block을 입력 구성에서 다시 써서 제거(`fact_retired`) |
| A·E·T3 읽기 예산 | `5782c56` | 거부 코드 처분표, 실패 retry 최소값과 provider 메타데이터 비용 분리, 범위 없는 `read_file`의 전체 파일 예산 상향, provider 성공 이어 읽기 제안의 `byteBudget` 복제 제거 |
| B 목록 | `7bea168` | `directory-listing` 파서, archive 레코드별 본문 뷰 버전, 목록 페이지 정보 보존, 실행 단위 목록과 파일별 coverage |
| F3·H5·C | `5743c64` | 검토 후보 상태 정리, 부분 보고 후보 표시, 강제 최종 보고의 누락 검토, `DeliveryController` 1회 생성, 결함 없음 결론의 범위 판정 |
| G 분류 | `cee1c4d` | `찾을·찾기·찾는·찾으·문제점·위반·결함`을 리뷰 요청으로 분류 |
| D 엔진 사실 | `fbf97fd` | `ue.net.server-rpc-owning-connection`(revision 8), 감사 체크리스트, eval 사례 |

## 2. 계획과 달라진 구현 결정

- 조사 한도는 루프 맨 위의 `execution.researchBoundary()` 한 곳에서 검사합니다. 세는 단위는 모델을 실제로 도구와 함께 호출한 횟수입니다. 모델 호출 전에 복구로 바뀐 라운드는 세지 않습니다. `research_round_limit` 이벤트에는 `rounds`(이 단위), `phase`, `roundIndex`를 함께 기록합니다.
- 한도·시간으로 조사가 끝나면 실행 상태가 `READ_RECOVERY`·`TOOL_REPLAN`·`REASONING_RECOVERY`·`RESEARCH` 전이를 거부합니다. 정책이 실수로 재개방을 요청해도 상태 계층에서 막힙니다.
- 최종 보고 단계의 추가 시도는 `DeliveryController`가 사유별로 1회만 줍니다. 사유는 raw tool 보정과 보고 검토 두 가지이고, 재계획 횟수와는 분리했습니다.
- 한도로 끝난 최종 보고의 지시문을 bounded audit 문구와 분리했습니다. 이전에는 "사용자가 bounded audit를 선택했다"는 문구가 쓰였습니다.
- 결함 없음 결론의 범위 판정은 순환 import를 피하려고 독립 모듈 `report-coverage.ts`에 두었습니다. 모델 보완 지시와 최종 안내가 같은 결과를 씁니다.
- `prediction-loop.ts`는 111,768B(LF 기준, 상한 112,000B)입니다. 최종 지시 선택, 최종 상태 문구, 한도 판정, 반복 실패·정체 문구, raw intent 텔레메트리를 각 소유 모듈로 옮겨 공간을 마련했습니다.

## 3. 검증

| 검사 | 결과 |
|---|---|
| 플러그인 전체(`run-tests.cjs`) | 1358 통과, 실패 0 |
| 생성물 `--check` 4종 | runtime identity, design guidance, tool contract, unity tool contract 모두 일치 |
| Unreal agent MCP 전체 | 374 통과, 1 실패, 2 skip. 실패한 `runUnrealBuildFromPlan reports timedOut`은 이 셸에서 `ping`을 찾지 못해 생긴 것이고, PowerShell에서는 20/20 통과 |
| Python 계약·eval | `test_unreal_contracts.py`, `test_unreal_correctness_eval_cases.py` 51 통과 |
| 엔진 근거 대응(5.8→5.7) | 인용 51건(신규 4건 포함), 재검토 0 |
| Python installer·cross-language | 13건 실패. 같은 셸에서 `main` 기준선도 정확히 같은 13건이 실패해, 이번 변경과 무관한 환경 문제로 판단 |

## 4. 원본 기록 replay

고정된 대화 기록에 새 코드를 적용해 상태 변환만 확인했습니다. 새 모델 판단이나 이후 실행의 라운드 수를 예측한 것은 아닙니다.

| 기록 | 확인 항목 | 결과 |
|---|---|---|
| `1793984074550` | 감사 요청 분류 | `explain` → `review` |
| | 잘린 전체 파일 읽기 18건 | 18건 모두 크기 힌트가 있었고, 당시 라운드 capacity에서 15건이 전체 파일 크기로 상향됨 |
| | 압축 투영 후 목록 이름 | 201/201 보존(투영된 목록 2개도 16/16) |
| | 부분 보고의 검토 후보 | 0건 → 열린 후보 11건 표시 |
| `1793532481549` | 감사 요청 분류 | `review` 유지 |
| | 실행 단위 목록 | 52개 중 완독 24, 미열람 28 |
| | "P0/P1 결함 없음" 결론 | 범위 gap 생성(미열람 28개 명시) |
| | P3-2 "어떤 클라이언트든 … RPC" | 새 엔진 사실 gap 생성(테스트로 고정) |

## 5. 배포와 롤백 주의

- archive 목록 레코드에는 `bodyViewVersion: 2`가 저장됩니다. 이전 플러그인은 이 필드를 몰라 같은 레코드를 JSON entries로 렌더링합니다. 그래서 롤백하면 이번 버전이 만든 목록 readHandle의 offset이 맞지 않습니다. 롤백한 뒤에는 해당 대화의 목록 readHandle 이어 읽기를 신뢰하지 말고 목록을 다시 요청해야 합니다. 버전 필드가 없는 기존 레코드는 계속 v1로 읽힙니다.
- provider의 이어 읽기 제안에서 `byteBudget`이 빠졌으므로, 하네스 밖의 MCP 소비자는 provider 기본 예산으로 이어 읽습니다.

## 6. 남은 일

- 같은 모델·프롬프트·설정으로 두 감사를 다시 실행하는 실측. 확인할 값은 라운드 수, 재읽기, 목록 재호출, `floorComposition`, 보고 품질입니다.
- 원안의 "놓친 4건"을 당시 소스 기준의 결함·비결함 기준 사례로 만드는 일.
- 측정 결과를 본 뒤 provider 응답 필드 정리(T1)와 투영 최소 절감률(K2)을 결정.
