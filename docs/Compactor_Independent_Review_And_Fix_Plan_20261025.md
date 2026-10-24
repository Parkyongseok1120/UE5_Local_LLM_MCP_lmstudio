# Context Compactor 독립 검토 및 수정 계획

> 이 문서는 0acefab 기준의 승인 전 검토·계획 기록이다. 승인 후 실제 수정·검사·설치 결과는 [수정 및 업데이트 기록](Compactor_Review_Fixes_And_Update_20261025.md)에 별도로 남겼다. 아래의 미실행 표시는 계획 작성 시점의 상태다.

작성일: 2026-10-25, Asia/Seoul. **계획 문서이며 제품 코드 수정·설치·커밋은 수행하지 않았다.**

추가 요청 반영: 일반 채팅에 나타난 `Runtime read-result budget facts`의 출력 경로를 F6으로 확인했다. 후속 구현·검증을 마친 뒤 **에이전트가 저장소의 `UPDATE.bat`를 직접 실행하고 설치 결과를 확인하는 것까지** 작업 범위에 포함한다. 이 계획 수정 단계에서 배치 파일을 실행한 것은 아니다. 저장소에는 `업데이트.bat`라는 이름 대신 `UPDATE.bat`가 있다.

- 검토 기준: `0acefab991acc32e7e56cabe223bc9c25cfcaf4d`
- 비교 기준: `3cec9733fd2847106594b5ad29b0ee47361ff4e2`
- 기존 구현 설명: [읽기 원장 구현 기록](Harness_Read_Ledger_Reread_Loop_20261025.md)
- 새 실제 실행: `1792862138948.conversation.json`, assistant message 3
- 이전 실제 실행: `1792842246501.conversation.json`, assistant message 3

## 1. 전체 판정

**부분 해결. 동일 본문 반복 억제와 보고서 생성은 새 실제 실행에서 확인했다. 정상 감사 능력 보존과 검증 상태의 신뢰성까지 해결됐다고 판단할 수는 없다.** 새 실제 실행 관찰은 있으나, 이 검토에서 통제된 A/B 실행이나 추가 실모델 실행을 수행하지 않았다.

새 실행은 2026-10-25 **02:36:27 KST**에 `eosFound`로 종료했다. `execution_outcome`은 `reportPresent/reportDelivered/reportCompleted=true`, `taskCompleted=false`, `objectiveSatisfied=null`이다. 보고서 전달 완료를 감사 목표 달성으로 바꾸어 해석하지 않는다.

로드된 빌드 ID는 `ef-build-v2-fa5375e8fb49c3bf8db81ac0dfbc958be1d4f5461cddbabad0afbb62bc5c87d0`이며, 로그의 installed ID 및 기준 커밋의 생성 ID와 일치한다. 이전 실행의 ID는 `ef-build-v2-e6f60aaf0aebd96f675209e1b3717d9e52d88ab81a1074587dbad10711a9c886`이다.

새 로그 snapshot SHA-256: `0c1fe780f3b6acbe258ed660f6bc824e7ac99337df77ea92a37b0ded49ab46da`.
이전 로그 snapshot SHA-256: `f32ea16fae4a2f3eff55da66abda46bafd4db4e0e4e21bc9c6192dbc2ebfa0b0`.
검토 시 원본 최신 파일과 보관 snapshot의 byte 일치도 확인했다. 전체 대화와 소스 본문은 저장소에 추가하지 않았다.

## 2. 실제 실행에서 관찰한 변화

| 항목 | 이전 `3cec973` 실행 | 새 `0acefab` 실행 |
|---|---:|---:|
| 라운드 | 51 | 16 |
| 도구 호출 | 72 | 31 |
| `read_file` 호출 | 49 | 16 |
| 같은 버전 소스 본문 재전달 | 33회 | 0회; 원본 재읽기 1회는 원장이 억제 |
| archive 본문 재전달 | 14회 | 4회 |
| 중복으로 분류한 전달 문자 수 | 212,460 | 28,329 |
| 중복 전달 토큰 추정 | 60,703 | 8,094 |
| 라운드별 prompt token 합계 | 2,131,765 | 623,316 |
| predicted token 합계 | 49,268 | 32,241 |
| reasoning token 집계 | 24,865 | 13,985 |
| `changed=true` working-context projection | 8 | 2 |
| 별도 semantic summary/compaction 이벤트 | 확인되지 않음 | 확인되지 않음 |
| prefill 집계 | 648.6초 | 334.4초 |
| generation 집계 | 1,024.0초 | 754.2초 |
| `executionElapsedMs` 기반 시간 | 1,715.4초 | 1,099.6초 |
| archive version 혼동 오류 | 1회 | 0회 |
| 유효하지 않은/지어낸 handle | 1회 | 1회 |
| 예산 guard 거부 | r24 1회 | 0회 |
| 최종 보고 | 사용자 중단, 없음 | 전달 완료 |

