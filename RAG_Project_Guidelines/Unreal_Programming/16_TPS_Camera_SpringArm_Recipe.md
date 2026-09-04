# 3인칭 카메라·조준·무기 연결

카메라가 캐릭터 뒤를 따라가고, 카메라 기준으로 이동하며, 화면 중앙에서 조준하는 작은 흐름부터 만듭니다.

`USpringArmComponent`와 `UCameraComponent`의 소유자·부착 위치를 정합니다. 캐릭터 몸체는 이미 있는 `ACharacter::GetMesh()`를 우선 사용하고 같은 몸체 메시를 중복 생성하지 않습니다.

조준 판정은 카메라 시점을 기준으로 할 수 있지만 무기 외형은 손 소켓에 붙이는 식으로 역할을 구분합니다. 실제 소켓 이름과 트레이스 충돌 채널은 프로젝트에서 확인해야 합니다.

`GameFramework/Character.h`를 `Game/Framework/Character.h`로 잘못 적지 않습니다. 입력 연결은 올바른 입력 초기화 함수에서 처리합니다. 카메라가 흔들리면 대상 이동과 카메라 갱신 순서를 확인해야 합니다.

## 코드 형태 예시

아래 예시는 실제 프로젝트에서 빌드를 끝낸 결과가 아닙니다. 이름·모듈·엔진 선언을 확인해서 적용해야 합니다.

```cpp
#include "GameFramework/Character.h"
#include "GameFramework/SpringArmComponent.h"
#include "Camera/CameraComponent.h"

USpringArmComponent* SpringArm = CreateDefaultSubobject<USpringArmComponent>(TEXT("SpringArm"));
SpringArm->SetupAttachment(RootComponent);
SpringArm->TargetArmLength = 300.f;
SpringArm->bUsePawnControlRotation = true;

UCameraComponent* Camera = CreateDefaultSubobject<UCameraComponent>(TEXT("Camera"));
Camera->SetupAttachment(SpringArm, USpringArmComponent::SocketName);
```

```cpp
void AMyCharacter::Move(const FInputActionValue& Value)
{
    const FVector2D Axis = Value.Get<FVector2D>();
    if (!Controller) return;
    const FRotator YawRot(0.f, Controller->GetControlRotation().Yaw, 0.f);
    AddMovementInput(FRotationMatrix(YawRot).GetUnitAxis(EAxis::X), Axis.Y);
    AddMovementInput(FRotationMatrix(YawRot).GetUnitAxis(EAxis::Y), Axis.X);
}
```

```cpp
#include "Kismet/GameplayStatics.h"

FHitResult Hit;
APlayerController* PC = Cast<APlayerController>(GetController());
if (PC && PC->PlayerCameraManager)
{
    FVector Start = PC->PlayerCameraManager->GetCameraLocation();
    FVector End = Start + PC->PlayerCameraManager->GetCameraRotation().Vector() * 10000.f;
    GetWorld()->LineTraceSingleByChannel(Hit, Start, End, ECC_Visibility);
}
```

```cpp
WeaponMesh->AttachToComponent(GetMesh(), FAttachmentTransformRules::SnapToTargetNotIncludingScale, TEXT("hand_r"));
```
