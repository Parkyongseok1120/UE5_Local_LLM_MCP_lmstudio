#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "HackComponent.generated.h"

UCLASS(ClassGroup=(Custom), meta=(BlueprintSpawnableComponent))
class COMPILEFIXSIG_API UHackComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	void DoWork(int Value);
};
