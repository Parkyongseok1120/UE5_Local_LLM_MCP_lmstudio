#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Character.h"
#include "GameFramework/SpringArmComponent.h"
#include "Camera/CameraComponent.h"
#include "SoulslikeCharacter.generated.h"

UCLASS()
class SOULSLIKE_API ASoulslikeCharacter : public ACharacter
{
	GENERATED_BODY()

public:
	ASoulslikeCharacter();

	UPROPERTY(VisibleAnywhere)
	TObjectPtr<USpringArmComponent> SpringArmComponent;

	UPROPERTY(VisibleAnywhere)
	TObjectPtr<UCameraComponent> FollowCamera;
};
