#pragma once

#include "CoreMinimal.h"
#include "GameFramework/PlayerState.h"
#include "DemoPlayerState.generated.h"

UCLASS()
class DEMO_API ADemoPlayerState : public APlayerState
{
	GENERATED_BODY()

public:
	virtual void GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const override;

	UPROPERTY(ReplicatedUsing = OnRep_Team)
	int32 Team = 0;

	UPROPERTY(ReplicatedUsing = OnRep_Score)
	int32 KillCount = 0;

	void OnRep_Team();
	virtual void OnRep_Score() override;
};
