# 정적 검사 경고를 읽는 방법

아래 코드는 확인할 위치를 알려 주는 보조 경고입니다. 실제 문맥을 읽고 판단해야 합니다. 경고 자체가 쓰기·빌드 권한을 주거나 빼앗지 않습니다.

| 경고 코드 | 확인할 내용 |
|---|---|
| `UOBJECT_CONTAINER_WITHOUT_UPROPERTY`, `TOBJECTPTR_WITHOUT_UPROPERTY`, `RAW_UOBJECT_MEMBER_WITHOUT_UPROPERTY` | 유지할 객체 참조를 가비지 수집이 추적하는지 |
| `DELEGATE_BIND_WITHOUT_UNBIND`, `TIMER_SET_WITHOUT_CLEAR` | 종료 때 연결·타이머를 정리하는지 |
| `INTERRUPT_PARAM_IGNORED` | 중단·취소를 성공과 구분하는지 |
| `UNCHECKED_CAST_RESULT` | 변환 실패 후 바로 접근하는지 |
| `REPLICATED_UPROPERTY_WITHOUT_DOREPLIFETIME` | 복제할 값을 등록했는지 |
| `RAW_NEW_DELETE_UOBJECT` | UObject에 일반 new·delete를 쓰는지 |
| `ACTOR_CTOR_GETWORLD` | 생성자에서 실행 중 월드를 요구하는지 |
| `SYNC_LOAD_IN_GAMEPLAY`, `HARDCODED_ASSET_PATH` | 반복 실행 중 동기 로딩·고정 경로가 필요한지 |
| `FVECTOR_FLOAT_PRECISION`, `BLUEPRINTPURE_MISSING_CONST` | 자료형 정밀도와 조회 함수 선언이 의도와 맞는지 |

무조건 제안대로 바꾸지 말고 현재 소유자·수명·사용 범위를 확인합니다. 최종 컴파일·실행 결과는 별도 검증입니다.
