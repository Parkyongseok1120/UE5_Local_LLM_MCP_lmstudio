#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Character.h"
#include "Engine/SoundBase.h"
#include "DemoCharacter.generated.h"

UCLASS()
class DEMO_API ADemoCharacter : public ACharacter
{
	GENERATED_BODY()

public:
	ADemoCharacter();

protected:
	virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;
};