토큰 합계는 라운드별 prompt/predicted 계수로, 캐시를 제외한 실제 연산량이나 고유 입력량이 아니다. 중복 토큰은 문자 기반 추정이다. archive 재전달에는 현재 입력에서 필요한 본문을 복구하는 정상 동작도 포함되므로 전부 제거 대상으로 삼지 않는다. 두 실행의 `working_context_projection`을 semantic 요약 호출로 세지 않는다.

두 실행의 사용자 감사 프롬프트 SHA-256은 `44fb1a236b866ae96f3d36a793fa5c8428eda981bb565b80fce29306f1abe93a`로 같다. 모델은 `swift-1.5-qwen3.8-27b`, 동일 GGUF와 load config이며 per-chat prediction config도 같다. 양쪽에서 관찰된 공통 프로젝트 소스 15개는 SHA가 모두 같다. 다만 전체 프로젝트/BP/생성 파일의 고정 snapshot을 확보해 통제한 실험은 아니며, tool registry도 변경됐다. 따라서 개선 폭을 일반 성능 보장으로 사용하지 않는다.

### 새 실행의 핵심 순서

1. r2–r6에 프로젝트 소스 15개를 읽었다.
2. r7에 첫 projection이 발생했다. 모델은 원문이 압축됐음을 알고 header와 cpp를 Archive로 복구했다.
3. r7–r9의 archive 읽기 3회가 `newDeliveries=0`으로 처리되어 r9에 첫 `report_now`가 발생했다. cpp의 두 페이지는 서로 다른 문자 offset의 연속 복구였다.
4. r10의 모델이 잘못 만든 handle은 `invalid_read_handle`을 반환했다. 이는 다른 record의 version을 섞은 오류와 다르다. 모델은 이를 “이전 실행 handle이 만료됐다”고 설명했지만 로그는 그 추론을 증명하지 않는다.
5. header 전체 재읽기 1회는 원장이 억제했고, 필요한 범위는 Archive에서 복구했다. 별도 engine lookup 및 `NetDriver.cpp` 읽기로 조사가 계속됐다. 두 번째 `report_now` 강제 종료는 이번 실행에서 발생하지 않았다.
6. r14에 evidence packet을 제출했고 r15에 보고서를 생성했다.

### 보고서 생성과 정확도는 구별해야 한다

최종 보고서는 `ClientConfirmHit()`을 일반적인 C++ 호출 형태로 호출하면 Client RPC가 소유 클라이언트에 도달하지 않는다고 P1으로 단정했다. 반환된 엔진 본문은 `NetDriver.cpp` 일부이며, 상위 호출 공간/소유 연결 결정 경로는 읽지 않았다. 하네스도 `ue.net.client-rpc-callspace`의 근거 누락을 r14·r15에 기록하고 마지막에 경고를 덧붙였다.

검토자가 별도로 읽은 설치 UE 5.7.4의 `Actor.cpp:5522–5610`에서는 서버의 Client RPC가 소유 연결을 확인한 뒤 조건에 따라 `FunctionCallspace::Remote`를 반환한다. `GetNetConnection`은 `Actor.cpp:1930`에서 Owner를 통해 연결을 얻는다. 따라서 “호출문에 연결 인수를 쓰지 않았으므로 Client RPC가 절대로 전송되지 않는다”는 일반 근거는 성립하지 않는다. 프로젝트의 PIE 동작이나 소유 연결 상태 자체를 이 검토에서 실행 검증하지는 않았다.

이 사례는 packet의 형식 통과나 `SourceVerified` 자기 선언을 ledger의 검증 완료로 승격하면 안 되는 실제 이유다. 기존 engine claim 경고는 동작했지만, 이미 작성한 P1 본문을 보류시키거나 모델이 추가 조사하도록 강제하는 동작까지 입증하지는 않는다. packet 검증 도구의 응답 역시 `factualEvidenceVerified=false`, `wholeTaskVerified=false`라고 명시한다.

