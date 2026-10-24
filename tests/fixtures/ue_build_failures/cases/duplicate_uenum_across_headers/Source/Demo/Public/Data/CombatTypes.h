#pragma once

#include "CoreMinimal.h"
#include "CombatTypes.generated.h"

UENUM(BlueprintType)
enum class ECombatState : uint8
{
	Idle,
	Firing,
	Dead
};
