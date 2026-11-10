# 에디터 구조 확인 하네스와 비율 기반 컨텍스트 관리 — 수정 계획 (2026-11-10)

- **상태:** 구현 전 계획. 설치·배포 보고가 아니다.
- **진행 기록:** [1단계 구현 및 자동 테스트](Harness_Context_Budget_Stage1_Implementation_20261110.md). 실제 모델 검증과 설치·병합은 별도이며, 아래 계획의 모든 인수 조건이 완료된 상태는 아니다.
- **후속 기록:** 1단계의 32K/84K 실제 모델 사례와 잔여 handler 검증을 추가했고, [2단계 규칙 조회](Harness_Project_Rules_Stage2_Implementation_20261110.md)를 구현했다. 설치와 에디터 실측, 3단계 이후는 별도다.
- **기준:** `Develop` `3116c07`. 구현을 시작할 때 브랜치와 작업 트리를 다시 확인한다.
- **대체 대상:** 「Fable 5.2 전달용 — 에디터 구조를 확인하는 개발 하네스와 비율 기반 컨텍스트 관리」(이하 원안).
- **근거:** [계획 검토](Harness_Editor_Authoring_And_Context_Plan_Review_20261110.md)의 14개 항목.
- **원안에서 그대로 가져오는 부분:** 4절(규칙 일반화 기준), 5.2–5.4절(변경별 사전 관찰, 관찰 기록, 변경 결정), 6절(Unity 작업), 7절(Unreal 작업), 13절(실패 복구). 이 문서는 달라지는 부분과 새로 정한 결정만 적는다.

## 1. 목표
1. 작업 대상과 적용 규칙을 식별하고, 영향받는 에디터 구조와 설정의 정본을 확인한 뒤 최소 변경한다.
2. 연결, 저장, 재로드, 실행을 따로 검증하고 따로 보고한다.
3. 소유 관계와 기각한 가설을 압축 뒤에도, 다음 사용자 턴에도 유지한다.
4. 컨텍스트는 로드된 세션 한도 C의 비율로 관리한다. 설정한 비율과 실제 적용값이 다르면 그 사유를 보여 준다.

하지 않는 것: 새 writer, 새 에디터 브리지, 새 근거 archive, 두 번째 예산 계산기, Human-Bartender 전용 수치의 공통화, 생성 파일 수동 편집.

## 2. 구현 전에 확정한 결정

### D1. 예산 방식의 기본값은 `legacy_auto`다
- 새 설정 `contextBudgetMode = percentage | legacy_auto | absolute`를 추가한다.
- 설정에 버전 필드가 없고 저장하지 않은 필드는 스키마 기본값이 반환된다. 그래서 스키마 기본값이 곧 기존 사용자의 동작이다.
- 첫 릴리스의 기본값은 `legacy_auto`다. 기존 `workingInputScaling`의 `auto`/`fixed`는 그대로 `legacy_auto`/`absolute`의 의미로 읽는다. 사용자는 `percentage`를 직접 고른다.
- 기본값을 `percentage`로 바꾸는 것은 7단계 실측 뒤의 별도 결정이다.
- `contextManagementMode = legacy`와 `percentage`의 조합은 지원하지 않는다. 이 경우 `legacy_auto`로 동작하고 그 사실을 디버그 정보에 표시한다.

### D2. 출력과 안전 여유는 실행마다 한 번 해석한다
- 문제: `maxOutputReserve`와 `safetyMarginTokens`는 고정 설정값이고 12곳에서 직접 읽는다.
- 결정: `budget-broker.ts`에 순수 함수 `resolveSessionBudget(config, contextLength, source)`를 둔다. 결과는 요청 비율, 해석된 토큰, 조정 사유, C의 출처다.
- 실행 시작에서 `tokenSource`를 얻은 직후(`prediction-loop.ts:94` 부근) C를 한 번 읽어 이 함수를 부른다. 결과의 출력·안전 토큰을 넣은 **유효 설정**으로 `config`를 바꾼다. 기존 12개 지점은 `config.maxOutputReserve`를 그대로 읽으므로 고치지 않는다.
- `readConfig`(`:77`)와 이 지점 사이의 코드가 예산 값을 쓰지 않는다는 것을 테스트로 고정한다.
- 실행 도중 측정된 C가 해석 당시와 다르면 `context_length_changed`를 기록한다. 압축 기준은 매 측정의 C로 계산하므로 바로 따라간다. 출력 상한은 다음 실행에서 다시 해석하고, 그때까지는 기존 `resolveGenerationBudget`의 headroom 제한이 초과를 막는다.
- C를 못 읽으면 `assumedContextLength`로 해석하고 출처를 `configured_fallback`으로 표시한다.