## 3. 호출 경로와 정상 동작

주 경로는 `prediction-loop → screenReturnedReads → captureReturned → recordReturnedReads → working-context 저장/복원 → readLedgerFact → 다음 model input`이다. 사용자에게 남기는 원래 도구 응답은 visible transcript에 보존하고, 중복 억제는 모델 작업 입력에 적용한다.

- `read-ledger.js`는 path+SHA별 전달 범위를 합친다. 처음 보는 SHA, 미전달 범위, 첫 집중 범위 읽기는 본문을 유지한다.
- `working-context.js:945`가 `readLedger.toJSON()`을 manifest에 넣고, `:1052`에서 scope/prefix/archive 참조 및 무결성 확인 후 load한다. 정상 restore가 원장을 비우는 별도 결함은 재현하지 못했다.
- `compaction-tool-memory.js`의 `projectedReadObservation`은 projection된 원문의 관찰 경로·SHA·범위를 checkpoint에 남긴다. 이 정보는 현재 본문 가시성이나 의미 검증을 증명하지 않는다.
- `evidence-archive.js:571`은 archive record digest와 source SHA를 구분하고, 다른 record의 version 소유자와 올바른 locator를 설명한다. 잘못된 version이 파일 변경 증거가 아니라고 안내하는 기존 구현은 유지한다.
- `budget-broker.ts`는 배치의 held/consumed 용량과 응답 전체 envelope 예산을 관리한다. known result size는 예약 추정이며 새 파일 내용의 상한 보장이 아니다. 이번 로그에는 예산 거부가 없으므로 이를 새 실행의 반복 원인으로 단정하지 않는다.
- `execution-config.ts`는 기존 설정을 읽고 기본 `noNewEvidenceRounds`는 3이다. 새 설정 항목을 추가하기보다 현재 진행 판단을 고친다.

이전 로그 r26의 “version mismatch라 파일이 바뀌었을 수 있다”는 모델 추측에는 `expectedHash_edit_ok`도 잘못 사용됐다. `continuity-file-observations.js:34–45`에서 이 값은 관찰된 hash를 사용해 편집 조건을 검사할 수 있다는 의미이며, 편집이 발생했다는 기록이 아니다. 새로운 로그에서는 동일한 version 혼동이 재현되지 않았다. 필드 전체를 재설계할 근거로 확대하지 않는다.

## 4. 재현한 결함과 최소 수정 계획

### F1. P1 — 제출/형식 검사한 claim이 검증 완료로 기록됨

근거: `quote-anchoring.ts:229`, `prediction-loop.ts:1118–1126`, `reference-context.ts:674–684`, `evidence-manager.ts:311–315`, `read-ledger.js:205–217,321–342`.

`validatePackets()`는 validation 응답이 아니라 요청 packet을 추출한다. `recordChecks`는 인용/엔진 근거 검사보다 먼저 실행된다. parser는 literal `Proposed`만 제외하므로 누락·잘못된 proofLevel도 통과한다. `recordCheck`는 인용 줄 범위를 버리고 최신 path의 SHA에 기록한다.

반례: 1–2줄만 읽고 99줄을 인용한 packet, validator `ok=false`, proofLevel 누락/잘못된 값인데도 `verified: behavior`가 표시되고 Remaining에서 빠진다. 이전 SHA에 근거한 packet을 다시 제출하면 새 SHA에 붙는다. 완전히 미관찰된 경로나 literal Proposed는 제외되는 반대 근거도 있다.

수정 계획:

1. 제출된 claim, packet 구조 수락, 인용/관찰 결합, 의미 검증을 구분한다. 형식 통과만으로 사실 검증 상태를 만들지 않는다.
2. 기록 진입점을 validator request/result 결합 및 근거 검사 이후로 옮긴다. 단순 `ok=true` 검사만으로 해결했다고 보지 않는다.
3. check는 실제 사용한 관찰의 project/path/SHA/range/claim provenance에 묶는다. 파서의 허용 proofLevel·반환 범위 검사를 보강한다. 관찰 연결을 확정할 수 없으면 미확인으로 둔다.
4. `verified`와 파일 단위 Remaining 제거를 현재 근거가 보장하는 수준으로 낮춘다. claimType 하나가 기록됐다고 파일 전체 감사 완료로 보지 않는다.
5. 재검토 결과가 이전과 같은 문장이라는 이유만으로 거부하지 않는다. 정상적인 재검증을 허용하고 관찰 연결을 확인한다.

