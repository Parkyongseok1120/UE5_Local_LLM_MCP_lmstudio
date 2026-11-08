# 감사 품질 개선안 재검토 및 수정 실행 계획

첨부된 「감사 품질 보강 4차」를 대체하는 구현 계획입니다. 원안의 목록 보존, 읽기 예산 개선, 종료 수렴, 미확인 범위 고지 방향은 유지합니다. 다만 원안 그대로 구현하면 기존 archive의 이어 읽기가 깨지고, 복구 상태의 라운드 초과와 강제 최종보고의 발견 누락이 남습니다. 이 문서의 필수 수정까지 포함한 뒤 구현해야 합니다.

구현 담당은 사용자 지정대로 Opus입니다. 이번 재검토에서는 제품 코드를 변경하지 않았습니다. 아래 변경안은 `Proposed`이며, 현재 결함의 재현과 수정 후 검증을 구분합니다.

## 1. 기준과 확인 범위

- 검토 기준은 `claude/harness-compactor-contract-audit-4fb294@0b089c6`입니다. 해당 작업 트리에 원안이 참조한 `docs/Harness_Tool_Compactor_Contract_Lifecycle_Review_20261107.md`가 있습니다.
- 현재 작업 폴더는 `main@7046762`입니다. 이번에 조사한 제품 소스는 두 기준 사이에 차이가 없습니다. 다른 문서와 배포 파일까지 같다는 뜻은 아닙니다. 구현 착수 시 HEAD와 작업 중 변경을 다시 확인해야 합니다.
- 원본 실행 기록은 로컬 LM Studio의 `1793532481549.conversation.json`, `1793984074550.conversation.json`입니다. 각 대화의 프로젝트 위치 질문과 후속 수정 요청을 제외하고 감사 요청에 대응하는 실행을 구분했습니다.
- 첫 감사 요청의 objective는 이미 `review`였습니다. 두 번째 감사 요청은 `explain`이었고, 한도 이벤트가 60회와 63회에 발생한 뒤 `unresolved_tool_intent` 부분 보고로 끝났습니다.
- 원안의 “실제 문제 4건”은 목록과 판정 근거가 첨부되지 않았습니다. 이 숫자를 정답으로 고정하지 않습니다. register의 `found` 행 개수도 실제 결함 수가 아닙니다.
- 검토 범위는 하네스, 도구 읽기 계약, 컴팩터, 보고 전달 및 관련 엔진 사실입니다. 게임 프로젝트 전체의 새 감사나 실제 모델 재실행은 이번 문서 검토의 결과에 포함하지 않습니다.

근거 패킷은 [재검토 근거 JSON](evidence/harness-audit-quality-plan-review-20261107.json)에 기록합니다. 로컬 재현 스크립트와 결과는 `artifacts/audit-plan-review-20261107/`에 있습니다. 원본 대화와 게임 소스는 공개 테스트 fixture에 복사하지 않습니다.

## 2. 구현 전에 반영할 변경

| 항목 | 원안에서 부족한 점 | 수정 결정 | 확인 수준 |
|---|---|---|---|
| F2 조사 한도 | 한도 후 도구 재개방만 막습니다. `READ_RECOVERY`는 한도 검사 자체를 건너뜁니다. | 모든 도구 허용 라운드의 공통 진입 경계에서 기존 한도를 적용합니다. | 현재 결함 `Bug / P1 / TestVerified` |
| H1 fact 해제 | Map에서 지워도 `shownDataBlocks`의 이전 문장이 다음 모델 입력에 남습니다. | 저장소와 실제 입력 구성의 해제·대체를 함께 처리합니다. | 현재 결함 `Bug / P1 / TestVerified` |
| F 최종보고 | 강제 최종보고가 정상 EOS이면 일반 보고의 누락 검토 경로를 지나지 않습니다. | 정상·강제·부분 전달 모두 같은 누락 판정과 마지막 안내를 사용합니다. | 현재 결함 `Bug / P1 / TestVerified` |
| B archive 호환성 | body view 함수만 바꾸면 구 레코드도 새 텍스트로 읽힙니다. | 레코드에 본문 뷰 버전을 저장하고 projection과 paging이 같은 버전을 사용합니다. | 제안의 회귀 `TestVerified` |
| F3 후보 상태 | 경로가 붙은 발견과 경로 없는 철회가 다른 행으로 남고, 같은 줄에서는 앞선 발견이 뒤의 철회를 이깁니다. | 최신 판정과 주장 식별을 정리한 후보 projection을 전달합니다. | 현재 결함 `Bug / P2 / TestVerified` |
| E 예산 최소값 | 한 파일의 긴 첫 줄 때문에 필요한 최소값이 provider 전체의 metadata floor가 됩니다. | 요청별 retry minimum과 provider metadata 비용을 분리합니다. | 현재 결함 `Bug / P2 / TestVerified` |
| B·C 범위 계약 | `read: boolean`, `truncated: false`, 256개 상한만으로는 전체 읽기·목록 완료를 판정할 수 없습니다. | 부분 읽기, 미열람 디렉터리, 페이지 범위, 생략·초과 상태를 명시합니다. | 설계 보완 `Proposed` |
| A 거부 안내 | 모든 거부를 이번 배치의 일시 부족으로 설명합니다. | capacity, schema 한계, 수명 오류를 나누어 안내합니다. | 원안과 현재 코드 대조 `SourceVerified` |
| G 분류 테스트 | “이 함수 설명해줘 → explain”은 현재 계약과 충돌합니다. | 이번 변경에서는 `review`를 유지하고 찾을·문제점·위반 표현을 보강합니다. | 현재 동작 `TestVerified` |
| H floor 측정 | 실제로 선택된 assembled input 대신 `floorCandidate.history`만 측정합니다. | 선택된 floor 입력을 측정하고 문자·토큰, 역할·부분집합을 구분합니다. | 현재 구조 `SourceVerified` |

### 수치로 재현한 사항

