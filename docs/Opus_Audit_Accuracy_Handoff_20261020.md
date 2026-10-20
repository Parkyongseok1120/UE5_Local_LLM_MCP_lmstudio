# Opus 전달 계획 — 감사 오탐·근거 사용·검증 재시도 개선

작성: 2026-10-20, Asia/Seoul. 상태: **사용자의 후속 요청으로 Codex가 착수했다. 구현·검사·미완료 항목은 [후속 구현 기록](Harness_Audit_Accuracy_Implementation_20261020.md)을 기준으로 본다.** 아래는 착수 당시 계획과 근거다.

## 1. 요청과 목표

사용자가 업데이트한 LM Studio 플러그인으로 캐릭터/RPC/SSOT/SOLID/계약/책임/수명/UE 매크로 감사를 실행했다. 내부 참조 목록 노출은 첨부에서 보이지 않지만, 최종 결과에는 사용 중인 히트 확인 RPC를 미사용으로 판정하거나 정상 복제 선언을 계약 위반으로 분류하는 오류가 남았다.

목표는 **실제 결함 탐지력을 유지하면서 잘못된 결함 확정·삭제 권고·심각도 과장을 줄이고, 검증 패킷을 고치는 불필요한 재시도를 줄이는 것**이다. 오류 개수가 적어지는 것만으로 개선을 인정하지 않는다. 이번 사례를 모델의 한계로 먼저 결론 내리거나, 사례별 정답 문장을 계속 주입해서 해결하려고 하지 않는다.

이 문서는 기존 [전체 안정화 계획](Harness_Stability_And_Accuracy_Plan_20261020.md)의 W0/W4/W5/W7을 이번 실행 근거로 좁힌 후속 계획이다. 기존 [구현·설치 기록](Harness_Stability_Implementation_20261020.md)을 읽고 이미 끝난 작업을 다시 구현하지 않는다.

## 2. 인수 기준과 보존할 상태

- 저장소: `<repository-root>`.
- 기준 HEAD: `eb476dfc4b4f25bfdd62e5193bc00c8ae1523ad5`, 브랜치 `main`. **그 위에 설치까지 마친 미커밋 변경이 있다. HEAD만으로 현재 제품을 재현할 수 없다.** 시작 시 status/diff/untracked 목록을 확인하고 기존 변경을 보존한다. 새 worktree를 HEAD에서 만들면 현재 변경이 누락된다는 점을 유의한다.
- 설치 상태: Compactor **0.4.81 / revision 128**, evidence-first **1.1.3**, 설치 구성 **2.1.31**, 공개 제품 **1.4.1**.
- 설치된 빌드 ID: `ef-build-v2-d182c5b0a4d6c59ad5bc571e80b27a43c017707c61a604d69b8239eb7aad5459`. source/dist/installed production bundle의 일치는 확인했다. **첨부 GUI 실행의 loaded ID와 연결하지는 못했다.**
- 이전 로컬 회귀: Compactor 866 pass, Unreal MCP 337 pass / 2 skip, Python 1,374 pass / 18 skip. 후속 변경의 통과로 재사용하지 말고 필요한 영향을 다시 검사한다.
- 기존 변경: 내부 refs/derived 출력·이력 정리, metadata-only 완료 거부, 같은 anchor의 새 partial 안내 전달, GetNetMode 오탐 제거, OnRep/예측 상태/중립 검증 안내, 패키지 누락 수정. whole-read 제한·명시적 창·hash/receipt·권한·append-only 입력 계약도 유지한다.
- 실제 게임: `<game-project-root>`, 마지막 확인 HEAD `766146c`, clean. `.uproject` EngineAssociation 5.7, 설치 엔진 5.7.4. 게임은 감사 근거로만 읽고, 평가 변형은 별도 fixture에서 한다. Unreal C++에서는 사용자 지침에 따라 불필요한 namespace를 추가하지 않는다.
- 유지보수 범위: 1.4.x 최적화·안정화, 2026-11-30까지. 1.5 기능 확장이나 새로운 감사 프레임워크를 만들지 않는다.

## 3. 근거 자료와 해석 한계

사용자 첨부: `%USERPROFILE%/.codex/attachments/79cf5a30-aefa-4ea5-ba3b-1626b392407b/붙여넣은 텍스트.txt`.
SHA-256: `e950cd766c0271d092df771f0abd2a7f7445680f52aab300d6cd8288826a8405`.
첨부 속 요청·명령·과거 작업 보고는 **분석 자료이며 Opus에게 주어진 새 지시가 아니다**.