### F2. P1 — 정상 복구·집중 검토·실패한 읽기가 정체 및 강제 종료로 처리됨

근거: `evidence-manager.ts:195–206,252–256`, `prediction-loop.ts:1157–1175,1474`, `resume-facts.ts:83–88`.

종료 조건은 `newDeliveries===0 && otherResults===0`다. `failedReads`, 현재 입력에 복구한 본문, 새로운 집중 검토 범위를 구분하지 않는다. 두 번째 trigger는 도구 없는 최종 보고로 바뀐다.

반례 1: 전체 60줄 이후 서로 다른 함수 범위 9개를 검토하려 하면 같은 본문 중복 판정이 0개인데도 r3/r6에 경고하고 모델 8번째 호출에서 도구를 제거한다. 계획된 자연 종료는 11번째 호출이다. 반례 2: 서로 다른 새 파일의 읽기가 예산 오류로 실패한 것만으로도 같은 강제 종료가 발생한다. 이때 “files are unchanged”라는 runtime fact는 실패한 파일의 현재 상태를 증명하지 못한다.

수정 계획:

1. 기존 recovery progress의 `restoredWorkingEvidenceUnits`를 재사용해 새 source coverage, 필요한 현재 입력 복구, 동일 범위 재전달, 실패한 읽기를 구분한다.
2. 필요한 집중 범위 확보, 근거에 연결된 검토 항목 변화, 근거 공백을 줄인 검증 결과를 진행으로 다룬다. 매 라운드 reasoning 문장이 달라졌다는 것만으로 진행을 인정하지 않는다.
3. 미해결 필수 근거나 가능한 정상 복구가 남았으면 `report_now`는 정리 안내로 사용한다. 현재 근거가 가용한데 같은 요청/범위만 실질적으로 반복하는 경우에 제한적으로 강제 수렴한다.
4. 예산/locator 오류는 실패 원인과 축소 재시도·복구 경로를 남기고, 기존 repeated-failure guard로 무한 반복을 제한한다. 이를 검토 완료와 합치지 않는다.
5. unchanged는 “마지막 관찰 SHA와 같다”로 한정한다. 새 관찰이 실패한 파일은 현재 상태 미확인으로 둔다.

정책 위험: `reportNowTriggers`는 실행 전체에서 누적되고 실제 새 파일을 읽어도 reset되지 않는다. 이는 현재 UI/주석에 명시된 설계이므로 별도 버그라고 단정하지 않는다. 다만 이번 정상 감사 보존 요구에 맞춰 실제 진행 시 정체 episode를 끊도록 변경할 것을 제안한다. 비읽기 라운드가 끼어도 현재 counter는 자동 초기화되지 않으므로 “연속” 정의도 함께 명시한다. 전체 실행 제한은 기존 round/time 정책으로 유지한다.

### F3. P2 — 넓은 coverage와 단일 Archive locator의 범위가 어긋남

근거: `read-ledger.js:163–185`, `evidence-manager.ts:238–244,285–294`, `working-context.js:515`.

전체 1–100줄을 읽은 뒤 50–52줄을 집중 읽으면 같은 ledger entry의 유일한 archiveRef가 3줄 record로 교체된다. 다음 전체 읽기는 complete/UNCHANGED로 억제되지만 제공 handle로 1–5줄을 요청하면 `line_range_outside_body`, available `[50,52]`다. 이전 전체 record는 남아 있고 직접 MCP로 복구할 수도 있으므로 소스 유실로 보고하지 않는다.

수정 계획: locator를 실제 포함 범위에 맞춰 선택한다. 전체 record가 유효하면 좁은 record로 덮어쓰지 않는다. 분리된 partial 범위를 합친 coverage에는 이를 실제 복구할 수 있는 record를 선택하고, 충분한 locator가 없으면 기존처럼 원래 읽기 본문을 유지한다. 모든 Archive 저장 구조를 새로 만들지 않는다.

### F4. P1 — SHA A → B → A 복귀 시 최신 관찰이 B로 남음

