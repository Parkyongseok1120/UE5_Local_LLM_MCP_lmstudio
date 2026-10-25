#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "TimerManager.h"
#include "DemoGameMode.generated.h"

UCLASS()
class DEMO_API ADemoGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	void HandleDeath(AController* Controller);
	void RespawnPlayer(AController* Controller);

protected:
	FTimerHandle RespawnTimerHandle;
};
