# 하네스 3차 — 발견 보존과 최종 보고 검토

실행별 리뷰 레지스터, 보고 검토 라운드 1회, 점진적 핀 해제를 구현했다. 작업 메모는 검증된 사실과 구분한다.

## 구현

- `review-register.ts`: 매 라운드 캡처된 assistant의 추론과 보이는 문장을 함께 읽고 식별자별 최신 found/cleared 상태를 보존한다. 근거 줄은 240자, 레지스터는 20행으로 제한한다. 상태 정규식은 `review-patterns.js`에서 엔진 검사와 공유한다.
- 런타임 레지스터는 증분 데이터 블록으로 주입하고 출력 에코 필터에 등록한다. 체크포인트의 `currentTurnFindings`에도 같은 행을 넘기며 기존 사용자 턴 키를 유지한다.
- 연구 중 found 행도 엔진 카탈로그와 대조한다. P등급이 없으면 unrated로 기록한다.
- 연구의 도구 없는 EOS 보고에서 누락 식별자 또는 엔진 계약 관련 미해결 항목이 있으면 도구 0개인 `report_review`를 한 번 실행한다. 기존 답변 뒤에 `## 검토 보완`만 이어 쓴다. 입력 공간이 부족하면 원래 답변을 보존하고 푸터로 남긴다. 검토 뒤에는 정정되지 않은 엔진 항목만 푸터에 남긴다.
- `reportReviewMode`는 on/off이며 기본 on이다. observe-only에는 적용하지 않는다.
- 핀을 유지한 입력이 high water를 넘으면 예산을 원래 값의 1/2, 1/4, 0 순서로 낮추고 매번 다시 측정한다. `pinnedWorkingSetFinalChars`는 최종 예산이며 `pinsReleased`는 0까지 낮춘 경우에만 true다.
- 새 런타임 파일을 배포 allowlist와 설치 상태 검사 목록에도 추가했다.

## 자동 검증

- 컴팩터 전체: **1,017 passed, 0 failed**.
- 포터블 패키지: 의존성 설치 후 실제 TypeScript 컴파일, 런타임 allowlist, 가이던스 패키지 검사 **3 passed**.
- 가이던스·Unreal/Unity 도구 계약·빌드 ID의 `--check`, 소스 배선 검사 및 `git diff --check` 확인.
- 로그 1792916809646의 r9/r10 원문을 fixture로 사용했다. 추론에만 있던 미사용 항목과 `TakeDamage` cleared 상태를 검사한다. Heal 행의 원문에는 HealthRegenRate/HealthRegenDelay가 함께 보존된다.
- 단일 보완 호출, 누락 없음, 설정 off, observe-only, 입력 공간 부족, 남은 계약 푸터, 다음 연구 입력의 사전 엔진 계약 주입을 검사했다.
- 반 예산에서 소스 하나가 남는 실제 프로젝션 경로와 장기 리플레이에서 추론 전용 레지스터 행이 최종 입력에 남는 것을 검사했다.

## 설치 및 실제 모델 실행

`UPDATE.bat --dry-run`과 실제 업데이트가 종료 코드 0으로 끝났다. MCP 및 LM Studio settings 파일 해시는 유지되었다. 소스·dist·설치 소스·설치 dist·production의 ID가 일치한다.

- 빌드: `ef-build-v2-68eb5ba25262e5aba2e64688d187221b44164fefd10517da62b2a158b37a0436`
- 백업: `C:/Users/sster/.evidence-first/backups/harness-review-register-20261025-183204`
- 진단 로그: `C:/Users/sster/.evidence-first/diagnostics/harness-review-register-20261025`
- 실제 실행: 대화 `1792916809646`의 새 응답 버전(index 1), execution `47fccccb-6d4b-47db-be4c-c1fc80d25d7a`
- 모델: `swift-1.5-qwen3.8-27b`, context 84,224, 원래 감사 요청 재생성
- `direct_runtime_identity.loadedMatchesInstalled=true`를 실제 로그에서 관찰했다.

## 실제 모델 최종 결과: 합격 아님

같은 프롬프트의 새 실행은 **63회 호출 뒤 truncated**로 종료했다. `reportDelivered=true`이지만 `reportCompleted=false`, `taskCompleted=false`다.

| 지표 | 기존 로그 | 이번 실행 |
|---|---:|---:|
| 모델 호출 | 27 | 63 |
| 도구 호출 | 51 | 87 |
| 보관본 읽기 호출 | 0 | 32 |
| 실행 시간 | 1,516.3초 | 2,315.9초 |
| 프리필 시간 | 577.3초 | 1,375.6초 |
| 프롬프트 토큰 | 1,218,262 | 3,158,235 |
| 압축 프로젝션 | 4 | 6 |

실제로 확인한 새 동작은 다음과 같다.

- 6개 프로젝션 이벤트 모두 `pinsReleased=false`였다. r26과 r50에서는 핀 예산 35,789자를 17,894자로 줄이는 분기가 사용되었다. 이 값은 핀 예산이며 실제 남은 본문의 문자 수는 아니다.
- r61의 레지스터 `ClientConfirmHit` found 행에서 `ue.net.client-rpc-callspace` 사전 경고가 만들어졌다.
- `report_review`는 **0회**였다. 연구 60회 상한 뒤 강제 보고 → raw tool text → 계획 복구 출력 한도 → 두 번째 강제 보고 출력 한도 순으로 진행되어, 계획에 지정된 정상 연구 EOS 보고 조건을 충족하지 못했다.
- 부분 보고에는 HeadshotMultiplier, HealthRegenRate/Delay, RunSpeed, ReloadSound가 없었다. ResetForRespawn과 TakeDamage는 있었으나, 완성된 보고의 누락·Tick 권고 검증으로 볼 수 없다.
- 부분 보고는 `AActor::TakeDamage`가 내장 `AActor::Health`를 차감한다는 잘못된 전제를 썼다. 설치된 UE 5.7 `Actor.cpp:3390–3454` 전체 함수는 피해 helper와 알림을 호출하고 ActualDamage를 반환하며 그런 내장 체력 차감은 하지 않는다. 프로젝트의 별도 Blueprint 이벤트 효과는 이번 작업의 조사 범위가 아니다.

한 차례의 실행에서 비용과 완료 상태가 악화되었다. 이를 특정 코드 변경만의 인과 효과로 단정할 수는 없으나, **3차 하네스를 실사용 품질 합격으로 판정할 수 없다**. 자동 테스트는 요청한 하네스 경로를 확인하며 실제 모델의 모든 결론이 옳다는 증거는 아니다. 레지스터 추출·누락 검사·계약 정정 감지는 정규식과 식별자에 따른 휴리스틱이다.

물리적 Escape 입력 뒤 컴퓨터 제어는 중단했다. 이후 UI를 조작하지 않고 로그만 읽었으며, 감사 자체는 이후 `max_predicted_tokens`로 종료했다. 원본 응답은 첫 번째 버전과 백업에 남아 있다.

