#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "AIController.h"
#include "AIComponent.generated.h"

UCLASS()
class COMPILEFIXAI_API UAIComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere)
	AAIController* CachedController;
};