### D3. 퍼센트 방식의 계산식

```text
C = 측정된 세션 컨텍스트 한도
O = min(outputMaxTokens, max(min(4096, floor(C × 25%)), floor(C × 출력%)))
S = max(512, floor(C × 안전%))
H = C − O − S
R = 도구 결과 예약 (기존 식: 읽기 도구가 있으면 max(최소 읽기, min(4096, H/8)))
Q = 도구 요청 여유 (기존 식: min(O, 2048))
high = min(floor(C × 시작%), H − R − Q)
low  = max(F, min(floor(C × 목표%), high − R))        F = 필수 입력 실측
```

- 초기값: 목표 55%, 시작 75%, 출력 10%, 안전 2%, `outputMaxTokens` 16384. 실측 전 제안이다.
- 출력 하한 `min(4096, C의 25%)`는 추론 모델의 보고가 잘리는 것을 막는다. C=32,768에서 10%는 3,276이지만 하한으로 4,096이 된다.
- `high − R`은 명시적인 재압축 방지 간격이다. 압축 직후 도구 결과 하나가 들어갈 공간을 남긴다. 기존의 `high × 0.75`는 이 방식에서 쓰지 않는다.
- F가 `floor(C × 목표%)`보다 크면 `target_unreachable`을 표시하고 기존 복구(본문 범위 축소, 선택적 지침 감소, 도구 스키마 축소)를 쓴다. F가 H를 넘으면 모델을 호출하지 않는다.
- 비율이 조정되면 사유를 기록한다: `tool_result_reserve`, `hysteresis`, `mandatory_floor`, `output_floor`, `output_cap`, `safety_floor`.
- 저장된 사용자 비율은 조정 결과로 덮어쓰지 않는다.
- 검증: 목표율 ≥ 시작률, 음수, NaN, 100% 이상은 거부하고 기본값으로 동작한다. 비율은 소수 한 자리까지 받는다. 기존 `numeric()`은 정수로 자르므로 비율용 정규화 함수를 따로 둔다. 스키마는 SDK의 `precision`과 `slider` 옵션을 쓴다.

계산 예 (읽기 도구 있음):

| C | O | S | high | low(F 무시) | 조정 |
|---|---|---|---|---|---|
| 8,192 | 2,048 | 512 | — | — | 도구 스키마(실측 5,689)가 H=5,632를 넘어 호출하지 않음 |
| 32,768 | 4,096 | 655 | 22,467 (68.6%) | 18,022 | `output_floor`, `tool_result_reserve` |
| 84,224 | 8,422 | 1,684 | 63,168 | 46,323 | 없음 |
| 131,072 | 13,107 | 2,621 | 98,304 | 72,089 | 없음 |
| 262,144 | 16,384 | 5,242 | 196,608 | 144,179 | `output_cap` |

C=84,224에서 현재 `auto`의 적용값은 high 63,626, low 47,719다. 위 값과의 차이는 −458, −1,396 토큰이다. 이 비교를 회귀 테스트로 고정한다.

### D4. 남은 공간 절대 기준은 퍼센트 방식에서 다음 호출 적합성으로 바꾼다
- 대상 지점은 셋이다.
  - `shouldCompactContext`의 `core.shouldCompact`(`context-manager.ts:191`, `direct-compaction-core.js:724`)
  - `selectSoftCompaction`의 목표 계산(`context-manager.ts:202-204`)
  - hard 판정(`context-manager.ts:897`)
- 퍼센트 방식에서는 `remaining <= softRemainingTokens` 대신 기존 `nextActionFit`이 거짓일 때를 쓴다. hard 판정도 같다. soft 목표는 `hardInputCeiling − low`를 쓴다.
- 측정이 정확하지 않을 때의 메시지 수 기준은 그대로 둔다.
- `legacy_auto`와 `absolute`에서는 지금 동작을 유지한다.

