#pragma once
#include "GameFramework/Info.h"
#include "PlayerState.generated.h"

UCLASS()
class ENGINE_API APlayerState : public AInfo
{
	GENERATED_BODY()
public:
	UFUNCTION()
	virtual void OnRep_Score();

private:
	UPROPERTY(ReplicatedUsing=OnRep_Score, Category=PlayerState)
	float Score;
};
