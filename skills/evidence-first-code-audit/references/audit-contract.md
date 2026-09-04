# 검토 근거를 기록하는 규칙

주요 주장마다 무엇을 확인했고 어디까지 증명됐는지 남겨야 합니다. 아래는 저장 호출이 빠진 상황을 설명하기 위한 가상 예시입니다. 필드 이름과 분류 값은 검사기가 읽으므로 그대로 사용하고 설명은 한글로 씁니다.

```json
{
  "claim": "갱신 요청을 받지만 바뀐 상태를 저장하지 않습니다.",
  "claimType": "wiring",
  "verdict": "Bug",
  "severity": "P0",
  "proofLevel": "SourceVerified",
  "evidence": [
    {
      "kind": "project_source",
      "location": "src/api/update_handler.py:42",
      "observation": "요청을 검사한 뒤 저장 함수를 호출하지 않고 반환합니다."
    }
  ],
  "behaviorPath": [
    {"stage": "entry", "stageStatus": "present", "location": "src/api/routes.py:18", "symbol": "update_resource"},
    {"stage": "decision", "stageStatus": "present", "location": "src/api/update_handler.py:35", "symbol": "validate_update"},
    {"stage": "side_effect", "stageStatus": "expected_missing", "location": "src/state/store.py:24", "symbol": "store.save"},
    {"stage": "observer", "stageStatus": "present", "location": "src/api/update_handler.py:42", "symbol": "요청 수락 응답"}
  ],
  "counterEvidence": [
    {
      "kind": "project_source",
      "location": "src/jobs/batch_update.py:67",
      "observation": "대응되는 일괄 갱신 경로에서는 store.save를 명시적으로 호출합니다."
    }
  ],
  "unknowns": ["중간 처리 함수와 운영 환경 실행 기록은 제공되지 않습니다."]
}
```

## 주장 유형 분류

`claimType`은 반드시 하나를 선택합니다. 선택적인 참·거짓 필드로 대신하지 말아야 합니다.

| 값 | 뜻 |
|---|---|
| `existence` | 함수·자료·서비스·설정이 존재합니다. |
| `behavior` | 입력이 관찰 가능한 결과를 만듭니다. |
| `framework_semantics` | 언어·라이브러리·실행 환경이 특정 규칙대로 동작합니다. |
| `wiring` | 선언하거나 만든 기능이 실제 상태 변경·외부 결과에 연결됩니다. |
| `state_transition` | 상태의 시작·종료·복구·취소·재진입이 올바릅니다. |
| `data_flow` | 정의한 자료가 실제 사용처와 출력까지 전달됩니다. |
| `architecture` | 소유권·의존성·생성 및 종료·경계에 관한 사실입니다. |
| `codegen` | 제안한 코드 변경이 요구나 필수 조건을 만족합니다. |

## 판단과 심각도

판단은 `Bug`(오류), `ByDesign`(의도된 동작), `Ambiguous`(판단 불명확), `NeedsRuntimeProof`(실행 확인 필요)로 구분합니다. 심각도는 아래 기준으로 정합니다.

| 값 | 영향 |
|---|---|
| `P0` | 핵심 기능 불능, 상태 손상, 자료 손실이나 치명적 보안 문제에 해당합니다. |
| `P1` | 주요 흐름이 일반적인 조건에서 실패하거나 일관되지 않습니다. |
| `P2` | 드물게 발생하는 오류나 유지보수·확장 문제에 해당합니다. |
| `P3` | 표현 방식이나 선택적인 개선에 해당합니다. |

구조 검토에서 확인된 현재 사실을 중립적으로 적을 때만 `Confirmed`와 `Info`를 함께 사용할 수 있습니다. 오류 등급이나 설계 의도의 증명이 아닙니다. `codegen`에는 사용할 수 없고 확인 수준도 `Proposed`이면 안 됩니다. 두 값을 다른 판단·심각도와 섞지 말아야 합니다. 동작 경로에 `unknown`이 있으면 `Ambiguous` 또는 `NeedsRuntimeProof`를 사용합니다.