근거: `read-ledger.js:123–136,180–185`, `evidence-manager.ts:233–244`, `working-context.js:791–798`.

역사적 A entry가 이미 있으면 실제 A 복귀도 redundant로 분류한다. 억제 경로가 sequence/latest 관찰을 갱신하지 않아 `latestFor()`는 B와 B의 checks를 최신으로 보여 준다. 이전 A Archive가 없어 fallback record를 수행하면 우연히 정상 갱신되는 반대 경로도 있다.

수정 계획: 이미 전달한 버전의 coverage와 마지막으로 실제 관찰한 버전을 구분한다. 과거 SHA로 되돌아가도 최신 관찰을 갱신하고 변경 전이를 알린다. cached coverage가 있어도 B→A를 unchanged 전이로 설명하지 않는다. fresh observation 없이 과거 checks를 현재 검증으로 승격하지 않는다.

### F5. P2 — 첫 집중 읽기와 같은 집중 범위의 반복이 구별되지 않음

근거: `read-ledger.js:128–136`, `evidence-manager.ts:250–256`.

전체 1–100 뒤 50–52를 여러 번 읽어도 모두 “전체보다 좁다”는 이유로 narrower가 된다. 합쳐진 coverage와만 비교하므로 집중 범위의 exact repeat는 계속 본문을 전달한다. 반대로 모든 narrow read를 막으면 정상 감사가 손상된다.

수정 계획: 기존 전달/현재 가용성 정보를 사용해 첫 필요 범위 복구와 현재 보이는 동일 집중 범위 반복을 구분한다. 현재 본문이 다시 projection됐거나 검토에 필요한 재확인이라면 읽기를 유지한다. 새로운 사용자 입력·새 SHA·새 범위·필요한 근거 복구를 일괄 차단하지 않는다. F2의 종료 조건으로 문제를 가리는 방식은 사용하지 않는다.

### F6. P2 — 내부 읽기 예산 안내가 일반 채팅 답변으로 재출력됨

사용자가 붙인 JSON 배열과 같은 내용이 새 실제 실행의 step 97에 있다. step 69·82·108·119에서도 같은 계열 문구가 일반 content block으로 관찰된다. 이 블록들은 thinking style이 없다. `denial:null`은 읽기 거부가 없었다는 뜻으로, 이 문구 자체가 오류나 실패 보고는 아니다.

입력부터 출력까지의 경로:

1. `budget-broker.ts:225`의 `readBudgetFeedback`이 예산 조정·응답 크기와 페이지 처리 지침을 만든다.
2. `prediction-loop.ts:864–866`이 이를 `runtimeFacts`에 보관하고 다음 라운드 입력에 넘긴다. `showDebugInfo` 전용 로그가 아니므로 디버그 표시 옵션을 끄는 것만으로 해결되지 않는다.
3. `context-manager.ts:216–303`의 `composeModelHistory`는 프롬프트 앞부분 재작성을 줄이기 위해 이 자료를 일반 `assistant` 데이터 슬롯에 배치한다. 모델의 reasoning 메시지가 아니다.
4. 모델이 이 안내를 일반 응답으로 출력하면 `prediction-stream.ts`가 해당 일반 출력 분기를 통해 채팅에 표시한다. 하네스가 budget 문자열을 직접 화면에 append하는 별도 경로는 확인하지 못했다.
5. `privateDataParts` 선별(`context-manager.ts:301`)과 `InternalDataOutputFilter`(`internal-data-output.ts:15–19,72`)는 Available tool-call refs 및 Derived reference data만 처리한다. budget 안내는 선별에서도 빠지고 필터 문법에도 없다. 선별 목록에 추가하는 것만으로는 출력이 숨겨지지 않는 것을 반례로 확인했다.

추가 재현: 별도 진단 폴더의 `budget-echo-probe.cjs`가 기준 커밋의 composer와 출력 필터를 호출했다. budget fact가 assistant 입력에 포함됨, privateDataParts에서 빠짐, 필터를 그대로 통과함, privateDataParts에 직접 넣어도 통과함을 각각 확인했다. 일반 채팅에 나온 사실은 실제 로그, 필터 누락은 코드·재현으로 구분해 입증했다.

수정 계획:

