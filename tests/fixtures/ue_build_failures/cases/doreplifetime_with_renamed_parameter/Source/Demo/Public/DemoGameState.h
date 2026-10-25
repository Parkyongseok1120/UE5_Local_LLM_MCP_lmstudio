#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameStateBase.h"
#include "DemoGameState.generated.h"

UCLASS()
class DEMO_API ADemoGameState : public AGameStateBase
{
	GENERATED_BODY()

public:
	virtual void GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const override;

	UPROPERTY(Replicated)
	int32 RedTeamScore = 0;
};