## 근거 종류와 확인 수준

| `kind` | 뜻 |
|---|---|
| `requirement` | 사용자 요청, 요구사항, 자료 형식이나 완료 조건입니다. |
| `project_source` | 저장소 소스·설정·자료 이전 코드·명시된 규칙입니다. |
| `framework_source` | 언어·라이브러리·실행 환경의 직접 구현입니다. |
| `official_docs` | 해당 버전에 맞는 공식 문서입니다. |
| `static_analysis` | 자료형·정적 분석·형식 검사 결과입니다. |
| `build` | 컴파일·연결·배포 묶음 생성 결과입니다. |
| `test` | 재현 가능한 자동 테스트 결과입니다. |
| `runtime` | 실제 실행 로그·추적·측정·디버거 관찰입니다. |
| `generated_metadata` | 자동 구조도·호출 관계·언리얼 등록 정보·검색 목록입니다. |

자동 생성 자료는 생성 방식이 해당 관계를 증명하지 않는 한 참고용입니다. 이전 AI 답변이나 실패 기억을 확정 근거로 쓰지 말아야 합니다.

`SourceVerified`에는 소스나 공식 문서가 필요합니다. `StaticVerified`, `BuildVerified`, `TestVerified`, `RuntimeVerified`에는 각각 `static_analysis`, `build`, `test`, `runtime` 근거가 필요합니다. `Proposed`는 요구나 소스를 사용할 수 있지만 검증된 동작처럼 설명하지 말아야 합니다.

## 실제 동작 경로

`behavior`, `wiring`, `state_transition`, `data_flow`는 아래 시작·중간·결과를 모두 추적합니다.

1. `entry`: 입력, 공개 함수, 사건, 콜백, 작업이나 메시지가 들어오는 곳입니다.
2. `decision` 또는 `dispatch`: 검사·분기·변환·정책 판단·다음 호출이나 대기열 전달을 확인합니다.
3. `mutation`, `side_effect` 또는 `observer`: 상태 변경·저장·외부 호출·사건 발행·화면 출력·구독자·결과 검사를 확인합니다.

`stage`는 위 여섯 값만 원인 순서대로 사용합니다. 각 단계의 `stageStatus`도 필수입니다. `present`는 근거로 확인한 단계, `expected_missing`은 필요하지만 없거나 도달하지 못한 단계, `unknown`은 아직 확인하지 못한 단계입니다. 생성이나 변환은 가까운 유효 단계의 `symbol`과 근거에 설명하며 단계 이름을 새로 만들지 말아야 합니다.

사건 전달기, 부모 호출, 대기열, 요청 객체, 의존 객체 생성에서 추적을 끝내지 말아야 합니다. 주장한 최종 결과가 그 자체인 경우에만 거기서 끝낼 수 있습니다.

선언 → 생성 → 등록 → 도달 가능 → 호출 → 상태 변경 → 실행 관찰은 서로 다른 확인 상태입니다. 해당 환경에 있는 단계만 적용하고 다음 단계를 추측하지 말아야 합니다. 객체가 만들어져도 요청에서 접근할 수 없을 수 있고 설정에 실제 사용처가 없을 수도 있습니다.

## 상태와 반대 근거 확인

초기 상태와 초기화 위치, 정상·중복 진입, 정상 종료, 실패·취소, 복구·초기화, 겹치는 콜백·재시도·타이머·효과, 소유자 파괴와 콜백 수명을 확인합니다. 동시에 처리되거나 비동기로 실행되는 순서도 해당되면 살펴봐야 합니다.

최종 결론 전에 가장 유력한 원인을 반박해 보고 대응되는 경로 하나 이상을 비교합니다. 같은 사용자 흐름에서 더 심각한 실패도 찾아봐야 합니다. 소스를 못 찾은 것과 기능이 없다는 증명은 다릅니다. 마지막 확인에 실행이 필요하면 판단 수준을 낮추고 확인된 사용자 영향이 큰 순서로 보고합니다.
