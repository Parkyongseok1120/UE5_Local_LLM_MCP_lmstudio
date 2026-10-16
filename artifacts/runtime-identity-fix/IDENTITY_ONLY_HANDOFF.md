# modelInputId 중복 수정 및 단일 live 검증

## 판정

**PASS — ID 수정, 계약/수명 회귀, 사용자가 지정한 압축 이후 누적 390K live 목표.**

복구 카운터 초기화 후 재진입은 결정적 자동 회귀로 검증했다. 이번 live에서는 카운터 초기화 재진입이 발생하지 않았으므로 그 분기의 live 재현을 통과했다고 주장하지 않는다. 전체 답변 품질이나 다른 정책 문제의 완료 판정도 포함하지 않는다.

## 변경 범위와 책임

- 시작 HEAD / 종료 HEAD: `868dedf8069c5b6d015794e32e5435ad540db219`. 새 커밋 없음.
- 원래 전체 리팩토링 기준 SHA: `3c4375fd3041da16b4e6af721c17fa2c230d2f50`.
- 기존 recovery ID: `${executionId}:research-recovery-${recoveryCoordinator.toolRounds + 1}`.
- 수정 ID: `${executionId}:research-recovery-${roundIndex + 1}`.
- RecoveryCoordinator는 에피소드 진행 카운터를 계속 소유한다. PredictionLoop는 실행 전체의 ID와 실제 roundIndex를 소유한다. reset()은 유효하며 더 이상 모델 호출 식별자를 재사용시키지 않는다.
- 같은 호출의 측정 → 근거 노출 → 도구 causal trace → 완료 관측은 동일한 지역 상수 modelInputId를 사용한다.
- 취소하면 기존 실행과 도구 세션이 종료된다. 다음 실행은 별도 execution UUID를 사용한다.
- 런타임 모듈 추가/이동 없음. 새 전역 카운터, 영속 ID 저장소, 아카이브 마이그레이션 없음.
- 이번 후속 작업 시작 시 이미 존재하던 한 줄 수정을 유지하고, 수명 주석 및 회귀 검증을 보강했다. 이전 작업의 다른 변경은 보존했다. 추가 budget/compaction/recovery/final-delivery 정책 수정 없음.

## 자동 검증과 설치

| 명령 | 결과 | exit |
|---|---|---:|
| `npm run build` (plugin 디렉터리) | PASS | 0 |
| `node --test --test-name-pattern='recovery re-entry never\|canceled execution releases\|bounded audit makes zero final' test/prediction-loop.test.cjs` | 3 pass | 0 |
| `python install.py --profile custom --components context_compactor --yes` | 내부 `npm ci`, `npm test` 포함: 364 pass / 0 fail / 0 skip, 설치 완료 | 0 |
| `node scripts/status.cjs` (plugin 디렉터리) | source layout PASS | 0 |
| `python artifacts/runtime-identity-fix/check-identity-live.py REDACTED_HOME/.lmstudio/conversations/1790324308065.conversation.json` | 아래 live 수치 및 ID 대조 | 0 |

집중 3개는 전체 364개에 포함되며 합산하지 않는다. 원래 실패는 `red-input-identity.log`의 5 calls / 4 unique IDs로 보존했다. 강화한 재진입 회귀는 `[recovery-1, recovery-2, final-report-1, tool-planning-retry-1, recovery-5]`를 확인하며, 관측과 측정의 `(ID, roundIndex)` 대응 및 세션 해제를 검증한다. 별도 취소 회귀는 재호출 시 roundIndex=0이어도 executionId 및 modelInputId가 달라짐을 검증한다.

관측 검증기는 ID만으로 join한다. 이전 revision 116 실제 대화에 같은 검증기를 적용한 negative control에서 중복 recovery ID 12개를 검출했다. roundIndex를 join key에 추가해 충돌을 숨기지 않는다.

## 단일 live 결과

