# Tick 실행 순서와 수명

매 프레임 실행할 필요가 있는지부터 판단해야 합니다. 상태 변화 알림이나 타이머로 충분하면 Tick을 추가하지 않습니다. 필요한 경우에는 언제 켜고 끄는지 정합니다.

다른 객체가 갱신되기 전에 값을 읽는다면 단순 컴파일 문제가 아닙니다. 실제 프레임 순서와 의존성을 확인해야 합니다.

- `TickGroup`: 물리 전·중·후와 후반 갱신을 구분합니다. `TG_PrePhysics`, `TG_DuringPhysics`, `TG_PostPhysics`, `TG_PostUpdateWork`의 현재 엔진 의미를 확인합니다.
- 선행 Tick: `AddTickPrerequisiteActor`, `AddTickPrerequisiteComponent`로 먼저 갱신되어야 하는 대상을 명시합니다. 관계가 끝나면 해제도 확인합니다.
- 실행 여부: `bCanEverTick`, `bStartWithTickEnabled`, `SetActorTickEnabled`, `SetComponentTickEnabled`를 확인합니다.

물리 결과를 읽는 시점, 캐릭터 이동 뒤 카메라 갱신, 초기화가 끝나기 전에 접근하는지 확인해야 합니다. null 검사나 한 프레임 캐시로 원인을 덮지 않습니다.

서브시스템에 컴포넌트의 Tick 방식을 그대로 적용하지 말고 실제 제공되는 기능과 수명을 확인합니다. 순서 수정은 빌드 성공만으로 끝내지 말고 플레이나 로그로 확인해야 합니다.
