#pragma once
#include "Components/ActorComponent.h"
#include "CharacterMovementComponent.generated.h"

UCLASS()
class ENGINE_API UCharacterMovementComponent : public UActorComponent
{
	GENERATED_BODY()
public:
	UPROPERTY(EditAnywhere)
	float JumpZVelocity;
};