- `maxResearchRounds=3`에서 복구 경로가 성공한 읽기 5라운드를 처리하고 여섯 번째 도구 허용 호출까지 진행했습니다. 한도 이벤트는 0회였습니다.
- fact 삭제 후 현재 fact는 0자였지만 다음 입력으로 전송한 이전 fact는 51자였습니다.
- 한도 1회 후 강제 최종 입력에 발견 후보가 있었는데, 모델이 “주요 문제 없음”으로 답하자 보고 보완 0회로 `reportCompleted=true`가 됐습니다. `reportCompleted`는 전달 완료이며 사실 검증이 아니라는 기존 구분도 유지해야 합니다.
- 원안처럼 body view만 교체한 실험에서 같은 archive hash와 뷰 버전의 본문 길이가 1,411자에서 176자로 바뀌었습니다. 이전에 유효했던 offset 500이 `invalid_range`가 됐습니다.
- 실제 provider가 긴 첫 줄에 요구한 최소 10,414B를 broker에 관찰시키자, 4,096B로 완독되는 다른 작은 파일도 가용 4,000토큰에서 거부됐습니다.
- 100줄 파일의 1줄만 반환해도 `bodyReturned=true`, `completeText=true`가 될 수 있습니다. 후자는 반환받은 텍스트 캐시의 완전성이며 전체 파일 완독이 아닙니다.

## 3. 유지할 책임과 불변 조건

| 사실 또는 상태 | 소유자 | 다른 코드가 지켜야 할 경계 |
|---|---|---|
| 거부 코드와 재시도 처분 | `read-budget-denial.ts` | 생산자·경계·복구가 같은 표를 사용합니다. |
| 읽기 예약과 요청별 최소값 | `BudgetBroker` | provider의 응답 한도와 최종 exact input 측정은 유지합니다. |
| 목록의 구조적 해석 | `directory-listing.js` | provider별 성공 계약 확인 뒤 사용하며, 감사 대상 필터와 분리합니다. |
| 반환 목록·본문·범위·버전 | `ReadLedger` | 기존 `fileSourceKey`, 범위 병합, 무효화 규칙을 재사용합니다. |
| archive 본문 뷰와 offset | `EvidenceArchive` | 저장된 레코드 버전이 projection과 paging 양쪽을 결정합니다. |
| fact 수명 | `RUNTIME_FACTS`와 실행 단위 저장소 | `ContextManager`가 해제를 실제 입력에 반영합니다. |
| 조사 한도와 복구 전이 | 기존 실행 상태 및 recovery 정책 | 다른 phase로 전이해도 한도 소진 사실을 잃지 않습니다. |
| 미검증 주장 상태 | `ReviewRegister` | 렌더러는 상태를 판정하거나 결함으로 승격하지 않습니다. |
| 보고 의무와 범위 차이 | `report-scope.ts`의 기존 판정 | 모델 보완과 deterministic 안내가 같은 결과를 사용합니다. |
| 최종·부분 전달 | `DeliveryController` | 재시도 횟수, 중복 전달, 초기화 실패를 같은 실행 소유자가 관리합니다. |
| 엔진 사실 | `config/unreal_contracts.json` | 엔진 판정·생성물·eval이 같은 revision을 사용합니다. |

별도 감사 게이트, 독립된 runtime data 주입 경로, 두 번째 읽기 장부를 추가하지 않습니다. 한도 수정은 기존 정책을 공통 경계로 옮기는 작업입니다. 새 영속 정보는 ledger의 목록뿐이라는 원안의 제한은 철회합니다. **목록의 완료·생략 정보와 archive 본문 뷰 버전도 저장**해야 합니다.

목록이나 본문이 반환됐다는 사실은 의미 검토 완료를 증명하지 않습니다. 모델의 판단 메모, 반환된 근거, 현재 입력에 보이는 본문을 계속 구분합니다. 언리얼 코드에 네임스페이스를 새로 추가할 필요는 없습니다.

## 4. 수정 실행 계획

### F2 조사 한도와 최종 재시도

근거: `prediction-loop.ts:1494`, `prediction-loop.ts:1610`, `execution-state.ts:66`, `delivery-controller.ts:104`입니다.

1. 한도 계산 단위를 먼저 고정합니다. 현재 `roundIndex`는 0부터 시작하며 복구·보완 호출에서도 증가합니다. 조사 예산을 소비하는 도구 허용 prediction과 도구 없는 최종 보정의 횟수를 명시적으로 구분합니다. 기존 설정의 의미를 바꾸는 경우 telemetry와 테스트에도 그 차이를 드러내야 합니다.
2. 모든 도구 허용 prediction 직전에 한도를 검사합니다. 일반 조사, 읽기 복구, 도구 재계획, reasoning 복구 후 재진입을 포함합니다. 각 분기의 `continue`가 검사를 피할 수 없어야 합니다. 완료된 도구 결과는 보존합니다.
3. 기존 recovery 정책에 `mayReopenTools`를 두되 `finalizationTrigger` 문자열 하나에만 의존하지 않습니다. `startReplan()`이 trigger를 지우므로, 실행 상태가 한도 소진 사실을 유지하거나 단일 예산 상태에서 다시 판정해야 합니다. 같은 정책을 예약·dispatch까지 연결해 한도 이후 새 provider 호출이 없음을 확인합니다.
4. 최종 raw tool intent에는 도구 없는 보정을 **추가 1회 이하** 허용합니다. 현재 `DeliveryController.begin()`의 추가 횟수는 replan 횟수에 연결돼 있으므로 이를 재사용해 우회하지 않습니다. 전달 소유자가 보정 이유와 소진 여부를 관리합니다.
5. 재시도 예정인 첫 실패에는 부분 보고를 먼저 내보내지 않습니다. 보정 실패, 취소, timeout, 예산 부족 시 한 번의 부분 보고로 수렴합니다. 사용자 취소 이후 새 모델 호출은 하지 않습니다.
6. 다른 finalization trigger의 기존 복구는 유지합니다. 다만 조사 한도·시간·취소 같은 실제 종료 제약을 우회할 수는 없습니다. 모든 trigger를 무조건 복구 금지로 바꾸지는 않습니다.

