# Runtime policy correction — revision 115

원래 작업 기준 SHA: `3c4375fd3041da16b4e6af721c17fa2c230d2f50`.
이번 후속 작업의 HEAD: `868dedf8069c5b6d015794e32e5435ad540db219`.
소스 버전: `0.4.68`, 설치 revision: `115`. 커밋/푸시는 하지 않았다.
사용자가 별도로 변경한 UnitySymbolWorker DLL/EXE/PDB는 수정 대상과 diff hash에서 제외했다.

## 책임 매핑

| 기존 책임 | 현재 소유자 | 검증 |
|---|---|---|
| PredictionLoop의 압축/예산/복구 입력 후보 조립 | `round-input.ts::prepareRoundInput` | production handler 회귀 |
| 전체 checkpoint를 필수 floor로 측정 | `checkpoint-budget.js`의 필수 continuity capsule과 선택 항목 예산 | 65페이지 반복 및 40라운드 handler 회귀 |
| 후보의 최종 exact 측정과 Low-water 판정 | `ContextManager.enforceLowWater` | 비단조 후보, floor, 강제 압축, next-action fit |
| ContextManager의 의미 요약 호출과 실패 재시도 | `semantic-handoff.ts` | SDK 정상 userStopped 반환의 timeout 분류, 실행 단위 실패 cooldown |
| 일반 복구 조회에 audit pagination 제한 적용 | `RecoveryCoordinator.advance`의 명시적 bounded 입력 | 정상 40페이지, bounded 12회, 무진전 종료 |
| 압축 후 사라진 archive ID 탐색 | 기존 `evidence_first_read_context` 도구의 `action=catalog`, `EvidenceArchive.catalog` | 범위/예산/대화 scope/과거 자료 권한 |
| 완료한 SDK 채널에 늦은 abort 전달 | SDK 1.5.0의 missing finished flag 보정 | 실제 upstream act 함수로 적용 전후 비교 및 active cancel |
| 소스 SDK에만 적용되는 패치 | 통합 설치기의 설치 대상 `npm run patch:sdk` | 사용자 지정 LM Studio home 동기화 후 적용, 실패 시 활성화 금지 |
| phase·취소·dispatch·최종 전달 연결 | PredictionLoop + ExecutionState + ToolBoundary + DeliveryController | 기존 계약 회귀 유지 |

PredictionLoop는 1,178줄이다. 로그와 SDK controller 연결은 남지만 입력 정책 약 337줄을 약 34줄의 disposition 처리로 교체했다. ContextManager는 598줄이며 의미 요약 정책이 독립되었다. 단순한 줄 수 감소를 구조 검증의 대체 지표로 사용하지 않았다.

## 유지한 계약

- `toolCalls`는 입력 소비 완료로 인정하지만 최종 보고 완료로 인정하지 않는다.
- 반환 evidence는 취소/실패에서도 보존하며 미완료 prose는 planning commit에 넣지 않는다.
- 최신 미소비 결과는 exact 입력 검사 전에 누락 검사를 통과해야 한다.
- range/capability/exposure/dispatch 계측과 provider별 ToolBoundary를 보존했다.
- Git/File/Symbol/Log/Unity/Unreal 결과는 기존 공통 observation 및 archive 경로를 따른다.
- archive catalog는 현재 파일 관측이나 쓰기/빌드 승인으로 승격되지 않는다.
- 파일 페이지는 최종 JSON envelope 예산 안에서 완전한 줄 단위로 반환한다.
- 최종 전달과 복구 완료는 DeliveryController의 판단을 따른다. 진전 또는 `hasMore`는 완료의 증거가 아니다.

## 계산식과 측정 계약

`C`는 실제 모델 context length, `G`는 현재 예측에 전달할 generation reserve, `S`는 safety margin, `F`는 필수 capsule·최신 미소비 exchange·현재 template/schema를 포함한 exact floor다.