### D5. 목표 판정과 선택적 지침 입장은 실제 적용값을 쓴다
- `targetMet`(`prediction-loop.ts:611`)과 `mandatoryFloorExceedsTarget`(`round-input.ts:609`)은 설정 원시값 대신 해당 라운드의 `low`와 비교한다. 세 방식 모두에 적용한다.
- 텔레메트리(`evidence-telemetry.ts:27-30`)는 요청값과 적용값을 따로 기록한다.
- 압축한 라운드에서 선택적 지침의 입장 상한은 그 압축이 쓴 `low`다. 지금은 입장 계산이 필수 입력 0으로 기준선을 다시 만들어서, 압축 직후 최종 입력이 `low`를 넘는다(실측 48–53.6K, low 47.7K). 압축에서 계산한 기준선 객체를 입장 계산에 넘겨 같은 값을 쓰게 한다.
- 이 항목은 동작이 바뀌므로 D3과 별도 커밋으로 낸다.

### D6. 요청·적용 토큰은 채팅에 표시한다
- 설정 스키마의 설명은 고정 문자열이다. 설정 화면에는 비율의 의미만 적는다.
- 실행마다 한 번 디버그 정보 `context_budget`에 요청 비율, 해석된 토큰, 적용 비율, 조정 사유, C의 출처를 기록한다.
- 방식을 바꾸기 전 미리보기도 같은 이벤트로 제공한다. `legacy_auto`로 동작하는 동안 "percentage였다면"의 값을 함께 기록한다.

### D7. 사전 검사는 provider와 플러그인으로 나눈다
- provider가 검사하는 것: 자신이 발급한 대상 관찰 receipt의 신선도, 프로젝트·대상·버전 일치, 보호 경로. 모든 호스트에 적용된다.
- 플러그인의 도구 경계가 검사하는 것: 구조나 설정에 영향을 주는 쓰기 요청이 현재 변경 결정의 근거 대상과 이어지는지. LM Studio에서만 적용된다.
- 모델이 적은 근거 ID나 hash는 어느 쪽에서도 조건 충족으로 인정하지 않는다.
- 플러그인 쪽 검사는 observe 방식으로 시작한다. 거부하지 않고 다음 입력에 관찰 보충 안내를 넣는다. 오탐을 측정한 뒤 결정적인 규칙만 거부로 올린다.

호스트별 보장:

| 경로 | provider 검사 | 결정 연결 검사 |
|---|---|---|
| LM Studio + 이 플러그인 | 적용 | 적용 |
| 같은 MCP를 쓰는 다른 호스트 | 적용 | 없음(지침만) |
| MCP를 거치지 않는 shell·패치 | 없음 | 없음 |

### D8. 규칙 문서는 요청 시 조회하고 본문을 항상 주입하지 않는다
- 조회 경계는 기존 `shared-tool-core/git.js`가 구하는 `gitRoot`와 workspace root 사이, 그리고 workspace 안의 작업 경로 상위 폴더다. 그 밖은 읽지 않는다.
- 대상 파일은 `AGENTS.md`다. 파일당 64KB까지 읽고 넘으면 잘림으로 표시한다.
- 새 도구(제안 이름 `project_rules`)는 기본으로 목차(제목, 줄 범위), 파일 hash, 크기, 적용 경로를 돌려준다. 본문은 제목이나 줄 범위로 요청하고 기존 `byteBudget` 계약을 따른다.
- 플러그인은 규칙이 있다는 사실만 runtime fact 한 줄(400자 이하)로 넣는다. 경로, hash, 크기, "구조나 설정을 바꾸기 전에 조회하라"는 안내다. 프로젝트나 hash가 바뀌면 교체한다.
- Unity provider는 같은 공용 기능을 쓴다. `project.js`의 Assets/Packages/ProjectSettings 정책은 바꾸지 않는다.
- 문서 안의 외부 명령, 비밀 전송, 권한 확장 지시는 실행하지 않는다. 모델 요약이나 RAG 내용은 프로젝트 규칙으로 승격하지 않는다.

### D9. 검토 기록을 턴 너머로 보존한다
- `ReviewRegister`에 `toJSON`/`load`를 추가하고, 읽기 장부와 같은 자리(`working-context.js`의 저장 상태)에 저장한다.
- 복원한 행은 이전 턴의 것으로 표시한다. 기각(cleared) 행은 관찰 당시 버전과 함께 유지한다. 발견(found) 행은 근거 파일의 현재 버전이 다르면 기존 `attach()` 규칙대로 재확인 필요가 된다.
- 프로젝트가 바뀌면 복원하지 않는다. 행 수 상한은 지금 값을 유지한다.
- 같은 버전에서 기각된 가설이 새 근거 없이 다시 발견으로 기록되면 "이전에 기각됨"을 행에 표시한다. 재검토 자체는 막지 않는다.