검증은 술어 단위 테스트에 더해 production handler로 수행합니다. 작은 한도, 매번 진전이 있는 recovery, 진전 없는 recovery, max=0, 한도 직전 replan, 한도 후 raw intent, 취소, 보정 실패를 포함합니다. 실제 provider dispatch 수와 phase 전이, 최종 전달 수를 확인해야 합니다.

### F1 남은 조사 예산 안내

1. 순수 함수 `researchRoundNotice`는 조사 횟수의 같은 단위를 사용합니다. 60회 한도에서 48회 조사 완료 후 남은 12회를 알리도록 시점을 정의합니다. “R48”이라는 표기만 검사하지 않습니다.
2. 경고 구간은 `min(max, max(5, ceil(max * 0.2)))`처럼 작은 양수 한도에서 유효하도록 제한합니다. 0이 무제한인 설정에서는 안내하지 않습니다.
3. 한 번 설정하고 유지할 문구에는 발생 시점을 씁니다. 예: “48회 조사 완료 시점에 한도 60회 중 12회가 남았습니다. 현재 근거로 보고를 준비하고 미확인 범위를 명시하십시오.” 갱신하지 않는 문구를 현재 남은 횟수처럼 쓰지 않습니다.
4. 기존 `Runtime evidence observation:` 헤더와 기존 fact 주입 경로를 사용합니다. 새 내부 헤더를 만들지 않습니다. 최종 전달 진입 시 해제합니다.
5. 임계 도달 여부는 실행 단위 상태가 보유합니다. 한도 경계 진입, 중복 호출, 작은 max, recovery에서 임계 통과를 테스트합니다.

### H1 runtime fact 수명과 실제 입력 제거

근거: `execution-contracts.ts:15`, `prediction-loop.ts:269`, `prediction-loop.ts:920`, `context-manager.ts:322`입니다.

1. `RUNTIME_FACTS`에 해제 조건을 선언하고 저장소는 이를 집행합니다. `set`, `clear` 또는 `onEvent`, `entries`의 작은 API로 시작합니다. 새 일반 이벤트 버스는 만들지 않습니다.
2. `budget`은 해당 제약이 해소됐다는 라운드 관측으로 해제합니다. 한 배치에서 파일 하나가 성공하고 다른 읽기가 거부됐는데 “성공이 하나 있음”만으로 지우면 안 됩니다. 제한·거부·잘림의 의미를 분리합니다.
3. `stagnation`, `repeated_failure`는 기존 진전 판정이 회복을 확인할 때 해제합니다. `prior_turn`은 실행 수명이며 현재도 실행마다 초기화됩니다. `report_review`, `delivered_report`는 해당 보완 생성이 끝날 때까지 필요합니다. 해제 코드가 없다는 이유만으로 여섯 key 모두를 P1 결함으로 취급하지 않습니다.
4. 해제와 대체가 `shownDataBlocks`에도 반영되도록 기존 input composition 경로를 수정합니다. 최소안은 삭제·수명 전환 시 명시적으로 이전 runtime block을 재구성하는 것입니다. 캐시 효율을 위해 오래된 명령을 유지하지 않습니다. 영향을 받는 문장은 지우되 무관한 반환 근거는 보존합니다.
5. `lastBudgetFeedback` 같은 중복 억제 상태도 정리합니다. 저장소 해제 뒤 같은 경고가 재발하면 다시 설정돼야 합니다. 동일값 `set()`을 저장소에서 멱등 처리하면 별도 문자열 캐시를 줄일 수 있습니다.
6. `round` 수명의 기존 key, review supplement, 실행 종료·예외·재진입도 함께 확인합니다. 초기화 전 callback이 미초기화 객체를 참조하지 않아야 합니다.

필수 테스트는 “설정 → 다음 입력에 있음 → 해제 → 다음 **실제 assembled input**에 없음 → 같은 내용 재발 → 다시 있음”입니다. Map의 크기만 검사하지 않습니다. 캐시 consolidation 임계 미만, projection 유무, 병렬 성공·거부 혼합을 포함합니다.

### A 거부 코드와 처분

근거: `budget-broker.ts`, `tool-boundary.ts:180`의 생산·소비 경로입니다.

1. 기존 거부 코드 7종을 union과 처분표로 모으고 `readBudgetDenialMessage`를 이동합니다. `tool-boundary`와 복구 분류가 표를 조회하게 합니다. 기존 처분의 변경은 별도 테스트로 명시합니다.
2. 일시적인 배치 capacity 부족에만 “이번 배치의 예약 여유가 부족하며 작업 전체 예산 소진을 뜻하지 않는다”라고 안내합니다. 컨텍스트 압축은 필요와 설정에 따라 수행되므로 “라운드 사이에 항상 압축된다”라고 보장하지 않습니다.
3. `minimum_exceeds_published_limit`은 schema 상한과 필요한 최소값의 충돌입니다. 더 좁은 범위는 본문 비용이 원인일 때의 대안입니다. metadata 자체가 상한을 넘으면 같은 도구의 범위를 줄여도 해결되지 않는다는 점을 구분합니다.
4. `duplicate_reservation`, `batch_closed`, `invalid_reservation` 같은 수명·내부 계약 오류를 모델의 읽기 크기 탓으로 설명하지 않습니다. overrun과 재측정이 필요한 상황도 별도로 안내합니다.
5. fixture 문구와 처분 불변식 테스트를 갱신합니다. 문자열 테스트 외에 거부 후 실제 선택한 recovery action을 검사합니다.

### E 읽기 크기와 요청별 최소값

근거: `budget-broker.ts:49`, `budget-broker.ts:281`, `budget-broker.ts:296`, `direct-runtime-context.js:144`입니다.

