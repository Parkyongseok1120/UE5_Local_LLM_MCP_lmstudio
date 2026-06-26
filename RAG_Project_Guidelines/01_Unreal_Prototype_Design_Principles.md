# Unreal Prototype Design Principles

## 목적

로컬 AI가 Unreal Engine C++ 프로토타입 코드를 만들 때, 빠르게 동작하는 코드뿐 아니라 나중에 실제 게임 코드로 이어질 수 있는 구조를 우선한다. 프로토타입은 버려도 되는 코드가 아니라, 검증된 방향을 남기는 작은 구현 단위로 취급한다.

검색 키워드: Unreal prototype, design principles, 책임 분리, SSOT, interface, API, lifecycle, local AI code quality, UE C++ 안정성

## 핵심 원칙

1. 먼저 근거를 찾는다.
   - Unreal API 이름, Lyra 패턴, 프로젝트 클래스 이름, 모듈 의존성은 RAG 검색 결과를 우선한다.
   - 근거가 없으면 API를 지어내지 말고 "확인이 필요하다"고 말한다.
   - 프로젝트 안에 이미 비슷한 구현이 있으면 그 스타일을 따른다.

2. 가장 작은 완성 단위를 만든다.
   - 기능을 "입력 -> 상태 변경 -> 피드백 -> 검증"까지 닫힌 한 조각으로 자른다.
   - 불필요한 프레임워크, 매니저, 추상화는 만들지 않는다.
   - 다음 단계가 명확하지 않은 범용 시스템보다 현재 요구를 정확히 만족하는 얇은 구조를 둔다.

3. 책임 소유자를 먼저 정한다.
   - 상태를 누가 소유하는지, 누가 읽는지, 누가 변경하는지 먼저 정한다.
   - Actor, Component, Subsystem, GameState, PlayerState, DataAsset, Widget의 역할을 섞지 않는다.
   - UI는 게임 상태를 직접 소유하지 않고 표시와 사용자 의도 전달을 맡는다.

4. SSOT를 깨지 않는다.
   - 같은 상태를 여러 곳에 복사하지 않는다.
   - 캐시는 허용하되 원본, 갱신 시점, 무효화 조건을 명확히 둔다.
   - Config/DataAsset/SaveGame/Replicated State 중 무엇이 원본인지 먼저 결정한다.

5. Unreal 생명주기를 존중한다.
   - Constructor에서는 기본값과 Subobject 생성만 한다.
   - World, GameInstance, PlayerController, Component 참조가 필요한 초기화는 BeginPlay, InitializeComponent, OnRegister, InitGame 등 적절한 생명주기에서 처리한다.
   - Tick은 마지막 선택지다. Timer, Delegate, Event, Gameplay Message, Ability Task 같은 이벤트 기반 흐름을 먼저 고려한다.

6. Blueprint 경계는 명시한다.
   - 디자이너가 조정할 값은 UPROPERTY(EditDefaultsOnly/EditAnywhere)와 DataAsset으로 노출한다.
   - Blueprint가 호출해야 하는 명령은 UFUNCTION(BlueprintCallable)로 제한하고, 순수 조회는 BlueprintPure로 분리한다.
   - Blueprint에서 오버라이드해야 하는 지점은 BlueprintImplementableEvent 또는 BlueprintNativeEvent로 의도를 드러낸다.

7. 네트워크와 저장을 나중 문제로 밀지 않는다.
   - 멀티플레이 가능성이 있는 상태는 처음부터 Authority, Owner, Replication, Prediction 필요성을 표시한다.
   - 저장 가능한 상태와 런타임 임시 상태를 섞지 않는다.
   - 클라이언트 입력은 요청일 뿐이며 최종 판정은 서버 또는 권한 소유자가 한다.

8. 구현 안정성을 설계 산출물에 포함한다.
   - Build.cs 의존성, include 위치, forward declaration, GENERATED_BODY, generated.h 위치를 확인한다.
   - UObject 참조는 GC가 볼 수 있게 UPROPERTY 또는 TObjectPtr/TWeakObjectPtr 사용 여부를 판단한다.
   - Delegate, Timer, Async 콜백은 해제 시점을 함께 설계한다.

## 로컬 AI 응답 규칙

- 코드 생성 전 "상태 소유자", "호출 방향", "검증 방법"을 짧게 정리한다.
- 파일을 제안할 때는 Header, CPP, Build.cs, Config/DataAsset 변경 여부를 분리해서 말한다.
- 모르는 API는 추측하지 않는다. RAG 검색어를 제안하거나 필요한 파일명을 물어본다.
- 예제 코드는 컴파일 가능한 형태를 목표로 하며, 누락된 프로젝트별 타입은 명시적으로 표시한다.
