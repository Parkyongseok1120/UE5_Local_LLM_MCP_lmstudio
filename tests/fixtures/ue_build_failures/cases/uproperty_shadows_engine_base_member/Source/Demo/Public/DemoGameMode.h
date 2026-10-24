#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "DemoGameMode.generated.h"

UCLASS()
class DEMO_API ADemoGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	ADemoGameMode();

	UPROPERTY(EditDefaultsOnly, Category = "Classes")
	TSubclassOf<APlayerState> PlayerStateClass;
};