1. **추가 필수 수정:** `OUTPUT_LIMIT_EXCEEDED.retry.minimum`에는 첫 본문 줄 비용이 들어갈 수 있습니다. 이를 provider 공통 metadata floor로 올리지 않습니다. 기존 요청별 `retryFloors`에만 기록합니다. 성공 응답에서 본문을 제외하고 관찰한 metadata 비용의 기존 활용은 유지합니다. 요청 키가 파일·프로젝트·selector를 구분하는지도 검사합니다.
2. 범위·cursor·offset이 없는 첫 `read_file`에만 전체 파일 추정치로 요청을 높이는 정책을 적용합니다. `read_file`이라는 이름만으로 전체 읽기라고 판정하지 않습니다. provider별 capability와 selector를 확인합니다.
3. 원안의 “들어가지 않으면 유지”와 `min(estimatedBytes, maximum, share)`는 모순됩니다. 이번 정책은 **추정 전체 응답이 상한과 share 안에 들어갈 때만** 올리는 것으로 고정합니다. 들어가지 않으면 기존 allocation을 유지합니다. 부분적으로만 늘리는 정책은 별도 효과 측정 없이 섞지 않습니다.
4. share는 단순 byte 수 `available/2`가 아닙니다. 기존 multiplier와 envelope reserve를 적용한 같은 단위의 함수로 계산합니다. 기존 기본값 floor, 요청 최소값, schema 최대값과의 우선순위를 명시합니다. 두 요청이 자기 최소값을 받을 여유가 있을 때 인위적인 거부를 만들지 않아야 합니다.
5. `estimatedBytes`는 15% 증가, 줄 번호 비용, 2,560B metadata를 포함한 추정치입니다. 1,628B 파일 예시는 현재 계산상 4,841B입니다. JSON escaping·경로 길이·CJK·매우 긴 줄·파일 변경 때문에 여전히 잘릴 수 있으므로 실제 provider의 `hasMore`, range, EOF를 확인합니다. 정확한 반환 크기처럼 기록하거나 예약 전에 완독을 선언하지 않습니다.
6. `knownResultBytes`, request-specific retry minimum, 원래 요청값, effective budget을 구분해 기록합니다. share 조정이 추정값 때문에 retry minimum을 다시 낮추거나 이미 알려진 정확한 응답 비용을 이중 예약하지 않게 합니다.
7. 안내는 전체 읽기에서 `byteBudget` 생략을 권장하되 “크기 힌트가 있으면 호스트가 이를 사용하고, 없으면 adaptive default로 결정한다”라고 씁니다. 항상 목록 크기를 안다고 보장하지 않습니다. 페이지 예산 유지와 host 자동 결정 중 어느 경로가 우선인지 모순 없이 표현합니다.

테스트에는 작은 파일, 추정치가 share와 정확히 같은 경계, share 초과, 최소값 충돌, 명시적 range·cursor·offset, 힌트 없음·낡은 힌트, 긴 첫 줄 후 다른 작은 파일, 순서가 다른 병렬 2건을 포함합니다. 원본 실행의 잘린 18건은 전체 읽기·range·continuation으로 나누어 **변경 적용 대상 수**부터 계산합니다. 같은 과거 capacity를 넣은 계산은 상한 효과 추정이며 새 실행의 재읽기 감소 증명이 아닙니다.

### T3 provider의 continuation 제안

`direct-read-capabilities.js`의 `continuationArgs`에서 `byteBudget` 복제를 제거하는 방향을 유지합니다. 경로, project, cursor, 범위는 보존합니다. 이 변경은 하네스 밖의 직접 MCP 소비자에도 적용되므로, 생략 시 provider 기본 예산으로 이어 읽기가 가능한지 확인합니다. 긴 줄의 최소값은 오류의 retry 계약으로 계속 전달해야 합니다. 성공 응답 continuation hint와 실패 응답의 필수 재시도 정보는 구분합니다.

### B 목록 보존과 archive 호환성

근거: `evidence-archive.js:15`, `evidence-archive.js:697`, `evidence-archive.js:753`, `working-context.js:771`, `evidence-manager.ts:75`입니다.

**파서와 본문**

1. `listingFacts`는 path, 관측 entries, 각 entry의 type/name/size, 페이지 범위, 이어 읽기 정보와 완료 여부를 해석합니다. 단순 `{complete: !truncated}`로 정의하지 않습니다. `offset > 0`인 마지막 페이지도 `truncated=false`일 수 있습니다.
2. 잘못된 이름, 빈 이름, 경로 구분자가 들어간 단일 entry, `.`/`..`, 실패·모호한 결과를 배제합니다. ledger 기록은 기존의 provider 권한 확인과 유일한 요청·결과 pairing 이후에만 합니다. 임의의 `entries` 배열을 신뢰된 inventory로 승격하지 않습니다.
3. `listingBody`는 반환된 **모든 종류의 이름**을 표시합니다. 소스 확장자 필터는 감사 범위 선정에서만 사용합니다. `.ini`, `.uproject`, JSON, 에셋, 다른 엔진의 파일이 generic body view에서 사라지면 안 됩니다. 특수문자와 줄바꿈이 이름 구조로 오해되지 않도록 안전한 표현을 사용합니다.
4. 이름 목록도 크기를 넘으면 기존 부분 본문 안내와 readHandle로 이어집니다. provider의 다음 목록 페이지와 archive의 현재 응답 다음 조각을 구분합니다. `offset`, `nextOffset`, `totalEntries` 등 source pagination 정보가 projection·read 결과에서도 보존되도록 기존 semantic facts 계약을 보완합니다. 새 projection kind는 만들지 않습니다.

**archive 버전**

5. 새 목록 레코드에 `bodyViewVersion` 같은 저장된 렌더링 버전을 둡니다. 필드가 없는 구 레코드는 현재 JSON entries 뷰 v1로 해석합니다. 새 레코드만 이름 목록 v2를 사용합니다. source/git 본문의 기존 동작은 유지합니다.
6. `EvidenceArchive.read()`와 `WorkingContext.project()`는 저장된 레코드의 같은 버전으로 본문을 만듭니다. `put()`이 동일 body/callKey의 구 레코드를 반환할 때 projection만 최신 renderer를 사용해서는 안 됩니다.
7. 같은 기존 readHandle/hash/offset의 의미는 업데이트 후에도 동일해야 합니다. 원본 envelope hash와 본문 뷰의 버전을 혼동하지 않습니다. 되돌리기 시 새 레코드를 읽을 수 없는 구 버전은 추측해서 다른 텍스트를 반환하지 않고 명시적으로 미지원 처리해야 합니다. 실제 배포 rollback 절차에 이 호환 조건을 기록합니다.
8. 구 레코드, 진행 중 offset, 동일 구 레코드 재저장, 재시작/load, 새 v2 레코드에서 projection excerpt와 전체 paging의 연결을 테스트합니다. 원안의 “저장된 body/hash가 있으므로 자연히 호환된다”는 설명은 삭제합니다.