- GUI prompt: `GUI-IDENTITY-R117-ONCE` 1회. 추가 프롬프트 / 계속해 / 재실행 0회.
- 모델: `swift-qwen3.8-27b`, 실제 로드 contextLength **38,912**. 모델 교체/언로드 없음.
- 실제 runtime revision **117**, package **0.4.70**.
- 실행 ID: `e561357c-d31d-44aa-aedd-b793ab45aa33`.
- source fingerprint: `aeadc3300e630bbe4ee8662769cd480dc86c0049562f11ac5c059df89394719e`.
- dist fingerprint: `d15b31358907552ee3e738f530a83597baf816e450468be502fc972c51b9e09d`.
- 위 두 fingerprint는 작업 트리의 source/dist와 일치한다.
- 30번째 완료 호출 (roundIndex 29)에서 최초 압축 이후 **393,340 tokens**로 목표 390,000을 처음 통과했다.
- 로그 관측 및 GUI 중지 사이 두 호출이 추가 완료되어, 최종 확인 누적은 **433,480 tokens**. 정확히 390,000에서 중지한 것은 아니다.
- 압축 이전까지 포함한 전체 검증 입력 합계: **572,457 tokens**.
- 완료된 호출 **32개**, ID만으로 exact/backend 대조 **32/32 일치**.
- 취소된 마지막 호출 1개 포함, 측정 및 관측 ID **33개 모두 고유**.
- 취소 호출 `research-recovery-33`: `userStopped`, backend 사용량 미제공. 후보 exact input 15,339를 누적 사용량에 포함하지 않았다.
- causal trace **96개**, exposure **113개**의 호출 ID 연결 불일치 0.
- 도구 오류 **0개**. 이번 관측 구간 main.log 추가 내용 0 bytes; 새로운 unknown-channel 경고 관측 0.
- 실제 완료 확인 압축 **4회**: **15,388 / 15,343 / 15,362 / 15,375**, 매번 effective LOW **16,128** 이하.
- 취소된 33번째 호출의 준비 과정에서도 압축 후보가 있었으나 완료 압축 횟수에는 포함하지 않았다.
- 소스 read 결과 32페이지까지 진행. 사용량 목표에 따라 중지했으므로 전체 파일/최종 보고 완료 판정 없음.
- 종료: GUI Stop generating을 한 번 실행. `execution_transitions`는 `READ_RECOVERY → TERMINATED`, reason `canceled`; `lms ps --json`은 같은 모델이 로드된 채 `idle`, queued=0임을 확인.

누적 수치는 완료된 실제 호출의 exact templated input을 합산한다. backend promptTokensCount로 각 값을 대조한다. 반복 입력된 문맥도 합산되며 동시 context 점유량이나 새로 읽은 고유 데이터 총량이 아니다. prefill 시간 및 cache 토큰을 이 수치와 혼합하지 않는다.

## 산출물

- `test-identity-lifecycle.log`, `build-identity-only.log`, `install-identity-only.log`
- `IDENTITY_ONLY_CONTRACT.md`: 소유권/수명/변경 경계
- `identity-only-live/run.json`: 실행 환경 및 시작 기록
- `identity-only-live/verification.json`, `events.json`: 종료 후 대조 결과와 원시 debug events
- `identity-only-live/milestone-390k.json`: 목표 도달을 관측한 시점의 snapshot
- `identity-only-live/stop.json`: 첫 도달 호출, 실제 종료량, 모델 idle 확인
- `identity-only-negative-control/verification.json`: 이전 live 충돌 검출 결과
- `identity-only-patch.json`, `model-input-identity.patch`: 좁은 런타임 수정과 해시

## 남은 runtime risk와 rollback

이번 live에는 recovery reset 재진입이 없었다. 해당 결함 분기는 자동 회귀에서 red→green이며, runtime에서는 연속 복구 호출, 반복 압축, 취소 종료의 ID 연결을 확인했다. 목표에 따라 중지했으므로 최종 보고 품질·전체 초반 기억 회수·다른 도구 예산 오류는 이 결과로 판정하지 않는다.

Rollback은 위 한 줄 ID 표현식만 이전 카운터 방식으로 되돌리는 것이다. 이는 알려진 중복 결함을 다시 만들므로 권장하지 않는다. 이전 작업과 사용자 수정이 섞인 전체 working tree를 reset하지 않는다. 전체 작업의 시작 SHA와 이번 follow-up HEAD는 서로 다른 범위의 기준점이다.
