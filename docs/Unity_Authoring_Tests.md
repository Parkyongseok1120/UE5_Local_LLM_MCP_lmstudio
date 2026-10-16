# Prefab · 파괴적 승인 · Test Framework 계약

세 기능은 구현되어 있으며 모델이 각각의 도구 호출을 선택합니다. 내부 Planner, 다음 테스트 선택, 수정 루프나 추가 모델은 없습니다. 실제 검증 환경/결과는 [검증 기록](Unity_Validation.md)을 참고하세요.

## Prefab

- `unity_prefab read/contents/overrides`: Observe. `target`은 반환받은 실제 ObjectRef입니다. `read`는 객체 Receipt와 별도로 정확한 원본 파일+meta에 결합된 `sourceReceipt`를 발급합니다. 10분, 최대 128개, domain reload 후 무효입니다.
- `patch`: Edit. `target`, `sourceReceipt`, `patches`, `operationId`, `save:true`. LoadPrefabContents로 격리해 공통 SerializedProperty 계층으로 수정하고 정확한 원본만 저장한 뒤 unload합니다. 열린 대상 Prefab Stage, dirty 원본, 변경된 파일/대상을 거부합니다. 모델이 새 값을 다시 읽어 재검증할 수 있습니다. 저장 전 preview를 폐기할 수 있으나 프로젝트 load/OnValidate callback의 부작용까지 되돌린다고 보장하지 않습니다.
- `create`: Edit. Scene GameObject `target/receipt`, 없는 `.prefab` `assetPath`, `mustNotExist:true`, `save:true`, `operationId`. 기존 파일/meta는 덮어쓰지 않습니다.
- `instantiate`: Edit. 원본 root `target`, 이미 열린 일반 Scene의 정확한 `scenePath`, `operationId`. 짧은 Undo group, Scene dirty만 설정하고 자동 저장하지 않습니다.
- `apply`: Edit. 인스턴스 `target/receipt`, 정확한 `propertyPaths`, 목적지 `assetPath`, 그 목적지의 `sourceTarget/sourceReceipt`, `save:true`, `operationId`. Nested/Variant의 목적지를 추측하지 않습니다. 선택한 override만 적용합니다.
- `revert`: Edit+Destructive. 인스턴스 `target/receipt`, `propertyPaths`, `operationId`, 독립적으로 승인된 `approvalId`. 인스턴스 override만 폐기하고 Scene을 자동 저장하지 않습니다.

배열 자체/`.Array.` 경로, managed-reference 구조, `m_Script` override apply/revert는 범위를 조용히 확대하지 않기 위해 거부합니다. 지원되는 구조 변경은 원본 `patch`로 명시합니다. Model Prefab은 쓰기 대상이 아닙니다. 원본 파일 8 MiB/meta 1 MiB, hierarchy 2,000 객체/10,000 구성 항목 상한입니다. 이름으로 객체를 대응시키지 않습니다. 원본 파일 저장은 일반 Scene Undo나 스냅샷 rollback 보장이 아닙니다.

## 삭제 승인

1. 모델은 대상과 현재 Receipt를 읽고 최종 삭제/revert 인자를 작성합니다.
2. `unity_approval {action:"request",method,arguments}`로 요청합니다. `arguments`에는 최종 `operationId`가 있으며 `approvalId`는 아직 넣지 않습니다.
3. 사용자가 Unity **Tools → Evidence First → Review destructive requests**에서 대상·전체 인자·영향을 보고 승인 또는 거부합니다. 이 단계는 작업을 실행하지 않습니다.
4. 모델이 `unity_approval status`로 상태를 확인하고, 같은 최종 인자에 `approvalId`만 추가하여 원래 도구를 호출합니다.

MCP에 `approve` action이나 `userApproved` 우회는 없습니다. 요청은 2분, 1회, 최대 32개이며 프로젝트/Editor/domain/전체 요청/대상 상태에 결합됩니다. 대상 상태나 operationId가 바뀌면 새 승인이 필요합니다. 만료·거부·사용된 승인은 실행을 허용하지 않습니다. 같은 operationId의 동일한 완료 요청은 journal 결과만 반환합니다.