**목록 장부와 수명**

9. 목록은 executionId, provider, 프로젝트 identity, directory sourceKey로 구분합니다. `fileSourceKey`와 기존 범위 장부를 재사용하고 path suffix만으로 다른 프로젝트를 합치지 않습니다. `sizeHint`도 동일한 해석 결과로 갱신하되 크기 힌트와 검토 완료 여부는 분리합니다.
10. 부분 목록의 관측 entry를 버리지 않습니다. 반환된 항목은 기록하고 전체 목록이 불완전하다고 표시합니다. 페이지가 변하는 디렉터리에는 안정된 snapshot/version이 없다면 완전한 합집합이라고 확정하지 않습니다. 하위 디렉터리를 나열했다고 그 안까지 조사한 것으로 보지 않습니다.
11. 256개 상한이 directory 수인지 file 수인지 명시하고, 전체 entry·문자/직렬화 크기도 제한합니다. 상한 초과·eviction에는 `complete=false`와 생략 사실을 보존합니다. 생략한 항목을 전부 읽은 것으로 만들거나 개수를 모를 때 정확한 미검토 수를 만들어내지 않습니다.
12. `returnedSources().listed`의 단순 `read` 대신 `unread`, `partial`, `complete`, `unknown`에 해당하는 coverage 상태와 그 판정 근거를 제공합니다. `bodyReturned`나 `completeText`로 완독을 계산하지 않습니다. 동일 sourceKey·현재 version의 실제 범위 합집합과 total/EOF를 사용합니다. 예전 실행에서 읽은 자료, 무효화된 자료, search hit, archive envelope는 따로 취급합니다.
13. load 시 이전 execution의 inventory가 새 실행의 완료 사실이 되지 않게 합니다. 같은 실행의 재입력·재관측은 멱등해야 합니다. coverage는 가능하면 기존 장부에서 파생하고 또 다른 mutable 읽음 캐시를 만들지 않습니다.

실제 목록 9개와 대형 목록을 사용한 재현 외에, 마지막 페이지만 반환, 부분 페이지 중복·순서 역전, 같은 상대경로의 두 프로젝트, 256개 초과, 재실행, 파일 수정·삭제를 검사합니다. 모든 이름 보존 테스트의 전제는 해당 이름 텍스트가 실제 projection 허용량 안에 들어가는 경우로 한정합니다.

### F3 후보 보존과 전체 전달 경로

근거: `review-register.ts:87`, `review-register.ts:123`, `review-register.ts:179`, `prediction-loop.ts:1321`, `prediction-loop.ts:1528`입니다.

1. `ReviewRegister`가 최신 판정을 반영한 read-only 후보 projection을 제공합니다. active `rows`뿐 아니라 기존 retired 후보와 `missing()`의 책임을 재사용합니다. 렌더러가 별도로 found/cleared를 재해석하지 않습니다.
2. 경로가 붙은 발견 뒤 경로 없는 철회, 같은 줄의 발견 뒤 철회, 다른 파일의 동명 symbol, 같은 symbol의 서로 다른 주장, source version 변경을 테스트합니다. 명확히 같은 주장일 때만 상태를 연결합니다. 모호한 철회는 임의로 전부 지우거나 확정 발견으로 남기지 않고 재확인 대상으로 표현합니다.
3. 부분 보고에는 “미검증 검토 후보”를 근거 발췌보다 먼저 싣습니다. 최대 12행과 총 문자 예산을 두고, 초과 시 생략 개수 또는 상한 초과 사실을 표시합니다. 후보가 없으면 기존 부분 보고를 유지합니다. bundle의 파일·범위·관측 버전과 상태를 사용하고, 추론 원문을 그대로 길게 노출하거나 확인된 결함 수로 집계하지 않습니다.
4. 일반 EOS뿐 아니라 강제 최종보고에도 기존 `reportReviewObligations`를 적용합니다. 이미 보고된 후보는 중복 표시하지 않고, 미해결 후보가 있으면 기존 도구 없는 보완을 공유합니다. raw intent 보정과 보고 누락 보정이 각각 별도 무한 루프를 만들지 않도록 실행 단위의 유한 전달 예산으로 묶습니다.
5. 보완 예산이 없거나 생성이 비어 있으면 기존 `terminalReportNotices` 경로로 미해결 후보와 범위 한계를 결정적으로 표시합니다. 새로운 UI 출력 경로나 runtime 주입 경로를 만들지 않습니다. 정상 문장으로 끝났다는 이유로 후보 누락이 사라져서는 안 됩니다.
6. `reportReviewMode=off`, observe-only, 취소, 초기화 실패 정책을 명시합니다. 명시적으로 꺼진 기능을 몰래 모델 재호출로 대체하지 않습니다. 필요한 상태 안내와 자동 보완의 구분을 유지합니다.
7. `DeliveryController`는 실행 시작에 한 번 만들고 objective·도구·후보를 안전한 포트로 받습니다. 초기화 실패 전에도 포트는 빈 값이나 준비된 값으로 동작해야 합니다. H5 자체는 P3 구조 정리이며 이번 실패의 독립 원인으로 주장하지 않습니다.

최종 handler 테스트는 후보 있음/없음/철회/재확인, active 40행을 넘는 retired, 표시 12행 초과, 정상·강제·부분 보고, 보완이 빈 출력, 재시도 후 실패, 사용자 취소를 포함합니다. found 행이 출력됐다는 검사와 그 내용이 사실인지 평가하는 검사를 분리합니다.

### C 결함 없음 결론의 범위

