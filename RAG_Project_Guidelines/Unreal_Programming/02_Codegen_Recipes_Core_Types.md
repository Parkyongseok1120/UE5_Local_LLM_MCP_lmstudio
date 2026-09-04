# 언리얼 자료형 선택과 구현 규칙

요청한 기능에 맞는 가장 작은 클래스를 만들어야 합니다. 파일 위치, 필요한 헤더와 모듈, 상태를 누가 보관할지 먼저 확인합니다. 네임스페이스는 웬만하면 추가하지 않으며 언리얼 선언 매크로가 있는 자료형을 새 네임스페이스로 감싸지 않습니다.

## 헤더 공통 규칙

`#pragma once` 뒤에 직접 사용하는 부모 클래스·자료형 헤더를 넣고 `*.generated.h`를 마지막 include로 둡니다. 선언에 맞는 매크로와 `GENERATED_BODY()`를 확인해야 합니다. 포인터·참조는 전방 선언을 검토하되 값으로 가진 구조체, 열거형, 부모 클래스, 인라인 구현에는 전체 정의가 필요한지 확인합니다.

## 자료형별 용도와 선택 기준

- `UActorComponent`: 특정 액터의 기능과 상태를 관리합니다. 해당 기능의 원본 상태를 액터와 중복 보관하지 않습니다.
- `AActor`: 월드에 배치되는 객체입니다. 생성자에서는 기본값과 `CreateDefaultSubobject`를 다룹니다. 실행 중 객체 생성은 적절한 실행 시점에 합니다.
- `UObject`: 보조 객체입니다. `NewObject<T>(Outer)`로 만들고 유지할 참조는 가비지 수집이 추적할 수 있게 보관합니다.
- `UDataAsset`: 조정용 기본값과 설정을 보관합니다. 공유 에셋에 현재 체력 같은 실행 중 값을 쓰지 않습니다.
- `UGameInstanceSubsystem`: 게임 인스턴스가 유지되는 동안 사용하는 기능입니다. `UWorldSubsystem`은 월드가 유지되는 동안 사용하는 기능입니다. 보통 엔진이 생성합니다.
- `UInterface`: 다른 객체가 호출하는 데 필요한 최소한의 규칙을 정의합니다. 블루프린트만 구현한 객체도 고려하고 인터페이스 함수는 생성된 `Execute_함수명` 호출 규칙을 확인합니다.

`BlueprintNativeEvent`는 C++ 기본 동작이 필요할 때 쓰며 `_Implementation`을 확인합니다. `BlueprintImplementableEvent`에는 같은 C++ 구현을 임의로 추가하지 말아야 합니다. 일반 클래스의 이벤트와 인터페이스 호출 규칙을 혼동하지 않습니다.

기본 모듈은 사용하는 자료형에 따라 `Core`, `CoreUObject`, `Engine`부터 확인합니다. 다른 모듈은 실제 사용 근거가 있을 때만 추가해야 합니다. 아래 코드는 형태 예시이며 현재 프로젝트의 이름·선언·엔진 버전으로 확인한 뒤 사용해야 합니다.

## 코드 형태 예시

아래 예시는 실제 프로젝트에서 빌드를 끝낸 결과가 아닙니다. 이름·모듈·엔진 선언을 확인해서 적용해야 합니다.

```cpp
#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "MyComponent.generated.h"

UCLASS(ClassGroup=(Custom), meta=(BlueprintSpawnableComponent))
class <MODULE_API> UMyComponent : public UActorComponent
{
    GENERATED_BODY()

public:
    UMyComponent();

protected:
    virtual void BeginPlay() override;

public:
    virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;
};
```

```cpp
#include "Feature/MyComponent.h"

UMyComponent::UMyComponent()
{
    PrimaryComponentTick.bCanEverTick = true;
}

void UMyComponent::BeginPlay()
{
    Super::BeginPlay();
}

void UMyComponent::TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
    Super::TickComponent(DeltaTime, TickType, ThisTickFunction);
}
```

```cpp
#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "MyActor.generated.h"

UCLASS()
class <MODULE_API> AMyActor : public AActor
{
    GENERATED_BODY()

public:
    AMyActor();

protected:
    virtual void BeginPlay() override;

public:
    virtual void Tick(float DeltaSeconds) override;
};
```

```cpp
#include "Actors/MyActor.h"

AMyActor::AMyActor()
{
    PrimaryActorTick.bCanEverTick = true;
}

void AMyActor::BeginPlay()
{
    Super::BeginPlay();
}

void AMyActor::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
}
```

```cpp
#pragma once

#include "CoreMinimal.h"
#include "UObject/Object.h"
#include "MyService.generated.h"

UCLASS()
class <MODULE_API> UMyService : public UObject
{
    GENERATED_BODY()

public:
    void Initialize();
};
```

```cpp
#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "MyDataAsset.generated.h"

UCLASS(BlueprintType)
class <MODULE_API> UMyDataAsset : public UDataAsset
{
    GENERATED_BODY()

public:
    UPROPERTY(EditDefaultsOnly, BlueprintReadOnly)
    FName Id;
};
```

```cpp
#pragma once

#include "CoreMinimal.h"
#include "Subsystems/GameInstanceSubsystem.h"
#include "MyGameSubsystem.generated.h"

UCLASS()
class <MODULE_API> UMyGameSubsystem : public UGameInstanceSubsystem
{
    GENERATED_BODY()

public:
    virtual void Initialize(FSubsystemCollectionBase& Collection) override;
    virtual void Deinitialize() override;
};
```

```cpp
#pragma once

#include "CoreMinimal.h"
#include "UObject/Interface.h"
#include "MyInteractable.generated.h"

UINTERFACE(BlueprintType)
class <MODULE_API> UMyInteractable : public UInterface
{
    GENERATED_BODY()
};

class <MODULE_API> IMyInteractable
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintNativeEvent, BlueprintCallable)
    void Interact(AActor* InstigatorActor);
};
```