- `unity_scene delete`: 한 GameObject와 그 하위 트리. Unity Undo, Scene dirty; 저장은 별도.
- `unity_scene remove_component`: Transform 이외의 정확한 component. Undo 및 동일한 승인 규칙.
- `unity_asset delete`: 한 main `.asset/.prefab/.mat`와 metadata를 **OS 휴지통**으로 이동. 폴더/sub-asset 삭제 금지, dirty 원본 거부. 참조 손실 가능성을 표시하며 복구/참조 수정을 자동 수행하지 않습니다.

승인은 같은 OS 사용자나 신뢰하지 않는 프로젝트 코드에 대한 보안 sandbox가 아닙니다. 프로젝트 코드는 원래 Editor 권한으로 실행됩니다.

## Test Framework

`unity_tests status`는 `availability`, `busy`, 활성 operationId를 반환합니다. 패키지 없음(`package_absent`), 패키지는 있으나 어댑터 미활성, API 비호환(`package_api_unsupported`)을 구분합니다. 패키지를 자동 설치하지 않습니다. 기본 Bridge는 Test Framework를 참조하지 않고 선택 assembly만 참조합니다.

실행 요청:

```json
{
  "action": "run",
  "operationId": "test-operation-0001",
  "mode": "EditMode",
  "testNames": ["MyTests.ExactTestName"],
  "maxTests": 1,
  "maxDurationMs": 30000,
  "acknowledgeSceneChanges": true
}
```

`categories`를 대신 지정하거나 함께 지정할 수 있습니다. 둘 다 있으면 교집합입니다. 발견된 정확한 leaf full name만 실행하며 선택이 없거나 모호하거나 상한을 넘으면 전체 테스트로 확대하지 않습니다. Execute 권한이 Node와 Editor 양쪽에서 필요합니다. idle Edit Mode에서 시작하며 dirty Scene이 있으면 명시적 저장/폐기를 먼저 요구합니다. Framework가 테스트용 Scene/Play 상태를 전환할 수 있음을 요청에서 인정해야 합니다.

- `run`: `preparing/accepted/running`과 operationId. 통과를 뜻하지 않습니다.
- `status`/`unity_operation get`: 저장된 job 상태와 관찰된 수치. `completed`라도 `testStatus`/`failed`를 확인해야 합니다.
- `results`: `operationId`, `limit/cursor/byteBudget`. 개별 name/resultState/duration/message/stackTrace/output을 페이지로 반환합니다. 기본 상태 조회는 전체 결과를 반환하지 않습니다.
- `cancel`: 협력적 취소 요청. `cancel_requested`, 요청 수락 여부, 원인을 기록합니다. 이후 소유 Framework job 종료를 확인하면 `cancelled/testStatus:incomplete`; 완료를 확인하지 못하면 `outcome_unknown`입니다. 테스트가 만든 외부 작업/부작용의 중단·복구까지 보장하지 않습니다.
- `release`: 완료/취소/불명 결과를 명시적으로 해제합니다. 활성 소유 job은 해제할 수 없습니다.

`not_run`(선택 없음/실행 전 취소), `execution_failed`, `completed`+실제 실패/통과, `cancelled`, `outcome_unknown`을 구분합니다. 미완료 이전 Editor 세션의 기록은 unknown으로 남기고 자동 재개하지 않습니다. PlayMode가 재구성하는 NUnit 내부 숫자 ID는 고정 식별자로 가정하지 않습니다. 단일 소유 실행, 관찰한 시작, root 이름과 정확한 결과 leaf 이름으로 연결하고 뜻하지 않은 추가 실행/중복 결과는 unknown으로 처리합니다.

자원 한도: 활성 job 1개, 이름/분류 각 32개, 발견 노드 20,000개, 선택 테스트 1..256개, 100..600,000ms, 이름 합계 128 KiB, 저장 16 runs×1 MiB. 결과 문자열별 800자 및 합계 바이트 상한, 잘림/생략 수를 기록합니다. 수집 결과는 프로젝트 `Library/EvidenceFirst/test-runs`에 보관합니다. 추가 모델은 사용하지 않습니다.