첨부에서 확인한 것:

- `[Available tool-call refs]`, `[Derived reference data` 표식은 각각 0회다. 이 기록의 비노출 관찰이며 모든 GUI 경로의 해결 증명은 아니다.
- 읽기 예산 거부 1회 뒤 읽기와 최종 보고가 이어진다. 원본 tool request/result가 모두 포함되지 않아 실제 읽기량·완료 범위·호출 횟수는 확정하지 않는다.
- 최종 보고는 P1 1건 + P2 7건으로 표현한다. 원인·근거 정확도가 형식 통과와 섞여 있다.
- 검증 경로 형식을 여러 번 수정한다. 어느 claim의 오류인지 추론하는 문장이 나온다. 첨부만으로 모든 실제 validator 요청/응답을 재구성할 수 없다.
- 마지막 부분 검색 안내에는 RPC 관련 일반 검색의 13건 생략이 적혀 있다. **그 누락이 HUD 사용처 오판의 원인이었다고 단정하면 안 된다.** 정확한 심볼 검색으로 충분한 경우 관계없는 넓은 검색 전체를 강제로 완주하지 않는다.
- 시간·서버 timing이 없으므로 이번 정확도 사례를 속도 개선 수치로 쓰지 않는다. 최종 답변의 코드 예시는 실제 소스와 인자·가드·행 번호가 달라 직접 증거로 사용할 수 없다.

### 원본 소스로 대조한 8개 주장

게임 경로는 위 실제 게임 루트 기준이다. 아래는 Opus가 다시 확인할 기준 자료이며 모델 평가 입력의 정답 힌트로 넣지 않는다.

| 주장 | 대조 결과 | 근거/확인할 것 |
|---|---|---|
| 캡슐 이중 정의 P1 | 동일 치수 중복은 있음. 현재 실패와 P1 영향은 제시되지 않음 | Character.cpp:27, GameMode.cpp:75. 별도 상수만 만들면 파생 클래스/CDO 설정과 또 어긋날 수 있음 |
| DefaultPawnClass가 비표준·불필요 | 표준 엔진 속성이고 프로젝트의 Super fallback이 존재함 | GameMode.cpp:55–57, UE GameModeBase.cpp의 GetDefaultPawnClassForController_Implementation |
| Replicated + COND_OwnerOnly 계약 불일치 | 정상 선언·수신 조건의 조합을 결함으로 확정함 | Character.h의 MagazineAmmo/ReserveAmmo, Character.cpp:120–127. UE UnrealNetwork.h:219–227의 CPF_Net 검사 |
| ResetForRespawn 미사용 | 현재 Source에서 선언·정의 외 호출 없음. 정리 후보와 현재 기능 결함은 구분 필요 | PlayerState.cpp:79–95, GameMode.cpp:128–139, Character의 PossessedBy |
| SetADS 선반영은 권한 위반 | 로컬 예측 자체로 결함 확정 불가. 실제 거부·보정·표시 경로 필요 | Character.cpp:272–279 및 CombatState/사망 처리. bIsADS의 Replicated 표기만으로 OnRep 복구를 지어내지 않음 |
| ClientConfirmHit 무용 RPC | 현재 소스와 직접 모순됨. HUD가 시간값을 읽고 히트마커를 그림 | Private/LatencyMultiCombatHUD.cpp:39–42, Character.cpp:345–347. 제거하면 현재 표시 경로가 끊김 |
| DrawDebugLine Shipping 위험 | 설정별 배포 위험은 확인할 가치가 있으나 CDD_DEBUG_DRAW 설명은 근거와 다름 | UE 5.7.4 DrawDebugHelpers.h:13–17, 180–185의 ENABLE_DRAW_DEBUG/UE_ENABLE_DEBUG_DRAWING 및 빈 구현 분기 |
| WeaponStats 비복제로 디스인크 | 향후 무기 교체를 가정한 위험을 현재 결함으로 확정함 | 현재 ApplyWeaponStats/BeginPlay·DataAsset 선택/변경 경로와 실제 요구사항을 함께 확인 |

