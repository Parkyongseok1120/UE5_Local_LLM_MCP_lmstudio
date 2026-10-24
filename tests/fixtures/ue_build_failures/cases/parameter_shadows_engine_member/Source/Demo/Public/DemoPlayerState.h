#pragma once

#include "CoreMinimal.h"
#include "GameFramework/PlayerState.h"
#include "DemoPlayerState.generated.h"

UCLASS()
class DEMO_API ADemoPlayerState : public APlayerState
{
	GENERATED_BODY()

public:
	void ApplyDamage(float Amount, AController* Instigator);

protected:
	float CurrentHealth = 100.f;
};