1. `REPORT_REVIEW_OBJECTIVES`를 선언하고 기존 review/explain 집합을 재사용합니다. 기존 verification/file/prior-item 범위 검사는 mutate/build에도 필요한 경우가 있으므로 이 상수로 기존 `scopeGaps` 전체를 제한하지 않습니다.
2. `cleanVerdictScopeGap`은 현재 요청 범위 안의 미확인 자료와 결론 문장을 대조하는 advisory 함수입니다. 목록이 불완전하거나 하위 디렉터리를 읽지 않았다면 파일명뿐 아니라 그 사실도 표시합니다. 전부 읽도록 강제하는 게이트를 만들지 않습니다.
3. 결론·요약·마지막 문장 판별에는 코드 fence, 인용, 철회 문장, 표를 제외합니다. “P0/P1 없음”을 “결함이 하나도 없음”과 같은 강도로 해석하지 않습니다. “지금 반환된 A/B 구현 범위에서 확인된 P1 없음”처럼 한정된 결론은 구분합니다.
4. 답변 어딘가의 “미확인” 한 단어로 광범위한 clean verdict를 면제하지 않습니다. 같은 결론에 실제 요청 범위 제한이 연결됐는지 확인합니다. 불명확하면 확정 판단 대신 범위 안내만 남깁니다.
5. 모델 보완, 최종 footer, 부분 보고가 같은 gap 결과를 사용합니다. 최종보고 분기에서 검토가 건너뛰어지지 않는 F 수정과 함께 연결합니다.
6. 기존 `implementation_scope`는 raw visible transcript를 읽고 있습니다. 이를 목록 장부로 통합한다면 신뢰된 pairing과 source identity를 유지하고, raw history의 비신뢰 목록을 새 ledger에 넣지 않습니다. 현재 production 호출은 `visibleTranscript.snapshot()`이므로 “압축되면 이 검사도 반드시 사라진다”는 주장은 하지 않습니다.

이 기능은 조기 종료의 과도한 완료 표현을 줄입니다. 읽은 구현 안의 논리 버그를 자동으로 찾아내거나 최소 조사 라운드를 보장하지는 않습니다. 그 효과는 아래 실측의 결함·비결함 기준 사례로 평가합니다.

### G 요청 분류

`REVIEW_REQUEST`에 찾을·찾기·찾는·찾으 및 문제점·위반·결함 표현을 보강합니다. 기존 `CHANGE_REQUEST` 우선순위를 유지하며, “찾아서 수정해”는 mutate입니다.

회귀 기대값은 첫 감사 원문 review, 두 번째 감사 원문 review, “찾아서 수정해” mutate, **“이 함수 설명해줘” review**, 일반 인사 explain입니다. 현재 `설명`과 `explain`은 명시적인 읽기 전용 요청으로 review profile을 사용하고 기존 테스트도 이를 요구합니다. 설명 요청을 새 explain 의미로 바꾸려면 도구 프로필과 requiredEffects까지 별도 설계해야 하므로 이번 범위에서 하지 않습니다.

수정 금지 표현과 실제 수정 요청, 설명 중 포함된 명칭으로 변경 권한이 넓어지는지 negative case를 보강합니다. schema 감소는 같은 도구 구성·모델·요청으로 측정하며, G가 register를 켜거나 C를 처음 활성화한다고 설명하지 않습니다. review/explain 모두 기존 register 관찰 대상이고 C의 제안 대상입니다.

### D Server RPC 소유 연결 사실과 감사 지침