```
hardInputCeiling = max(0, C - S - G)
expectedResult = hasReadTools ? max(minimumReadResult, min(4096, floor(hardInputCeiling / 8))) : 0
postToolReserve = hasReadTools ? G : 0
HIGH = min(configuredTrigger, max(0, hardInputCeiling - expectedResult - postToolReserve))
configuredLOW = min(configuredTarget, floor(HIGH * 0.75))
effectiveLOW = max(configuredLOW, F)
nextActionFit = exactInput + postToolReserve + minimumReadResult <= hardInputCeiling
```

선택적 checkpoint 기록은 F에 포함하지 않는다. 필수 정보를 임의로 잘라 floor를 낮추지 않는다. configured LOW가 불가능하면 명시적 mandatory-floor exception으로 기록한다. 모든 압축 성공은 최종 조립된 exact input <= effective LOW 및 hard ceiling을 만족해야 한다. 26K/40K를 고정 threshold로 사용하지 않았다.

이번 기본 설정의 C=38,912, G=8,192, S=2,048에서 hard=28,672, expectedResult=3,584, postToolReserve=8,192, HIGH=16,896, configuredLOW=12,672이다. 실제 LOW는 도구 schema와 현재 필수 자료 때문에 더 높을 수 있다. 그 변동이 기록 누적으로 계속 상승하는지는 live에서 별도로 평가한다.

누적 380K는 첫 실제 압축 이후 완료된 실제 research/report 호출의 최종 exact input 합이다. 후보 tokenizer 호출, GUI 대화 크기, 별도 semantic summary 입력을 섞지 않는다. backend `promptTokensCount`와 exact input은 직접 비교한다. slot `n_tokens`, cached-prefix/prefill 토큰은 SDK에서 제공되지 않으면 unknown으로 남기며 prefill 시간(ms)을 토큰 수로 바꾸지 않는다.

## 설치와 검증

`UPDATE.bat` 최종 실행은 exit 0이다. Unity Bridge 바인딩, Unity/Unreal runtime 검사, npm ci/test/build, LMS 설치와 설치 대상 SDK 패치까지 실행했다. 실제 설치 SDK에 보정 코드가 존재함을 확인하고 GUI에서 Compactor/Unity MCP만 Force Restart했다. 모델을 unload/reload하지 않았다.

회귀 최종 결과: Compactor 355 pass; Unity 35 pass/4 skip; Unreal 247 pass/1 skip; Python 1,050 pass/19 skip. 명령과 exit code 및 중간 실패 원인은 `test-results.json`과 개별 로그에 보존했다. 설치 SDK 패치를 추가한 뒤 실패한 16개 Python fixture는 가짜 설치 트리에도 실제 패치 명령과 SDK 버전/대상 fixture를 제공해 해결했다. 실제 제품 검사에 우회 조건을 추가하지 않았다.

## 제한과 rollback

- 의미 요약 실패 시 deterministic continuity와 archive 경로로 이어지며 같은 실행에서는 실패한 요약을 반복하지 않는다. 의미 요약의 유효한 생성 자체를 보장하지 않는다.
- 최신 live receipt 등을 제거한 영속 후보가 pending evidence와 동일성을 증명하지 못하면 window commit은 거절될 수 있다. 실제 입력의 evidence guard를 우회하지 않으며 source 대화와 반환 자료를 보존한다.
- SDK 보정은 1.5.0에만 적용한다. 버전/대상이 바뀌면 자동으로 무시하지 않고 실패시킨다. 직접 LMS CLI만 사용한 재설치에는 README의 별도 patch 절차가 필요하다.
- 유한한 live 검증을 모든 모델/모든 업무/무한 실행의 증명으로 확장하지 않는다.
- rollback 기준은 위 HEAD와 `../runtime-contract-fix/implementation.patch`의 revision 114 수정 전후 snapshot이다. 최종 전체 소스 diff/hash는 이 폴더의 `implementation.patch`, `implementation-diff.json`이다. rollback 시 해당 소스로 복원 후 UPDATE.bat과 통합 재시작을 수행한다. 사용자 바이너리/프로젝트/대화 파일은 되돌리지 않는다.

최종 live 수치와 PASS/FAIL/BLOCKED 판정은 `HANDOFF.md`에 기록한다.