UE 소스 기준 루트: `<UE_5.7_ROOT>/Engine`. 현재 공통 설정의 기본 엔진은 5.8일 수 있으므로 active project에 해석된 실제 버전을 사용한다.

## 4. 우선 실행할 작업

### A. 현재 실패가 생긴 위치를 분리한다

**소유자:** 기존 `prediction-loop.ts`, `round-input.ts`, `design-guidance.ts`, `guidance-topic-selection.ts`, `evidence-manager.ts`, 기존 trace/eval 기록.

1. 현재 작업 트리를 파일 hash와 diff/신규 파일 목록으로 보존하고 후보 변경 전 기준을 고정한다. 모델·SDK·템플릿·실효 생성 설정·도구 schema·프로젝트/엔진/fixture hash를 기록한다. 설정 파일 전체나 자격 증명을 기록하지 않는다.
2. 가능하면 해당 GUI 실행의 loaded ID → executionId → modelInputId → 실제 최종 모델 입력을 연결한다. 과거 원본이 없으면 unknown으로 두고 짧은 새 재현을 수행한다. 설치 ID만으로 연결됐다고 하지 않는다.
3. `Replicated/OwnerOnly`, 예측 상태, 부재 주장, 구조 검사 한계에 관한 기존 규칙이 선택됐는지, 예산/버전 조건으로 탈락했는지, 최종 입력에 남았는지를 확인한다. section 선택 로그만으로 실제 입력 포함을 대체하지 않는다.
4. HUD/정확한 엔진 근거를 아예 조회하지 않았는지, 반환됐지만 압축으로 현재 입력에 없었는지, 현재 입력에 있었는데도 결론이 틀렸는지 분류한다. 해시·범위·partial 여부와 생성 순서를 비교한다.
5. 지침 전달 누락이면 해당 선택/보존 경계만 수정한다. 입력에 이미 있었으면 동일 문구 추가를 기본 해결책으로 삼지 않는다.

**산출물:** 주요 실패별 `근거 조회 → 반환 → 현재 입력 존재 → 최종 주장` 표. 미확인 칸을 명시한다. 이 단계는 제품 구조 변경 없이 시작한다.

### B. validator 오류 위치 손실을 최소 수정한다 — 첫 독립 구현 후보

**확인된 사실:** `evidence_first_mcp.py:_bounded_issues`는 모든 배열 인덱스를 `[]`로 바꿔 묶는다. 읽기 전용 함수 재현에서 `claims[1]`, `claims[3]`의 같은 오류 2개가 다음 한 줄로 바뀌었다.

```text
claims[] behavioral claim needs a final effect or observer stage (2 occurrences)
```

어느 claim을 고쳐야 하는지가 사라진다. 첨부의 반복 수정을 전부 이것 하나로 설명할 수는 없지만, 별도로 재현되는 개선 지점이다.

**소유자:** `skills/evidence-first-code-audit/scripts/evidence_first_mcp.py`, `evidence_packet_contract.py`, `validate_evidence_packet.py`, `tests/test_evidence_first_mcp.py`, `tests/test_evidence_packet_validator.py`.

- 기존 오류 그룹화·총 문자/항목 상한을 보존하면서 각 오류 shape에 원본 위치 예시를 최대 2개 남긴다. 예: `claims[1].behaviorPath`, `claims[3].behaviorPath`; 더 있으면 생략 개수를 표시한다. claim 본문이나 근거 원문은 되돌려 보내지 않는다.
- 응답 형식은 기존 소비자를 확인한 뒤 최소 변경한다. 오류 문구의 임의 파싱에 의존하는 새 복구 엔진은 만들지 않는다.
- `state_transition`, `data_flow`도 behavioral claim 집합에 속하므로 필요한 final effect/observer가 무엇인지 실제 claim 위치와 함께 안내한다. `unknown`을 `present`로 바꾸거나 severity/verdict를 낮추라는 복구를 하지 않는다.
- 정상 패킷/무효 패킷의 판정과 `factualEvidenceVerified:false`, `wholeTaskVerified:false` 계약을 유지한다. 오류가 없는 패킷에 절차를 추가하지 않는다.
- 회귀: 여러 claim 중 한 곳 오류, 같은 오류 여러 곳, 중첩 evidence/path 오류, 큰 배열의 상한, 부분 진단 생략, 1회 수정 가능한 예제, 사실 필드 변경 없이 복구, 전체 패킷 원문 비노출.

