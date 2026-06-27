#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "SoulsCombatComponent.generated.h"

UCLASS(ClassGroup=(Custom), meta=(BlueprintSpawnableComponent))
class SOULSLIKE_API USoulsCombatComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	void ApplyStagger(float Amount);
	void PerformDodge();
	void AttackTrace();

	UPROPERTY(Replicated)
	float StaggerMeter = 0.f;
};