### D10. 구조 결정 기능의 활성 조건은 그대로 둔다
- 구조 결정 컨텍스트는 지금처럼 `designGuidanceMode = auto`일 때만 켜진다. 입력 토큰을 쓰는 기능이라 같은 스위치에 둔다.
- 규칙 안내(D8), provider 검사(D7), 보고 상태 구분, 검토 기록 보존(D9)은 이 스위치와 무관하게 동작한다.
- 이 조건을 설정 설명과 문서에 적는다.

### D11. `prediction-loop.ts` 공간을 먼저 만든다
- 현재 111,896바이트이고 상한은 112,000바이트다.
- 2단계 첫 커밋에서 `direct_context_measurement` 이벤트의 payload 조립을 `evidence-telemetry.ts`로 옮긴다. D5의 판정 값도 그 함수가 계산한다.
- 이후 단계도 이 파일에는 호출만 둔다.

## 3. 구조 결정의 Unity 확장
- `architecture-observation.ts`를 엔진별 어댑터로 나눈다. Unreal은 지금의 소스 어휘 검사를 유지한다.
- Unity 관찰은 소스 어휘가 아니라 Bridge 결과에서 만든다. `unity_object_read`, `unity_prefab`, `unity_references` 결과를 `ArchitectureEvidenceRef`로 바꾼다.
  - `sourceKey`: 자산 GUID와 객체 식별자
  - `observedVersion`: Bridge receipt의 세션과 domain generation
  - `applicability`: receipt가 유효하면 `same_observed_version`, domain reload나 Play 종료 뒤에는 `stale`
- Unity facet 후보: `prefab_source`, `instance_override`, `serialized_reference`, `layout_owner`, `runtime_overwrite`. Bridge가 실제로 돌려주는 필드를 0단계에서 확인한 뒤 확정한다.
- Unreal 패턴 카드는 Unity에 주입하지 않는다. Unity는 1차로 패턴 카드 없이 결정 형식만 쓴다.
- 결정 2개 한도는 유지한다. `DecisionBody`에 수정 대상(code, definition asset, template source, scene instance, runtime state)과 보존할 값을 적을 자리를 추가한다. 기존 저장본과의 호환은 note 계약 버전으로 처리한다.

## 4. 단계와 완료 조건

| 단계 | 작업 | 완료 조건 |
|---|---|---|
| 0 | 기준 실측과 조사 | 아래 0단계 항목 |
| 1 | 퍼센트 예산 (플러그인만) | D1–D6, D11. 아래 예산 검증 통과 |
| 2 | 규칙 조회 | D8. 상위 저장소 규칙이 전달되고 다른 프로젝트로 새지 않음 |
| 3 | 검토 기록 보존과 보고 상태 | D9. 후속 턴에서 기각 가설과 소유 사실이 남음. 보고가 상태를 구분함 |
| 4 | Unity 관찰과 결정 연결 | 3절. 기존 UI·설정·프리팹 재사용, 기본값이 아닌 값 보존 |
| 5 | Unreal 관찰 공백 | 원안 7절. 맵 목록과 실제 계층을 구분해 표시 |
| 6 | 사전 검사 | D7. observe로 오탐 측정 후 결정적 규칙만 거부 |
| 7 | 통합 검증과 패키징 | 아래 검증 표. 기본값 전환 여부 결정 |

원안과 달라진 순서: 퍼센트 예산을 가장 먼저, 독립 변경으로 낸다. 검토 기록 보존(3)은 에디터 관찰과 무관하므로 앞으로 당겼다.

**0단계 항목**
- `3116c07` 빌드로 기존 감사 프롬프트 두 개를 다시 실행해 기준을 잰다: 라운드 수, 재읽기, 압축 횟수, `floorComposition`, 투영 절감량, 압축 직후 입력.
- Unity Bridge가 계층·원본·override를 어디까지 돌려주는지 표로 만든다.
- Unreal exporter의 엔진 버전별 관찰 가능 범위를 표로 만든다.
- 감사 사례 `1794306949131`에서 실패 사례와 정상 대조 사례를 고른다.
- 설치된 SDK에서 설정 스키마의 `precision`과 `slider`가 실제로 동작하는지 확인한다.

