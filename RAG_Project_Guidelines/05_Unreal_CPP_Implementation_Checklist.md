# Unreal C++ Implementation Checklist

## Critical Rule: Label Code Accuracy

설계 리뷰나 코드 제안에 포함된 예시는 반드시 `Pseudocode only` 또는 `Compile-ready Unreal C++` 중 하나로 구분한다. 확신이 없으면 의사코드로 표시한다.

검색 키워드: compile-ready Unreal C++, pseudocode only, Unreal API signature, UFUNCTION, UINTERFACE, generated.h, implementation accuracy

Compile-ready Unreal C++라고 제시할 때 확인할 것:

- Unreal API 함수명과 시그니처가 실제 엔진 버전과 맞는가?
- UCLASS/USTRUCT/UENUM/UINTERFACE 선언 규칙을 지켰는가?
- `GENERATED_BODY()`와 `.generated.h` 위치가 맞는가?
- UFUNCTION specifier와 `_Implementation` 필요 여부가 맞는가?
- Blueprint 구현 가능 인터페이스 호출에 `IInterfaceName::Execute_FunctionName(Object, Args)`가 필요한지 확인했는가?
- Build.cs 모듈 의존성을 확인했는가?
- 헤더/CPP에 필요한 include와 forward declaration이 일치하는가?

설계 코드와 구현 코드 일치 규칙:

- 설계에서 제안한 Owner, Command, Query, Event 이름이 코드 예시와 일치해야 한다.
- 설계에서 "delegate로 분리"라고 말했으면 구현 예시에 인터페이스 `OnXChanged` 함수를 넣지 않는다.
- 설계에서 `ApplyDamage`를 제안했으면 구현에서 `SetHealth`로 바꾸지 않는다.
- GAS AttributeSet의 generated setter는 compile support 또는 initialization helper일 수 있다. 리뷰/답변에서 이를 안전한 외부 mutation API처럼 소개하지 않는다.
- 구현하지 않은 부분은 `not implemented in this snippet` 또는 `project-specific type required`라고 명시한다.

자기모순 및 선언 일치 검사:

- 금지한다고 말한 패턴을 코드 예시에서 다시 사용하지 않는다.
- 코드에서 호출하는 함수는 이전 선언 또는 RAG로 확인된 Unreal/project API에 존재해야 한다.
- 선언에 없는 함수, 변수, Delegate, TimerHandle을 사용하지 않는다.
- `.cpp`에서 사용하는 멤버는 `.h`에 선언되어 있어야 한다.
- `Broadcast`하는 Delegate는 해당 상태 소유 객체에 선언되어 있어야 한다.
- Timer를 설정/해제하는 코드는 대응하는 `FTimerHandle` 선언을 포함해야 한다.
- BlueprintNativeEvent를 사용하면 선언과 `_Implementation` 구현 규칙을 함께 확인한다.

## 목적

이 체크리스트는 로컬 AI가 Unreal Engine C++ 코드를 생성하거나 수정할 때 컴파일 안정성, 런타임 안정성, 유지보수성을 확인하기 위한 기준이다.

검색 키워드: Unreal C++ checklist, UCLASS, UPROPERTY, TObjectPtr, Build.cs, replication, lifecycle, timer, delegate, module, generated.h, implementation stability

## 파일과 모듈

- `.generated.h`는 헤더 include 목록의 마지막에 둔다.
- 헤더에는 필요한 최소 include만 넣고 가능한 곳에는 forward declaration을 사용한다.
- `.cpp`에는 실제 사용하는 타입의 include를 둔다.
- 새 모듈 타입을 쓰면 Build.cs 의존성을 확인한다.
- Editor 전용 API는 Runtime 모듈에서 직접 참조하지 않는다.
- 순수 게임플레이 UCLASS/USTRUCT/UENUM은 불필요한 namespace 안에 넣지 않는다.

## Reflection 매크로

- UCLASS/USTRUCT/UENUM에는 적절한 Unreal macro와 `GENERATED_BODY()`를 둔다.
- UObject 파생 타입은 Unreal 생성 규칙을 따른다. 일반 `new`로 만들지 않는다.
- Blueprint 노출이 필요한 타입과 함수만 노출한다.
- UPROPERTY specifier는 소유권과 편집 의도를 드러낸다.
  - 튜닝 기본값: `EditDefaultsOnly`
  - 인스턴스별 편집: `EditInstanceOnly` 또는 `EditAnywhere`
  - 런타임 읽기 표시: `VisibleInstanceOnly`
  - Blueprint 읽기: `BlueprintReadOnly`

## UObject 수명과 GC

- UObject 참조를 멤버로 보관하면 UPROPERTY 또는 `TObjectPtr` 사용을 우선한다.
- 소유하지 않는 약한 참조는 `TWeakObjectPtr`를 사용한다.
- Actor/Component 포인터는 사용 직전에 `IsValid()` 또는 명확한 lifetime 보장으로 확인한다.
- Timer, Delegate, Async 콜백이 객체 파괴 후 실행되지 않도록 EndPlay/BeginDestroy/NativeDestruct에서 정리한다.
- `AddToRoot`는 일반 게임플레이 코드에서 사용하지 않는다.

## 생명주기

- Constructor: 기본값, CreateDefaultSubobject, CDO-safe 설정만 수행한다.
- OnRegister/InitializeComponent: Component 등록과 의존 Component 탐색.
- BeginPlay: World가 필요한 초기화, Actor 관계 연결.
- EndPlay: Delegate 해제, Timer 정리, 외부 등록 해제.
- Tick: 꼭 필요할 때만 켜고, 가능하면 이벤트/타이머로 바꾼다.

## 네트워크

- 서버 권한이 필요한 Command는 `HasAuthority()` 또는 명확한 RPC 흐름을 둔다.
- Replicated 변수는 `GetLifetimeReplicatedProps`와 OnRep 필요 여부를 확인한다.
- OnRep 함수는 cosmetic/UI 동기화에 집중한다.
- 클라이언트 입력값은 검증 전까지 신뢰하지 않는다.
- Gameplay Ability System을 쓰는 프로젝트에서는 Attribute, Effect, Ability, GameplayTag의 기존 패턴을 우선 검색한다.

## 데이터와 에셋

- 하드코딩된 asset path보다 UPROPERTY로 지정한 class/object reference 또는 DataAsset을 우선한다.
- ConstructorHelpers는 정말 필요한 기본 에셋에만 제한적으로 사용한다.
- 런타임 로딩은 SoftObjectPtr/StreamableManager 등 명시적 비동기 흐름을 고려한다.
- DataTable/DataAsset row 이름과 GameplayTag는 오타에 취약하므로 검증 로그를 둔다.

## 안정성

- 실패 가능한 경로에는 `ensure`, `UE_LOG`, result enum, false 반환 중 적절한 신호를 둔다.
- `check`는 복구 불가능한 개발 오류에만 사용한다.
- Null fallback을 조용히 삼키지 않는다.
- 로그 카테고리를 새로 만들거나 기존 프로젝트 카테고리를 사용한다.
- AI가 생성한 임시 이름은 프로젝트 naming convention에 맞춰 정리한다.

## 검증

- 최소한 컴파일 가능한 include와 Build.cs 의존성을 확인한다.
- 가능하면 Automation Test, Functional Test, PIE 수동 검증 단계 중 하나를 제안한다.
- 수정 파일별로 "컴파일 영향", "런타임 영향", "네트워크 영향"을 짧게 점검한다.
