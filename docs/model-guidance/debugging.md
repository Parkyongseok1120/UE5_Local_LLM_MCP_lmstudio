# 오류 조사와 의미를 보존하는 수정

적용: 빌드 오류, API 검색 실패, 같은 오류의 반복, 수정 뒤 행동 변화.
근거: [확인한 Unreal 사례](sources.md#unreal57), [Unity 문서](sources.md#unity).

<!-- guidance-section: definitions -->
## 오류에서 정의까지

1. 현재 파일의 진단 위치와 실제 표현식을 읽는다. 생성식, 참조 전달, 멤버 접근, 매크로 확장을 구분한다.
2. 정확한 심볼·부모 선언·인자 타입과 적용 버전을 찾는다. 이미 확인한 동일 버전의 근거는 재사용한다.
3. 검색은 좁은 이름·모듈·파일 범위부터 시작한다. 직접 소스, 심볼 도구, 적용 버전의 공식 문서 중 가능한 수단을 선택한다.
4. 검색 결과 없음, 도구 오류, 인덱스 범위 밖, API 부재를 구분한다. 실패 메시지에 맞춰 범위를 조정한다.
5. 확인된 원인에 맞춰 최소 수정하고 결과를 본다. 같은 가설이 실패하면 새 근거 없이 헤더·시그니처를 계속 바꾸지 않는다.

이는 판단 순서의 참고다. 모든 작업에 고정된 도구 호출 순서를 강제하지 않는다.

<!-- /guidance-section: definitions -->
<!-- guidance-section: preserve-contract -->
## 보존할 계약

컴파일 오류를 없애려다 부모 호출, 콜백 등록, RPC, 복제 등록, 필터·채널, 처리 순서를 바꾸는지 살핀다. 바꿔야 한다면 게임 규칙의 근거와 영향 범위를 설명한다.

| 실제 사례 | 정확한 구분 |
| --- | --- |
| UE 5.7의 FDamageEvent() 생성 | 완전한 타입 정의가 필요하며 Engine/DamageEvents.h에서 확인. 현재 경로 확인으로 “5.7에서 이동했다”는 이력까지 증명되지 않음 |
| DOREPLIFETIME 사용 | UE 5.7 매크로가 OutLifetimeProps라는 식별자를 참조. C++ 선언·정의의 매개변수 이름 일치를 일반 필수 규칙으로 설명하지 않음 |
| APlayerState의 Score·OnRep_Score | 부모에 실제 선언이 있음. 부모 등록과 신규 UFUNCTION/RepNotify 계약을 구분 |
| FCollisionObjectTypes::AllStatic 오류 | UE 5.7의 실제 타입은 FCollisionObjectQueryParams, 열거 값은 AllStaticObjects. 정적 객체만 맞혀야 하는지 의도는 별도 확인 |
| ObjectType → Visibility Trace | 조회 기준이 변경됨. 벽·Pawn·Mesh·자신·아군 필터에 미치는 영향을 확인 |
| Unity Physics2D → Physics | 2D/3D 물리 시스템이 다름. 오버로드 오류를 피하는 교체로 처리하지 않음 |
| Unity 콜백·네트워크 API 변경 | 컴파일 성공과 엔진·네트워크의 실제 호출 조건을 각각 확인 |

<!-- /guidance-section: preserve-contract -->
<!-- guidance-section: verification -->
## 결과를 보고하는 수준

- 선언을 찾음: 해당 버전의 API 근거를 얻었다.
- 코드를 수정함: 수정 파일을 확인했다.
- 빌드 성공: 실행한 대상·구성의 실제 결과가 성공했다.
- 실행 확인: 명시한 조건에서 관찰한 동작이 맞았다.

다른 오류가 먼저 나왔다는 사실만으로 이전 오류의 해결을 확정하지 않는다. 캐시·컴파일러·버전 이동을 원인으로 설명할 때도 직접 근거를 둔다. 도구 실패 표시만으로 컴파일 실패와 도구 전송 실패를 동일하게 취급하지 않는다.
<!-- /guidance-section: verification -->
