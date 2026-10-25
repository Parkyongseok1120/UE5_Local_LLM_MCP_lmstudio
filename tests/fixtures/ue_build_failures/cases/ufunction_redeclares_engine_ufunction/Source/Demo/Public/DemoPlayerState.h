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

	UPROPERTY(ReplicatedUsing = OnRep_Score)
	int32 KillCount = 0;

	UFUNCTION()
	void OnRep_Score();
};