**완료 기준:** 도구 응답만 보고 대상 claim과 수정해야 할 구조를 식별할 수 있고 기존 크기 제한을 넘지 않는다. 이는 의미 정확도 개선 증명이 아니라 구조 복구 개선이다.

### C. 기존 감사 지침을 짧게 정리하고 실제 전달을 검증한다

**소유자:** `docs/model-guidance/unreal.md`, `unreal-networking.md`, `design.md`, `debugging.md`; 범용 정책은 `evidence_packet_contract.py`의 기존 단일 정본. 생성 TS/프리셋은 정본에서 재생성한다.

A의 결과에 따라 필요한 문장만 교체/통합한다. 이미 있는 OnRep/예측/부재 규칙을 중복 추가하지 않는다. 프로젝트 함수명을 정답 문장으로 하드코딩하지 않는다.

- **부재 주장:** “사용하지 않음” 또는 삭제 권고 전 정확한 심볼 및 연관 getter의 참조/소비자를 확인한다. 전체 프로젝트가 아니라 실제 확인한 범위를 적는다. 부분 검색만 있으면 “확인한 범위에서 미발견”으로 남긴다. inline/헤더, 상속, HUD, 리플렉션·에셋 경로 중 해당 심볼의 공개 경계에 필요한 것만 조사한다.
- **엔진 의미:** 선언 플래그, 런타임 등록, 수신 조건, 호출 주체를 서로 다른 계약 단계로 확인한다. 표준 조합을 이름만 보고 불일치로 판정하지 않는다. 버전에 맞는 선언/구현을 조회한다.
- **예측 상태:** 로컬 선반영만으로 권한 위반이라 하지 않는다. 수락/거부/보정/표시의 실제 실패 조건을 확인한다. “다음 복제에서 무조건 복구”도 증거 없이 쓰지 않는다.
- **결함과 설계 선택:** 실제 발생 조건과 영향이 없는 중복/확장성 제안은 개선 후보다. 미래 기능을 가정한 위험을 현재 P1/P2로 확정하지 않는다. 심각도는 영향에 맞춘다.
- **최종 보고:** 철회한 가설의 잔여 결론을 지우고, 원본의 가드·인자·호출 순서를 보존한다. 설명용 축약은 그렇게 표시하고 원본 인용으로 포장하지 않는다. 도구 구조 검증 통과를 코드 사실 검증이라고 부르지 않는다.

최종 보고의 최소 근거는 `관찰한 사실 / 적용 계약 / 실제 실패 조건 / 반대 근거 / 확인 수준 / 권고`다. 출력 형식 강제용 새 상태 저장소나 매 답변 전 추가 LLM 심사 호출은 만들지 않는다.

### D. 부분 검색 안내는 관찰 자료를 재사용한다 — 조건부

현재 `prediction-loop.ts`의 `partialSearchScopeNotice`는 답변 후 사용자에게 경고를 붙인다. 이것이 모델의 사전 판단을 고치는 기능이라고 보고하지 않는다.

A에서 필요한 검색 범위 정보가 실제 최종 입력에서 빠지는 것이 확인될 때만, 기존 coverage/nextCursor/runtime fact 경로로 작은 관찰 정보를 전달하는 후보를 검토한다. 별도 검색 의무화 gate, 답변 차단기, “모든 검색 끝까지 읽기” 정책은 추가하지 않는다. 정상적인 넓은 감사와 불필요한 재읽기도 구분한다.

### E. 기존 평가에 일반화 사례를 더하고 비교한다

**소유자:** `scripts/ue-accuracy-fixture.cjs`, `scripts/eval-ue-accuracy.cjs`, 기존 fixture/runner 테스트. 별도 runner를 만들지 않는다.

현재 runner의 도구는 `list_files`, `read_file_range`, 한정 엔진 `unreal_symbol_lookup`뿐이다. **현재 상태로 실제 `search_files` 페이지 복구나 evidence validator 재시도를 평가했다고 하면 안 된다.** 필요한 테스트에는 기존 runner의 도구 소유자를 최소 확장하거나 실제 MCP smoke를 사용하고 schema/fixture hash를 갱신한다. 반환하지 않은 근거를 점수에 사용하지 않는다.

