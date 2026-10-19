# Unity의 책임·수명·API

적용: Unity C#, 설정·활성화·풀링·비동기·입력·물리 오류.
범위: Unity 6.0, Input System 1.14 문서 기준. 실제 Editor·패키지·asmdef·플랫폼 조건과 대조한다. [근거](sources.md#unity)

<!-- guidance-section: wiring -->
## 버전과 실행 연결

- ProjectVersion.txt, manifest.json과 실제 해결된 packages-lock.json, 관련 asmdef 참조를 확인한다.
- API 도입·변경 전에 실행 Editor 버전, 저장된 ProjectVersion, 해결된 패키지 버전·asmdef를 구분해 선언과 호출 조건을 확인한다. 같은 근거를 재사용하되 버전/의존성이 바뀌면 재확인한다.
- SerializeField는 필드 직렬화 목적과 지원 타입을 확인한다. 일반 C# property/event와 UnityEvent를 동일하게 취급하지 않는다. 선언뿐 아니라 에셋·Inspector 연결과 실제 콜백을 확인한다.
- CS0246·CS1061에서는 수신 타입, 확장 메서드, 접근 범위, 패키지 소스(read_file path=package://<패키지명>/<경로>, 읽기 전용·packages-lock.json에 있는 패키지)·메타데이터와 적용 버전을 찾는다.
- 새 외부 API를 사용할 때도 같은 선언 확인을 적용한다. 검색 후보·다른 패키지 버전의 예제를 실제 해결 버전의 계약으로 쓰지 않는다. 컴파일을 통과하려고 callback·async·네트워크 호출의 의미를 바꾼 경우 별도 동작 근거가 필요하다.
- 인스펙터 필드·ScriptableObject·이벤트 선언 → 초기화·등록 → 실제 호출·변경의 연결을 확인한다.
- 비기본 Inspector/ScriptableObject 값이 실제 소비자에 전달되는지 확인한다. 필드·UnityEvent 이름이나 시그니처를 바꾸기 전에 Scene/Prefab·직렬화 참조와 migration을 확인한다. C# 검색만으로 에셋 사용처 부재를 증명하지 않는다.
- ScriptableObject 설정과 개체별 실행 상태를 구분한다. 공유 에셋의 변경을 의도한다면 범위와 복구·저장 책임을 명시한다.
- 서로 다른 GameObject의 동일 콜백 순서를 임의로 가정하지 않는다. 초기화 의존성은 엔진 보장 또는 프로젝트의 명시적 연결로 해결한다.
- Runtime/Editor·테스트·플랫폼 코드를 asmdef와 의존성 관점에서 확인한다.

<!-- /guidance-section: wiring -->
<!-- guidance-section: lifetime -->
## 종료와 재사용

- 객체 존재, 활성화, Scene, Pool 대여, 네트워크 Spawn/Despawn의 수명은 다르다.
- 이벤트는 필요한 수명에 맞춰 구독·해제한다. 비활성화 동안에도 필요한 알림을 무조건 끊지 않는다.
- MonoBehaviour.enabled=false만으로 코루틴은 멈추지 않는다. GameObject 비활성화·파괴와 구분한다.
- destroyCancellationToken은 파괴에 연결된다. 필요한 토큰은 파괴 전에 확보하고 실제 비동기 API까지 전달·처리한다.
- Task/Awaitable의 재개 문맥과 Unity API의 메인 스레드 제약을 확인한다. Task.Run 추가·await 제거를 단순 컴파일 해결로 쓰지 않는다.
- Pool 반환·Despawn 이후 완료된 결과가 재사용된 개체에 적용되지 않게 한다. 기존 작업의 취소·식별 방식부터 재사용한다.
- 취소 요청, 실제 작업 중단, 늦은 결과의 적용 차단을 구분한다. 로컬 토큰 생성만으로 비동기 API에 취소가 연결됐다고 보고하지 않는다. 실패·중복 반환·재대여에서도 상태 초기화와 구독 수명을 확인한다.
- Domain Reload를 끈 환경에서 static 값·이벤트가 Play 사이에 남을 수 있으므로 초기화 책임을 둔다.
- 사망·반환·취소 뒤 늦은 재장전·로드 결과가 이전 상태를 복원하지 않게 한다.
- GetComponent 등 조회를 캐시하기 전에 호출 빈도와 실제 비용, 교체·재대여 수명을 확인한다. 캐시 초기화가 Awake 한 번으로 충분한지 실제 소유자의 수명과 대조한다.

<!-- /guidance-section: lifetime -->
<!-- guidance-section: api -->
## 입력·물리·콜백의 의미

| 대상 | 구분할 계약 |
| --- | --- |
| Input System 1.14 | 프로젝트 전체 Action은 기본 활성화, 그 밖의 에셋·코드 정의 Action은 활성화 필요. 프로젝트 구성별로 확인 |
| Physics / Physics2D | 서로 다른 물리 시스템; LayerMask·Trigger·깊이·결과 타입도 적용 API로 확인 |
| Physics.RaycastAll | 결과 순서 미보장; 첫 원소를 가장 가까운 것으로 사용하지 않음 |
| OnTriggerEnter2D | Collider2D 시그니처와 실제 물리 발생 조건을 함께 확인 |
| 콜백·이벤트 등록 | 메서드 존재와 호출 가능성은 다름. 이름·인자를 바꾼 뒤 실제 연결 확인 |

Unity 네트워크의 Spawn·권한·복제는 채택한 패키지에 맞춰 unity-networking.md를 참고한다. 다른 Unity 네트코드의 API를 섞지 않는다.
<!-- /guidance-section: api -->
<!-- guidance-section: verification -->
## 도구 결과와 검증 범위

- 도구의 applied, changed, dirty, saved를 따로 읽는다. Editor 메모리 변경은 저장 완료가 아니다. compile 요청의 accepted는 성공이 아니므로 unity_operation get의 completed·failed·no_compilation_observed와 unity_logs(source=compiler)를 확인한다.
- 테스트 completed는 종료 상태일 뿐이다. testStatus와 failed 개수·실행 범위를 확인한다. 실행할 이름은 unity_tests list(mode)로 찾는다.
- Editor/Play Mode 테스트와 Player 빌드, host 단독과 실제 원격 client 검증의 범위를 구분한다. 소유권과 서버의 상태 결정 책임은 동일한 개념으로 취급하지 않는다.
- 연결 실패 코드를 구분한다. editor_reloading만 잠시 뒤 재시도하고, editor_not_running·bridge_not_installed는 nextStep을 사용자에게 전한다. edit_disabled·execute_disabled·permission_denied는 꺼진 쪽(서버 플래그/Editor 메뉴)을 그대로 보고한다.
- truncated나 omissionReason이 있는 결과는 suggestion(nextCursor·nextStartLine)으로 끝까지 읽기 전까지 일부다. 검색 결과 0건이나 부분 페이지는 부재 증명이 아니다(absenceVerified:false).
- unity_symbols find는 단순·정규화 이름을 match(exact|prefix|contains)로 찾는다. 선언의 uri를 read_file이나 source로 읽고, stale 표시는 색인 뒤 바뀐 파일이다.
<!-- contract-summary: verification -->
프로젝트 정책 unity_contracts@1:
- `unity.policy.claims.absence` (프로젝트 정책) 부분 검색·어휘 경고만으로 부재나 결함을 확정하지 않는다. 이어 읽기와 실제 구독·해제·취소·활성 상태를 확인한다.
<!-- /contract-summary: verification -->
<!-- /guidance-section: verification -->

<!-- guidance-section: verified-lifetime -->
## 로컬 Editor 문서로 확인한 수명 계약

<!-- contract-summary: verified-lifetime -->
엔진 계약 unity_contracts@1 (Unity 6000.3 소스 기준, 다른 버전은 해당 엔진 소스로 재확인):
- `unity.lifetime.destroy-token` (엔진 사실) destroyCancellationToken은 MonoBehaviour가 파괴될 때 취소되는 읽기 전용 토큰이다. 근거: Data/Managed/UnityEngine/UnityEngine.CoreModule.xml:undefined
- `unity.lifetime.persistent-object` (엔진 사실) DontDestroyOnLoad는 새 Scene 로드 시 대상 Object를 파괴하지 않도록 한다. 근거: Data/Managed/UnityEngine/UnityEngine.CoreModule.xml:undefined
<!-- /contract-summary: verified-lifetime -->
<!-- /guidance-section: verified-lifetime -->