## 5. 변경 위치

| 묶음 | 파일 | 책임 |
|---|---|---|
| 예산 설정 | `config-defaults.ts`, `direct-config.ts`, `execution-config.ts`, `execution-contracts.ts` | 새 설정 4개와 방식 선택, 비율 정규화 |
| 예산 계산 | `budget-broker.ts` | `resolveSessionBudget`, 퍼센트 기준선. 유일한 계산 지점 |
| 압축 시작 | `context-manager.ts`, `direct-compaction-core.js` | D4, D5의 입장 상한 |
| 판정·계측 | `evidence-telemetry.ts`, `round-input.ts` | D5, D6, D11 |
| 규칙 | `shared-tool-core/git.js`, `workspace.js`, 각 provider catalog | D8 |
| 검토 기록 | `review-register.ts`, `working-context.js` | D9 |
| 구조 결정 | `architecture-*.ts` | 3절 |
| Unity | `lmstudio-unity-mcp/src/`, `unity-editor-bridge/Editor/` | 원안 6절 |
| Unreal | `tools/ue_export/`, `LmStudioGraphExporter`, `editor_export_contract.py` | 원안 7절 |
| 사전 검사 | provider 쓰기 경계, `tool-boundary.ts` | D7 |

## 6. 검증

**예산 (1단계)**
- 위 계산 예 다섯 가지 C에서 기준선과 조정 사유가 표와 같다.
- C=84,224에서 `legacy_auto`와 `percentage`의 차이가 high 458, low 1,396 토큰이다.
- 12개 출력 상한 지점이 해석된 값을 쓴다. 최종 보고, 이어쓰기, 추론 복구(2배), 조사 복구 각각을 실제 handler로 확인한다.
- 남은 공간이 6000 아래여도 `nextActionFit`이 참이면 압축을 시작하지 않는다(퍼센트 방식). `legacy_auto`에서는 지금처럼 시작한다.
- 압축한 라운드의 최종 입력이 `low` 이하이거나 초과 사유가 기록된다.
- `targetMet`이 적용값과 비교한다.
- 잘못된 비율, 소수 비율, 목표 ≥ 시작, C 미확인, 실행 중 C 변경, `legacy`와의 조합.
- 저장된 `auto`/`fixed` 설정이 방식 추가 뒤에도 같은 기준선을 낸다(기존 `budget-scaling`, `budget-broker-v3/v4` 테스트 유지).
- C=32,768에서 추론 모델로 최종 보고가 잘리지 않는지 실제 실행으로 확인한다.

**그 밖의 단계**: 원안 12절의 사례 표를 그대로 쓰고 다음을 더한다.
- 규칙: 12KB 문서에서 목차만 기본 반환, 64KB 초과 잘림, 필수 입력 증가가 400자 이하.
- 검토 기록: 후속 턴에서 기각 행 유지, 파일이 바뀌면 재확인 필요, 프로젝트 전환 시 복원 안 함.
- 사전 검사: 호스트별 보장 표의 세 경로 각각.

**공통**
- 플러그인 전체 테스트와 생성물 검사 4종, Unreal agent·Unity provider 테스트.
- 실제 에디터 저장·재로드와 실제 모델 실행 결과는 fixture·replay 결과와 따로 적는다.
- 이 환경에서만 실패하는 기존 테스트는 `main` 기준선과 비교해 구분한다.

## 7. 배포와 되돌리기
- 예산 방식은 설정 하나로 되돌린다. `legacy_auto`가 기본값이므로 새 빌드를 설치해도 동작은 바뀌지 않는다.
- 검토 기록 저장본에는 형식 버전을 둔다. 이전 빌드는 이 필드를 무시한다.
- 구조 결정 형식을 바꾸면 note 계약 버전을 올리고, 구 버전 저장본은 읽기만 한다.
- 사전 검사는 off, observe, enforce 순서로 올린다. 기존 권한·경로·CAS 보호를 끄는 스위치로 만들지 않는다.
- 병합, 설치, 재시작은 구현 완료와 별도로 기록한다.

## 8. 남은 미확인
- 감사 사례 `1794306949131`의 내용.
- Unity Bridge와 Unreal exporter가 실제로 돌려주는 필드(0단계에서 확인).
- 초기 비율 값이 품질과 지연에 주는 영향(7단계 실측).
- 설정 화면에 동적 값을 표시할 다른 방법이 SDK에 있는지.