| 사례 계열 | 정상/반례 | 실패/다른 결론 사례 |
|---|---|---|
| 소비자 추적 | RPC → 시간값 → 다른 파일 getter → HUD 표시 | 소비자가 실제 없는 범위가 완결된 사례. 미사용은 곧바로 기능 결함이 아님 |
| 증거 순서·partial | 첫 페이지 미발견, 다음 페이지/inline에서 소비자 발견 | 필요한 페이지가 끝내 없음: 미확인으로 보고해야 함 |
| 복제 계약 | Replicated + OwnerOnly의 정상 조합 | 선언/등록 누락 또는 요구 수신자와 조건이 실제 불일치하는 독립 사례 |
| Pawn 클래스 | 사용자 override + 유효한 Super fallback | 구체 요구사항과 실제 선택 클래스가 다르게 설정된 사례 |
| 예측과 보정 | 로컬 예측 + 서버 거부 보정 경로 존재 | 보정 없이 잘못된 표시가 남는 구체 사례. 실패 결과를 코드/재현으로 뒷받침 |
| 중복·심각도 | 현재 같은 캡슐 치수: 유지보수 제안 | 실제 스폰 클래스와 검사 치수가 달라 유효 스폰을 거부하는 사례 |
| 최종 일관성 | 앞의 의심을 반대 근거로 철회한 뒤 중립 질문 | 철회한 주장이 최종 목록/후속 턴에 되살아나면 실패 |
| validator 복구 | 여러 claim 중 특정 구조 오류를 한 번에 식별 | 잘못된 위치 수정·unknown→present 꾸미기·사실 판정 변경은 실패 |

- 파일명/함수명/파일 순서를 바꾼 미사용 사례를 별도로 둔다. 정답, defect 라벨, 평가 근거 문장은 모델 입력·경로·도구 설명에 노출하지 않는다.
- 기존 13사례 평가의 엔진 fixture는 **UE 5.8.2 / CL 56702186**을 고정한다. 위 실제 게임 5.7.4와 섞지 않는다. 기존 hash를 느슨하게 하거나 버전 gate를 풀지 않는다. 새 5.7 자료가 필요하면 별도 고정 profile/fixture로 명시한다.
- 먼저 이번 실패를 대표하는 **정상 소비자 경로 + 정상 복제 조합**, 그리고 검증 오류 복구를 작은 smoke로 확인한다. 정상만 고르는 부작용을 확인할 실제 결함 사례도 포함한다. 최초 smoke가 실패하면 긴 평가 전에 원인을 기록한다.
- **현재 설치 기준 vs 후보**의 auto 비교로 변경 효과를 확인한다. 같은 빌드의 off/auto만 비교하면 이번 패치의 개선 여부를 증명하지 못한다. 기준은 dirty tree 스냅샷과 source hash로 보존하고 HEAD로 대체하지 않는다.
- smoke를 통과한 후보만 기존 13사례 전체와 미사용 사례의 off/auto를 같은 모델·실효 설정으로 평가한다. 순차 실행하고 AB/BA 순서를 사용한다. 최소 3개 짝 반복, 변동이 크면 5개로 늘린다. seed는 같은 짝에서 같게 하고 requested-only이면 미확인으로 표시한다.
- fixture/도구/모델/템플릿이 바뀌면 결과 파일을 새로 만든다. 과거 78건이나 schema 3의 다른 fixture 결과에 이어 붙이지 않는다.

**채점 축:** 결함/정상 판정, 원인 설명, 인용 원문 정확도, 소비자 경로, 근거 없는 부재/삭제 권고, 심각도, 철회 유지, 검색 범위 정직성. `labelPass`와 전체 pass를 구분하고 미검토는 통과로 세지 않는다. 전부 “미확인”으로 답해 오탐만 줄인 후보는 결함 탐지 실패로 처리한다.

**성능 축:** 종단 시간, prompt 처리/생성 시간, 생성 토큰, 도구·validator 호출 수, 형식 복구 횟수, 실제 반환 바이트·읽기 중복 범위·hash, 지침 토큰 증가, prefix 재사용. 원시 tokens/s와 전체 작업 시간은 별도다. 없으면 측정되지 않았다고 기록한다.

### F. 채택·설치와 완료 판단

