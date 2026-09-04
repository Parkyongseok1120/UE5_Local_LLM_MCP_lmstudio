---
name: evidence-first-code-audit
description: 코드 검토, 오류 분석, 구조 파악과 코드 작성 전에 소스와 실제 동작 근거를 확인합니다. 호출 시작부터 최종 결과까지 추적하고 반대 근거와 확인 수준을 함께 남겨 추측으로 결론 내리는 일을 줄입니다.
---

# 근거부터 확인하는 코드 검토

## 검토·구조 분석·코드 작성 모드

- `Audit`: 파일을 바꾸지 않고 동작과 오류를 검토합니다.
- `Architecture`: 누가 데이터를 관리하고 서로 어떻게 연결되는지 파악해 필요한 최소 변경을 제안합니다.
- `Codegen`: 코드를 쓰기 전에 지켜야 할 조건과 확인 방법을 정합니다.

사용자가 구현을 요청한 범위에서만 파일을 수정해야 합니다.

## 확인 순서

1. 저장소, 요청 범위, 실행 환경과 버전, 수정 권한을 확인합니다.
2. 언어·빌드·테스트 도구와 저장소의 작업 지침을 읽습니다.
3. 정의, 소유자, 시작점, 호출부, 설정, 테스트, 외부 라이브러리 경계를 찾습니다.
4. 구현을 판단하기 전에 인터페이스, 주석, 데이터 형식, 테스트, 부모 클래스, 생성·종료 시점의 규칙을 읽습니다.
5. 중요한 동작은 입력부터 분기·호출을 거쳐 상태 변경이나 외부 결과까지 추적합니다.
6. 선언 여부, 생성 여부, 등록 여부, 호출 여부, 상태 변경 여부, 실제 관찰 여부를 구분합니다. 하나를 확인했다고 다음 단계도 된 것으로 보지 말아야 합니다.
7. 주요 결론마다 근거 종류, 반대 근거와 모르는 점을 기록합니다.
8. 다른 원인 하나 이상을 검토하고 같은 흐름에 더 심각한 실패가 없는지 확인합니다.
9. 관련 근거를 확인한 뒤 구조나 코드를 제안합니다.

세부 형식은 [근거 기록 규칙](references/audit-contract.md), [구조 변경과 코드 작성](references/architecture-codegen.md)을 참고합니다. 다른 도구에 적용할 때는 [공통 작업 규칙](references/portable-rule.md), LM Studio 연결은 [연결 안내](references/lmstudio.md)를 읽어야 합니다.

## 근거 확인과 판단 기준

외부 라이브러리 동작이 원인이라고 말하려면 해당 버전의 직접 소스나 공식 문서를 확인합니다. 설정이나 객체가 존재한다는 사실만으로 실행에 연결됐다고 말하지 말아야 합니다. 플레이어·적, 클라이언트·서버, 성공·실패, 시작·복구처럼 대응되는 경로도 비교합니다.

`behaviorPath`의 `stage`에는 `entry`, `decision`, `dispatch`, `mutation`, `side_effect`, `observer`만 사용하고 원인 순서대로 적습니다. 각 `stageStatus`는 `present`, `expected_missing`, `unknown` 중 하나입니다. 생성·변환은 `symbol`이나 근거에 설명하고 단계 이름을 새로 만들지 말아야 합니다.

상태가 있는 기능은 시작·종료·복구·취소·재진입·중복 실행·객체 파괴까지 확인합니다. 판단은 `Bug`, `ByDesign`, `Ambiguous`, `NeedsRuntimeProof`로 구분합니다. 구조 검토에서 확인된 현재 사실만 중립적으로 기록할 때는 `Confirmed`와 `Info`를 함께 쓸 수 있습니다. 이 조합은 설계 의도를 증명하지 않으며 `codegen`이나 `Proposed`에는 사용하지 않습니다. 경로에 `unknown`이 있으면 판단도 `Ambiguous` 또는 `NeedsRuntimeProof`로 남겨야 합니다.

확인 수준은 `Proposed`, `SourceVerified`, `StaticVerified`, `BuildVerified`, `TestVerified`, `RuntimeVerified`로 나누고 실제 근거와 맞춥니다. 빌드 성공만으로 실행 동작까지 검증됐다고 말하면 안 됩니다.

## 근거 형식 검사와 설치

근거를 JSON으로 저장하고 아래 명령으로 검사합니다. 원인을 단정하는 P0·P1 보고나 여러 파일의 구현 계획에는 이 검사가 필요합니다. 그 외 답변에서는 선택 사항입니다.

```text
python scripts/validate_evidence_packet.py audit.json
```

특정 엔진·언어·운영체제를 공통 규칙에 끼워 넣지 말아야 합니다. 프로젝트별 명령과 검사는 해당 프로젝트의 지침으로 관리합니다. 아래 명령은 이 스킬 폴더 기준입니다.

```text
python scripts/install_skill.py --dry-run
python scripts/install_skill.py
python scripts/install_portable_rule.py <agent-rule-path> --dry-run
python scripts/install_portable_rule.py <agent-rule-path>
```

`<agent-rule-path>`는 규칙을 저장할 실제 경로로 바꿔야 합니다. Windows에서는 다음 명령도 가능합니다.

```text
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-skill.ps1 -WhatIf
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-skill.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-portable-rule.ps1 -OutputPath <agent-rule-path> -WhatIf
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-portable-rule.ps1 -OutputPath <agent-rule-path>
```

LM Studio는 저장소의 통합 설치기로 설정과 읽기 전용 `evidence-first` MCP를 설치합니다. 이 도구는 근거 형식을 검사하며 프로젝트 파일 접근·실행은 별도로 허용한 프로젝트 도구가 담당합니다.

## 결과 작성

확인된 문제 중 영향이 큰 것부터 설명합니다. 주요 결론마다 판단, 심각도, 소스 근거, 실제 호출 경로, 검토한 반대 근거, 확인 수준과 남은 불확실성을 적어야 합니다. 구조 제안은 필요한 차이만 설명하고 재사용해야 할 기존 기능은 `doNotDuplicate`에 명시합니다.