- 필요한 예산 안내는 모델 입력에 유지한다. 가변 데이터 슬롯과 입력 측정·prefix cache 정책도 보존한다.
- 이번 실행이 실제 주입한 내부 budget 프로토콜을 출력 필터가 식별할 수 있게 연결한다. 관련 ledger/projection 안내도 같은 노출 경계를 점검하되, 입증되지 않은 출력까지 무작정 제거하지 않는다.
- 스트리밍 조각 처리, finalized message/fallback, durable history 변환에서 같은 경계를 적용한다. 일반 답변 뒤에 붙은 내부 안내만 제거하고 앞뒤의 실제 설명·도구 호출은 유지한다.
- 이런 내부 안내만 출력된 라운드를 최종 보고서나 감사 진행 근거로 취급하지 않는다. 원래 도구 응답과 진단 로그는 유지한다.
- 단순히 `Runtime`으로 시작하는 문장을 전부 지우거나, 일반 로그를 thinking으로 위장하지 않는다. 사용자가 해당 문구를 인용·설명해 달라고 한 경우와 코드 블록/일반 JSON은 보존한다. 프로토콜의 출처·형식이 불확실하면 임의로 사용자 내용을 삭제하지 않는다.

추가 회귀: 실제 배열 형태의 재출력, 여러 budget 항목, fragment 경계가 문자열/JSON 중간에 끊기는 경우, 취소 중 부분 출력, reasoning/일반 text 혼합, streaming이 없는 fallback, 내부 안내 뒤의 정상 최종 보고, budget 안내만 있는 가짜 보고, 사용자의 인용·설명 요청을 포함한다. 모델 입력에는 안내가 남고 화면/일반 대화 재료에는 누출되지 않는지 따로 검사한다.

## 5. 회귀 검증 결과와 편입 계획

기준 커밋을 별도 진단 폴더에 `git archive`로 보관해 실행했다. 작업 중인 원본 checkout의 생성물과 설정을 바꾸지 않았다.

- 기존 TypeScript `tsc -p tsconfig.json`: 통과.
- 기존 plugin 전체 테스트: **949 통과, 실패 0**.
- 기존 분석/trace Python 테스트: **24 통과, 실패 0**.
- 독립 임시 반례: 두 Node test 파일, **16개 중 정상 대조군 6개 통과, 기대 동작 10개 실패**. 실패는 여러 입력 변형으로 위 결함/정책 위험을 재현한 것이며 서로 다른 버그가 10개라는 뜻은 아니다.
- F6 추가 직접 재현: `budget-echo-probe.cjs`의 4개 단언으로 현 출력 경계 누락을 확인했다. 기존 16개 Node test 집계에는 합산하지 않는다.
- 제품 테스트 파일 추가 및 제품 코드 수정: **미실행, 이 계획 승인 후 단계**.
- 수정 후 회귀 통과 및 새 실모델 시험: **미실행**.

임시 재현 파일은 `%USERPROFILE%/.evidence-first/diagnostics/compactor-independent-review-20261025/independent-ledger-probes.cjs`와 `progress-exit-independent.test.cjs`다. 같은 폴더의 `*-root.log`에 재실행 결과가 있다. 실제 사용자의 코드를 test fixture로 복사하지 않았다.

| 필수 사례 | 현재 확인 | 구현 시 회귀 조건 |
|---|---|---|
| 1. 동일 SHA 전체 반복 | 기존 테스트·독립 대조군 통과; 실제 1회 억제 | 사용자 원본에는 본문 유지, model input에는 사실/충분한 locator만 전달 |
| 2. 동일 SHA 새 줄 범위 | 기존·독립 대조군 통과 | 미전달 범위 본문과 coverage 증가 유지; batch 동시 도착도 확인 |
| 3. 전체 후 함수 집중 읽기 | 첫 읽기 통과, 반복 구분/F2 실패 | 첫 복구와 의미 있는 확인 허용; 동일 가용 범위 반복만 제한 |
| 4. 실제 내용 변경 | 처음 보는 SHA 통과, A→B→A 실패 | 새/복귀 SHA 모두 최신 관찰 갱신; stale check 자동 승계 금지 |
| 5. 압축 후 관찰·검토 상태 유지 | 정상 저장/restore 통과, F1/F3 의미 결함 재현 | delivered/visible/claim/check를 별개로 복원; 무결성 실패 시 safe fallback |
| 6. 다른 record version 전달 | 기존 버전 소유자 설명 테스트 통과 | 올바른 record/handle과 마지막 관찰 SHA를 반환; 과거 추론/편집 가능 플래그로 변경 추정 금지 |
| 7. 병렬 읽기 예산 부족 | 기존 batch 계약 테스트 통과; 실패의 종료 분류는 F2 실패 | peer 결과 소비, 실패 범위 미확인 유지, 축소/다음 라운드 재시도 성공을 진행 반영 |
| 8. 근거 부족 상태의 report_now | 집중 읽기·실패 읽기 반례에서 조기 종료 | 미해결 필수 근거 보존, 정상 복구 경로 허용, 완료 상태 조작 금지 |
| 9. 실제 무진전 상태의 수렴 | 기존 loop 테스트·독립 대조군 통과 | 원본 도구 호출/원장/입력가용성으로 실질 중복 확인 후 부분/최종 보고, unresolved 명시 |

