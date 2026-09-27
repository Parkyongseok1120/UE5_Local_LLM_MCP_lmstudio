# Unity의 책임·수명·API

적용: Unity C#, 설정·활성화·풀링·비동기·입력·물리 오류.
범위: Unity 6.0, Input System 1.14 문서 기준. 실제 Editor·패키지·asmdef·플랫폼 조건과 대조한다. [근거](sources.md#unity)

## 버전과 실행 연결

- ProjectVersion.txt, manifest.json과 실제 해결된 packages-lock.json, 관련 asmdef 참조를 확인한다.
- CS0246·CS1061에서는 수신 타입, 확장 메서드, 접근 범위, 패키지 소스·메타데이터와 적용 버전을 찾는다.
- 인스펙터 필드·ScriptableObject·이벤트 선언 → 초기화·등록 → 실제 호출·변경의 연결을 확인한다.
- ScriptableObject 설정과 개체별 실행 상태를 구분한다. 공유 에셋의 변경을 의도한다면 범위와 복구·저장 책임을 명시한다.
- 서로 다른 GameObject의 동일 콜백 순서를 임의로 가정하지 않는다. 초기화 의존성은 엔진 보장 또는 프로젝트의 명시적 연결로 해결한다.
- Runtime/Editor·테스트·플랫폼 코드를 asmdef와 의존성 관점에서 확인한다.

## 종료와 재사용

- 객체 존재, 활성화, Scene, Pool 대여, 네트워크 Spawn/Despawn의 수명은 다르다.
- 이벤트는 필요한 수명에 맞춰 구독·해제한다. 비활성화 동안에도 필요한 알림을 무조건 끊지 않는다.
- MonoBehaviour.enabled=false만으로 코루틴은 멈추지 않는다. GameObject 비활성화·파괴와 구분한다.
- destroyCancellationToken은 파괴에 연결된다. 필요한 토큰은 파괴 전에 확보하고 실제 비동기 API까지 전달·처리한다.
- Task/Awaitable의 재개 문맥과 Unity API의 메인 스레드 제약을 확인한다. Task.Run 추가·await 제거를 단순 컴파일 해결로 쓰지 않는다.
- Pool 반환·Despawn 이후 완료된 결과가 재사용된 개체에 적용되지 않게 한다. 기존 작업의 취소·식별 방식부터 재사용한다.
- Domain Reload를 끈 환경에서 static 값·이벤트가 Play 사이에 남을 수 있으므로 초기화 책임을 둔다.
- 사망·반환·취소 뒤 늦은 재장전·로드 결과가 이전 상태를 복원하지 않게 한다.

## 입력·물리·콜백의 의미

| 대상 | 구분할 계약 |
| --- | --- |
| Input System 1.14 | 프로젝트 전체 Action은 기본 활성화, 그 밖의 에셋·코드 정의 Action은 활성화 필요. 프로젝트 구성별로 확인 |
| Physics / Physics2D | 서로 다른 물리 시스템; LayerMask·Trigger·깊이·결과 타입도 적용 API로 확인 |
| Physics.RaycastAll | 결과 순서 미보장; 첫 원소를 가장 가까운 것으로 사용하지 않음 |
| OnTriggerEnter2D | Collider2D 시그니처와 실제 물리 발생 조건을 함께 확인 |
| 콜백·이벤트 등록 | 메서드 존재와 호출 가능성은 다름. 이름·인자를 바꾼 뒤 실제 연결 확인 |

Unity 네트워크의 Spawn·권한·복제는 채택한 패키지에 맞춰 unity-networking.md를 참고한다. 다른 Unity 네트코드의 API를 섞지 않는다.
