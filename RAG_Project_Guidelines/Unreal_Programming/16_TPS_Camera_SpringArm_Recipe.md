# TPS Camera, SpringArm, and LineTrace Recipe (UE 5.8)

## Intent

Third-person shooter prototype: camera behind character, camera-relative move, screen-center line trace, weapon attach.

## Keywords

SpringArm, CameraComponent, USpringArmComponent, UCameraComponent, third person, TPS, line trace, LineTraceSingleByChannel, weapon socket, attach component

Korean: 3인칭, 스프링암, 카메라, 라인트레이스, 조준, 무기 소켓

## Minimal character setup

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

Use `ACharacter::GetMesh()` for the skeletal mesh — do not create a second body mesh in TPS unless intentional.

## Camera-relative movement

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

## Line trace (screen center)

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

## Weapon attach

Attach weapon mesh to character mesh socket (e.g. `hand_r`):

```cpp
WeaponMesh->AttachToComponent(GetMesh(), FAttachmentTransformRules::SnapToTargetNotIncludingScale, TEXT("hand_r"));
```

For TPS feel, attach trace origin to **camera**, weapon visual to **hand socket**.

## Common mistakes

| Mistake | Fix |
|---------|-----|
| `#include "Game/Framework/Character.h"` | `#include "GameFramework/Character.h"` |
| Weapon on Camera for visual + hand | Visual on hand, trace from camera |
| Input bind in BeginPlay on PC | `SetupInputComponent` |
| Duplicate `USkeletalMeshComponent` body | Use `GetMesh()` |

## RAG mode

`prototype_component` + genre `shooter` for new TPS work; `compile_fix` for include errors.