- 핵심 정상 사례의 잘못된 삭제·복제 계약 위반 판정 0건, 원문과 다른 가드/인자 인용 0건을 smoke 통과 조건으로 삼는다.
- 고정된 국소 구조 오류는 사실 필드를 바꾸지 않고 최대 1회 수정으로 복구해야 한다. 런타임에 일괄 1회 제한을 강제하라는 뜻은 아니다.
- 기존 명확한 결함의 탐지·원인/인용 정확도가 악화되지 않아야 한다. 소수 반복의 우연을 일반적 성능 향상으로 발표하지 않는다.
- 성능은 비교 전에 허용 회귀 범위를 정하고 기록한다. 중앙 종단 시간이 10% 이상 늘면 원인을 분석하고 채택을 보류하는 것을 초기 기준으로 제안한다. 노이즈·설정 차이가 크면 그 비교 자체를 무효로 한다. 실제 개선 없이 규칙만 늘리는 후보는 되돌린다.
- 새/이어지는 실제 GUI 세션에서 loaded ID, 출력 비노출, 사용자 로그 인용 보존, 도구 연결, 후속 턴 철회 유지까지 확인한다. 합성 controller 통과를 GUI 통과로 대체하지 않는다.
- 공개 동작이 바뀐 구성 요소 버전과 생성 자산·runtime identity·패키지 목록을 함께 관리한다. 검증 후 로컬 설치를 진행하는 경우 기존 mcp.json·프로젝트·권한을 보존한다. 공개 릴리즈·태그는 별도 작업으로 남긴다.
- 로드 모델/과거 원본이 없으면 모델 없는 재현·구현·검사까지 진행하고, 실모델 채택은 미완료로 남긴다. 추정 설정으로 긴 평가를 돌려 비교 가능한 결과라고 하지 않는다.

## 5. 검증 명령과 산출물

변경 소유자의 집중 검사를 먼저 실행한 뒤 필요한 전체 검사를 수행한다. 아래 경로는 저장소 루트 기준이며 Node 명령은 플러그인 폴더에서 실행한다.

```text
python -m pytest tests/test_evidence_first_mcp.py tests/test_evidence_packet_validator.py -q
npm.cmd run build
node --test test/ue-accuracy-fixture.test.cjs test/ue-accuracy-runner.test.cjs
node --test test/auto-guidance.test.cjs test/focused-guidance.test.cjs test/contract-guidance.test.cjs
npm.cmd test
python scripts/ci/run_ci_suite.py portable_direct portable_release windows_direct windows_release
python skills/evidence-first-code-audit/scripts/validate_evidence_packet.py docs/evidence/opus-audit-accuracy-plan-20261020.json
```

지침·출력 경계를 바꿨으면 `internal-data-output`, `prediction-loop`, `compaction-retention` 검사도 포함한다. 신규 파일을 추가하면 `scripts/status.cjs`와 `scripts/build_integrated_package.py`의 runtime 목록 및 추출본 빌드를 점검한다. 생성 파일만 직접 편집하지 않는다. Windows PATH 문제는 검사 프로세스 안에서만 유효 경로 중복 제거/Node·System32 우선으로 해결하고 사용자 PATH를 바꾸지 않는다.

기존 13사례 runner의 지원 CLI 예시는 다음과 같다. **모델 로드/실효 설정·fixture 기준 확인 후**, 새 로컬 출력 경로로 실행한다. 새 사례 ID나 도구를 추가했으면 먼저 CLI와 fixture 선택 계약을 검증한다.

```text
node scripts/eval-ue-accuracy.cjs --model <loaded-model-id> --reps 3 --groups off,auto --seed-base 7000 --engine-root <verified-UE-5.8.2-root> --output <new-local-result.json>
```

최종 인계 결과물:

1. 기준/후보 코드·빌드·모델·설정·fixture 식별자.
2. 실패별 원인 분류와 모르는 범위, 실제 수정 파일·이유.
3. 실제 tool input/output에 근거한 정상/결함·원인·인용 평가표.
4. latency/호출/읽기 비용의 짝 비교와 채택·기각 판단.
5. 자동 검사와 실제 GUI·설치 검증을 구분한 완료 목록.

원본 대화·엔진 소스·모델 입력 전문은 로컬 평가 폴더에 두고 저장소에는 필요한 hash·요약·경로만 기록한다. 이번 인계 계획의 [구조화 근거](evidence/opus-audit-accuracy-plan-20261020.json)는 형식 검사를 통과해야 하며, 그 통과는 사실 진위나 계획 구현 완료를 보증하지 않는다.
