# 작업 시작·종료 처리의 대응 관계

연결이나 실행을 시작한 객체가 종료·취소·실패·파괴 때 무엇을 정리하는지 함께 정합니다.

| 시작 | 정리할 것 |
|---|---|
| 델리게이트 연결 | 해당 연결 해제 |
| `SetTimer` | 타이머 종료 |
| Tick 활성화 | 유휴·종료 시 비활성화 |
| 몽타주와 종료 콜백 | 몽타주 중단과 콜백 정리 |
| 시네마틱 실행 | 기능 종료·객체 종료에서 시네마틱 정리 |

필요한 `Super::BeginPlay`, `Super::EndPlay`, `Super::Tick` 호출을 확인합니다. 몽타주의 `bInterrupted`를 정상 완료와 구분하고 재진입·중복 종료도 확인해야 합니다.

관련 경고는 `TIMER_SET_WITHOUT_CLEAR`, `DELEGATE_BIND_WITHOUT_UNBIND`, `INTERRUPT_PARAM_IGNORED`, `MISSING_SUPER_LIFECYCLE_CALL`입니다.
