# 프로토타입 컴포넌트·서브시스템 구현

먼저 기능 하나가 끝까지 동작하는 작은 단위를 만들어야 합니다. 필요 없는 관리자 클래스를 늘리지 않습니다. 헤더·구현과 필요한 경우의 `Build.cs` 변경부터 잡습니다.

`UActorComponent`는 특정 액터의 기능과 상태를 가집니다. 매 프레임 처리가 필요하지 않으면 Tick을 끕니다. `Components/ActorComponent.h`를 포함하고 입력 연결도 담당자 하나를 정합니다.

`UWorldSubsystem`은 월드 수명의 기능에 사용합니다. 초기화는 `Initialize()`, 종료 정리는 해당 엔진의 종료 순서를 확인해 배치합니다. 월드가 살아 있어야 하는 정리는 너무 늦은 `Deinitialize()`에만 의존하지 말고 제공되는 `OnWorldEndPlay(UWorld&)`, `PreDeinitialize()`의 실제 선언과 호출 시점을 확인해야 합니다. 없는 `OnWorldDestroyed` 오버라이드를 만들지 않습니다.

월드는 서브시스템·액터·컴포넌트의 `GetWorld()`나 명시적인 `UWorld*`에서 얻습니다. `GEngine->GetWorld()`나 `GEngine->GetGameInstance()`를 공통 접근점으로 쓰지 말아야 합니다.

명령 표를 `static TMap`에 두면 여러 월드가 같은 상태를 공유할 수 있습니다. 월드별 서브시스템의 인스턴스 멤버로 보관하고 등록과 해제를 짝짓습니다. 콜백은 `TWeakObjectPtr`와 실행 시 유효성 검사를 사용합니다.

`UGameInstanceSubsystem`은 게임 인스턴스 전체에 필요한 기능에 사용합니다. 월드 이동 뒤에도 원래 `UWorld*`를 그대로 쥐고 있지 않도록 무효화 조건을 정해야 합니다.

생성자에서 월드 조회·액터 생성에 의존하지 말고, 선언·구현·모듈을 확인한 뒤 실제 빌드 결과 또는 아직 못 한 검사를 적습니다.