추가 반례로 validator 거부/누락 proof/미읽은 줄/잘못된 인용/엔진 경로 공백/오래된 SHA 재제출, full→focused→full locator, disjoint partial locator, TTL/lineage restore, cancel 후 재진입을 포함한다. 기존 단위 테스트와 full `prediction-loop` mock 테스트를 함께 사용한다.

테스트의 oracle은 모델의 “분석했다”는 문장이 아니다. 원래 도구 응답, model-facing 변환 결과, 실제 전달 범위, 현재 입력 가용성, 관찰 SHA, check provenance, 종료 원인을 각각 검사한다. 문구 단언만으로 정상 동작을 입증하지 않는다.

## 6. 구현 순서 및 실제 모델 검증 계획

1. **F1 + F4**: 사실/검토 provenance와 최신 관찰의 불일치를 먼저 수정한다. check 기록과 source version이 잘못되면 종료 판단도 신뢰할 수 없다.
2. **F2**: 기존 recovery progress를 재사용해 정상 복구·집중 확인·실패를 진짜 정체와 구분한다. 자연 종료 대조군과 진짜 무진전 수렴 대조군을 모두 유지한다.
3. **F3 + F5**: 복구 가능한 locator 선택과 focused exact repeat를 함께 처리한다. Archive/읽기 API 공개 schema 변경이 필요한지 먼저 확인하고, 필요하지 않으면 유지한다.
4. **F6**: 내부 budget 안내의 model-input → stream/fallback → visible/durable 경계를 수정한다. 프롬프트 앞부분을 매번 바꾸는 설계로 되돌리지 않는다. 실제 보고서와 사용자 인용 보존 테스트를 함께 편입한다.
5. 각 수정은 기존 테스트 파일에 최소 합성 fixture로 편입하고 TypeScript 검사·plugin 전체 테스트·분석기 테스트를 수행한다. 계약/schema를 바꾸면 생성물 검사, 포터블 runtime 의존성 및 설치 검증을 추가한다.
6. 아래의 로컬 업데이트 절차로 **에이전트가 `UPDATE.bat`를 직접 실행**하고 설치본 source/dist/production ID를 맞춘다. LM Studio 재시작 후 실제 loaded ID를 별도로 확인한다. 실험 전 프로젝트 snapshot과 프롬프트·모델·context·sampling·도구/하네스 설정을 보관한다.
7. 같은 요청을 각 후보에서 최소 3회 비교한다. 사용자 실행을 방해하거나 기존 대화를 조작하지 않는다. latency는 캐시 상태를 함께 기록하고 가능한 한 순서를 번갈아 실행한다.
8. 라운드/호출/중복 요청·본문·token/projection/semantic compaction/소요 시간/종료 사유/보고서 여부를 따로 측정한다. 실제 입력 전문이 없으면 관측 가능 범위를 낮춰 보고한다. 내부 budget JSON이 일반 채팅에 노출되는지도 측정한다.
9. 이번 Client RPC 판정을 정확도 대조 사례로 사용한다. 필요한 상위 `GetFunctionCallspace` 및 소유 연결 근거를 조사하거나 미확인으로 남겨야 하며, 형식 검사 성공으로 P1을 확정하면 실패다. 다른 정상 코드도 별도 대조군으로 둔다.

### 마지막 배포 단계: 에이전트가 UPDATE.bat 실행