새 사실의 방향은 유지합니다. 클라이언트에서 서버로 보내는 일반 Server RPC는 소유 연결 계약을 따르며, 다른 클라이언트 소유나 소유 연결이 없는 Actor에 대한 호출은 실행되지 않습니다. 서버에서 자체 호출하는 경우와 게임 규칙 검증은 구분해야 합니다. 소유 연결 검사가 탄약·생존·속도·입력 검증까지 대신하지 않습니다. [Epic RPC 실행 표](https://dev.epicgames.com/documentation/unreal-engine/remote-procedure-calls-in-unreal-engine), [Actor 소유 연결 설명](https://dev.epicgames.com/documentation/unreal-engine/actor-owner-and-owning-connection-in-unreal-engine)이 이 구분을 뒷받침합니다.

설치된 5.7.4와 5.8.2에서 확인한 직접 소스 위치는 다음과 같습니다. 엔진 소스를 저장소에 복사하지 않습니다.

| 경로와 symbol | 5.7.4 | 5.8.2 | 입증하는 단계 |
|---|---:|---:|---|
| `Engine/Source/Runtime/Engine/Private/Components/ActorComponent.cpp`, `UActorComponent::GetFunctionCallspace`의 owner 위임 | 1219 | 1258 | 컴포넌트의 callspace 위임 |
| `Engine/Source/Runtime/Engine/Private/DataChannel.cpp`, Actor connection과 channel connection 비교 | 3358 | 3391 | 수신 연결의 net owner 판정 |
| `Engine/Source/Runtime/Engine/Private/DataReplication.cpp`, `FObjectReplicator::ReceivedRPC`의 `ShouldCallRemoteFunction` 호출 | 1387 | 1391 | 실행 여부 분기 |
| `Engine/Source/Runtime/Engine/Private/NetDriver.cpp`, `UNetDriver::ShouldCallRemoteFunction` | 8242 | 8452 | 서버에서 bNetOwner 요구 |

1. `ue.net.server-rpc-owning-connection`의 적용 범위와 위 호출 경로를 statement/evidenceRefs에 기록하고 revision을 8로 올립니다. Iris나 커스텀 NetDriver까지 위 경로 하나로 검증했다고 확대하지 않습니다. 해당 경로를 주장하려면 별도 근거가 필요합니다.
2. `verify_engine_contracts.py`는 인용 줄·주변 창의 버전 간 대응 검사입니다. 새 사실의 의미를 처음 증명해 주는 도구가 아닙니다. 먼저 양쪽 소스를 읽고, 그 후 신규 인용을 포함해 실행합니다.
3. marker는 Server 문맥과 “아무/모든 클라이언트가 호출 가능” 같은 문제 표현을 함께 요구하도록 설계합니다. `ServerSetADS`와 설명이 다른 줄에 있는 원문도 잡아야 합니다. marker 일치는 오류 확정이 아니라 근거 확인 요청입니다.
4. owning-client 호출, 서버 자체 호출, Client RPC, 권한은 있으나 gameplay validation이 필요하다는 문장, 철회·코드 예시를 negative case로 둡니다.
5. 현재 free-text 보고는 P0–P3를 검사하지만 근거 packet의 대상은 P0/P1입니다. 원안의 “엔진 claim 검사는 P3까지”라는 문장은 이 둘을 구분해 적습니다.
6. eval pin과 생성물을 갱신합니다. 체크리스트는 기존 섹션 안에서 간결하게 보강하고, 송신→수신→상태 변경→원격 관찰 및 시작→종료→복구의 대응 경로를 확인하도록 합니다. 고정된 글자 수가 핵심 의미를 빼는 기준이 되어서는 안 됩니다.

### H floor와 projection 측정

근거: `context-manager.ts:740`–`744`, `budget-broker.ts:146`, `working-context.js:782`입니다.

1. `floorCandidate`가 아니라 실제 선택한 `floor`의 assembled history와 measurement를 대상으로 합니다. `floorInput`이 exact이면서 더 작을 때만 선택되고, 아니면 `before`가 선택됩니다. `selectedFrom`과 `exact`를 기록합니다.
2. role별 문자 수, tool request/result의 비중첩 구분, runtime/reference frame의 분류 규칙을 명시합니다. pinned·memory frame은 역할 합계의 부분집합일 수 있으므로 같은 합계에 더하지 않습니다. `otherChars` 또는 envelope 경계를 정의해 문자 합 검사를 정확하게 합니다.
3. `toolSchemaTokens`, `inputTokens`는 별도 토큰 지표이고 문자 합에 넣지 않습니다. UTF-16 문자 수, UTF-8 byte 수, tokenizer count를 구분합니다. 역할별 문자 비율이 정확한 토큰 기여도라고 주장하지 않습니다.
4. projection telemetry는 실제 변경된 결과만 대상으로 `projectedCount`, `originalChars`, `projectedChars`, 절감량을 기록합니다. pinned·너무 작은 결과·archive 실패·절감 불가로 건너뛴 수는 별도 분류하고 중복 투영을 합산하지 않습니다.
5. 측정 추가가 schema, 선택 입력, projection 임계, prompt를 바꾸지 않도록 합니다. 단위 테스트는 선택된 입력의 분해와 합계를 확인합니다. 이 단계에서 최소 절감률 30% 같은 새 정책은 넣지 않습니다.
6. 같은 fixture와 모델 설정으로 전후를 비교하고 provider envelope, runtime stale block, projection 고정 비용, pinned 범위를 분리해 봅니다. pinned working set의 35,789자는 특정 실행 관찰값입니다. 코드는 effective low water에 비례해 계산하므로 고정 상한이라고 설명하지 않습니다.

## 5. 구현 순서와 변경 범위

1. 기준선과 재현 사례를 고정합니다. H의 측정 필드가 필요하면 동작을 바꾸지 않는 telemetry 커밋을 먼저 둡니다.
2. F2에서 모든 phase의 조사 한도를 적용하고 전달 재시도 예산을 연결합니다. F1 안내를 같은 조사 계수에 연결합니다.
3. H1에서 fact 수명과 실제 모델 입력 제거를 함께 수정합니다.
4. A와 E의 요청별 최소값 격리를 수정한 뒤, 전체 읽기 예산 확대 및 T3 continuation 변경을 적용합니다.
5. B의 버전 있는 body view와 inventory를 구현합니다. archive 호환 테스트가 먼저 통과해야 합니다.
6. register의 최신 상태 정합성, F3 후보 보존, 강제 최종보고의 누락 검토와 H5 단일 전달 소유자를 연결합니다.
7. C 범위 판정을 모델 보완과 terminal notice에 함께 연결합니다.
8. G 요청 분류와 D 엔진 사실·지침·eval을 각각 독립 커밋으로 적용합니다.
9. 전체 통합 검사와 동일 조건의 실측을 수행합니다. provider envelope 슬림화와 projection 최소 절감률 같은 후속 최적화는 결과를 보고 별도 결정합니다.

각 커밋은 관련 회귀와 타입 검사를 수행하고, 모듈 경계가 연결된 시점 및 최종 통합 시 전체 검사를 수행합니다. 매 커밋마다 동일한 전체 검사를 반복하는 것은 필수 조건으로 두지 않습니다.

원안의 수정 파일 목록에 `review-register.ts`, `execution-state.ts`, archive의 저장·읽기·재사용 경로, `context-manager.ts`의 **동작 수정**을 추가합니다. 따라서 `context-manager.ts`는 telemetry만 수정한다는 제한을 해제합니다. `context-ports.ts`에는 반환형뿐 아니라 실제 ledger method 계약과 mock도 갱신해야 합니다. `runtime-fact-store.ts`를 추가하면 신규 파일 목록에도 명시합니다.

`prediction-loop.ts`는 연결과 기존 분기 이동 위주로 순감소를 유지합니다. 크기 검사는 기존 테스트의 LF 정규화 기준을 따릅니다. 파일 크기 때문에 안전한 기존 모듈 분리를 피하거나 문자열·조건을 난해하게 축약하지 않습니다. `compaction-tool-memory.js`, `prediction-ui.ts`, `internal-data-output.ts`는 현재 근거상 변경 필요가 없습니다. 다만 기존 내부 헤더와 출력 필터를 사용한다는 통합 회귀는 수행합니다.

## 6. 구현 후 검증과 수용 기준

### 자동 검사

- 플러그인: clean-dist, TypeScript build, `run-tests.cjs`입니다. 로컬 PATH 제약이 있으면 저장소의 기존 짧은 PATH 절차를 사용합니다.
- 생성물 `--check`는 **4종**입니다. `build-design-guidance.cjs`, `build-tool-contract.cjs`, `build-unity-tool-contract.cjs`, `build-runtime-identity.cjs`를 확인합니다.
- provider의 `direct-response-budget.test.js`와 관련 전체 테스트를 수행합니다. 하네스 없이 이어 읽는 소비자도 포함합니다.
- 엔진 계약 관련 Python 테스트, eval 사례, 신규 사실을 포함한 `verify_engine_contracts.py`를 수행합니다.
- archive 구·신 레코드 왕복, inventory 상한과 실행 전환, runtime fact 재발, 제한 이후 provider 호출 0건, 재시도 상한, 최종 중복 출력 0건을 검사합니다.
- raw 도구 구문과 내부 register 행·fact가 사용자 보고로 그대로 새지 않는 기존 출력 계약 테스트를 유지합니다.

### 원본 기록 replay

원본 실행을 바꿀 수 있는 모델 재실행과 고정 로그를 사용하는 replay를 구분합니다. replay는 기록된 입력과 상태 변환의 효과를 확인합니다. 새로운 모델 판단이나 미래 capacity까지 재현한다고 주장하지 않습니다.

| 기록 | replay로 확인할 것 | replay만으로 확정하지 않을 것 |
|---|---|---|
| `1793984074550` | 정확한 감사 턴 분류, 예산 확대 적용 대상, 목록 이름과 페이지 정보, 한도 경로, 후보 보존 | 반드시 60회 전에 끝남, 실제 결함 수, 새 모델의 읽기 순서 |
| `1793532481549` | 기존 review 분류 유지, 범위 한정 안내, P3-2 Server RPC 표현의 engine gap | 종전의 “놓친 4건”이 모두 사실이라는 판단 |

### 보고 품질 평가

1. 원안이 말한 놓친 4건은 각각 파일·범위·당시 버전·관찰 증거·반대 근거·기대 처분을 명시한 로컬 기준 사례로 만듭니다. 기존 실행이 게임 코드를 수정했으므로 현재 HEAD를 당시 소스와 같은 것으로 취급하지 않습니다.
2. 실제 결함뿐 아니라 올바른 RPC, 연결된 delegate, 의도된 수명 처리 등 비결함 사례를 함께 둡니다. 발견 후보 개수를 늘리는 방식으로 품질 목표를 대체하지 않습니다.
3. 같은 모델·프롬프트·설정·프로젝트 revision에서 반복 가능한 비교를 수행합니다. 조사 라운드, 재읽기, 목록 재호출, 전송 토큰, floor 구성, 보고 전달, 기준 사례 재현율, 잘못된 지적, 미확인 표시를 기록합니다.
4. 필수 수용 조건은 한도 준수, 재개방 금지, 후보의 처리 상태 보존, archive 호환성, 과도한 완료 표현 방지입니다. 평균 라운드 감소와 실제 결함 탐지 향상은 실측 결과로 보고합니다.
5. 위 원안 보완으로 “모든 조기 종료”와 “모든 결함 누락”이 해결된다고 약속하지 않습니다. 미검토 범위를 남기면서 일찍 보고하는 것은 유효한 결과일 수 있습니다.

병합·배포는 이번 요청의 구현 범위가 아닙니다. Opus가 코드 변경과 검사 결과를 준비한 뒤 사용자가 선택한 기존 배포 절차로 진행합니다.

## 7. 이번 재검토에서 수행한 확인

- 기준 작업 트리 소스를 격리된 `artifacts` 출력으로 TypeScript 빌드했습니다. 생성된 85개 파일이 기준 작업 트리의 dist와 같음을 확인했습니다.
- 분류·범위·엔진 claim 기존 테스트 27개, sizeHint·budget 관련 기존 테스트 18개가 통과했습니다. 이 결과는 아래 새 결함들이 기존 테스트로 포착되지 않았다는 한계도 보여 줍니다.
- finalization 재현 5개, scope/분류 경계 재현 3개, budget/archive 진단 4개를 실행했습니다. 현재 결함 또는 제안 회귀를 재현한 것이며 수정 완료 검사가 아닙니다.
- 생성물 4종 `--check`를 수행했습니다.
- 기존 revision 7의 엔진 참조 47개에 대해 5.8→5.7 대응 검사가 통과했습니다. 그중 4개는 주변 창 전체가 아닌 인용 줄만 같은 결과였습니다. 신규 Server RPC 사실은 아직 계약 파일에 구현하지 않았습니다.
- 근거 패킷은 네 개의 작은 묶음으로 구조·기록 일관성을 검사했고 구조 오류는 없었습니다. P1 네 항목에는 framework 근거가 필요한지 확인하라는 일반 안내가 남습니다. 이 항목들은 외부 framework 동작이 아닌 저장소 자체의 분기·저장·입력 구성과 재현 결과를 근거로 합니다. 이 검사가 코드 주장의 사실성을 자동으로 인증하지는 않습니다.

검토 중 배제하거나 낮춘 주장은 다음과 같습니다.

- `sizeHint`가 프로젝트 구분 없이 저장된다는 설명은 부정확합니다. 현재 key는 프로젝트 등 identity를 반영하고 관련 격리 테스트도 존재합니다. 실행 수명·낡은 힌트·provider normalization은 별도 조건으로 검사합니다.
- pinned working set이 언제나 35,789자라는 설명은 부정확합니다.
- `prior_turn`과 보고 보완용 fact의 해제 함수 부재만으로 독립적인 실행 결함을 확정하지 않습니다.
- `reportScopeGaps`에 투영된 history를 직접 넣으면 구현 범위 gap이 사라지는 합성 결과가 있지만, 현재 production 호출자는 원본 visible transcript를 전달합니다. 이 결과는 향후 ledger 통합 시의 회귀 조건이며 현재 실측 실패 원인으로 채택하지 않았습니다.
- 목록 재호출·고정 byteBudget·floor 증가에는 모델의 선택, 입력 캐시, provider 고정비 등 여러 요인이 있습니다. 한 문구나 모듈을 단일 원인으로 확정하지 않습니다.