1. 구현·오프라인 회귀·타입/생성물/패키징 검사가 통과한 후보를 식별한다. 작업 중인 Unity 바이너리 등 무관한 변경은 포함하지 않는다. 현재 LM Studio 요청이 실행 중이면 강제로 중단하지 않고 종료 상태부터 확인한다.
2. 설치 전 플러그인 및 `mcp.json`·관련 설정을 백업하고 후보 commit/build ID와 로그 위치를 기록한다. 배치 파일은 Unity/Unreal MCP의 기존 binding도 검사·업데이트하므로 그 경로를 포함한다.
3. `UPDATE.bat --dry-run`으로 현재 MCP binding 및 설치 대상을 확인한 뒤 **`UPDATE.bat`의 실제 업데이트를 에이전트가 실행한다.** dry-run만으로 작업을 끝냈다고 보고하지 않는다.
4. `UPDATE_NO_PAUSE=1`과 필요한 Python/PATH 지정은 실행 프로세스에만 적용한다. 저장소의 관리 Python pointer를 우선 사용하고, stdout/stderr와 종료 코드를 보관한다. 종료 코드·실패 단계가 있으면 설치 성공으로 보고하지 않는다.
5. 설치본 소스·dist·production의 ID/필수 파일, 기존 MCP/project binding 및 설정 보존 여부를 확인한다. 실패하면 해당 단계와 복구 상태를 기록하고 백업을 사용한다.
6. `UPDATE.bat`는 완료 시 LM Studio 재시작을 안내하며, 실행 중 프로세스의 loaded ID를 자동으로 새 코드로 바꾸지는 않는다. 안전하게 재시작한 뒤 실제 loaded ID를 확인하기 전에는 활성화 완료로 표시하지 않는다. 사용자 작업 때문에 재시작을 진행할 수 없다면 “설치 완료 / 새 코드 로드 대기”로 정확히 구분한다.
7. 최종 결과에는 배치 실제 실행 결과·백업/로그 위치·설치 ID·로드 확인 여부·실모델 회귀 결과를 포함한다. 이 후속 업데이트 실행은 이번 사용자 요청으로 작업 범위에 포함되었으며, 계획 문서 작성만 마친 현재 시점에는 미실행이다.

완료 조건은 “읽기 횟수가 줄었다”만이 아니다. 실제 진행이 있는 집중/복구 읽기에서 도구를 조기 제거하지 않고, 실패한 읽기·형식 통과·과거 추론을 검증 완료로 승격하지 않으며, 원래 전체 반복 루프도 재발하지 않아야 한다. 임의의 전체 반복 0회·본문 1회 목표 때문에 정상 재확인을 막지 않는다.

## 7. 실제 수정 사항·남은 위험·최종 권고

**실제 제품 수정은 없음.** 이 작업의 산출물은 검토/계획 문서와 구조화 근거, 별도 진단 폴더의 실행 snapshot·기존 테스트 로그·임시 반례다. 기존 생성 identity 파일 및 Unity 바이너리 변경은 이 작업의 변경이 아니다.

이번 보완에서는 F6 및 실제 `UPDATE.bat` 실행·설치 확인 단계를 계획에 추가했다. 예산 안내 문구 노출은 일반 답변 채널의 결함으로 다루며, thinking을 생성하거나 숨겨진 추론의 공개 범위를 바꾸는 작업으로 취급하지 않는다.

정상 restore 자체의 원장 초기화, 새로운 SHA 차단, 현재 실행의 예산 거부 반복, 새로운 archive-version 혼동은 확인되지 않았다. 이미 동작하는 경로를 재설계하지 않는다. 유효하지 않은 handle 1회는 모델이 출력한 잘못된 locator와 이후 복구를 포함해 관찰했으며, 그 사실만으로 Archive가 올바른 handle을 무효화했다고 판단하지 않는다.

현재 버전은 **감독하에 실험·분석하는 후보로 사용할 수 있지만, ledger의 verified 또는 최종 P1 보고를 자동 승인 근거로 쓰기에는 부족하다.** 정확성에 영향을 주는 F1/F2/F4를 우선 해결한 뒤 실제 모델 회귀를 수행할 것을 권고한다. 같은 본문 반복 감소는 실제 실행에서 확인했지만, 정상 감사 능력 보존과 일반 성능 개선은 이 계획의 후속 검증 대상이다.
