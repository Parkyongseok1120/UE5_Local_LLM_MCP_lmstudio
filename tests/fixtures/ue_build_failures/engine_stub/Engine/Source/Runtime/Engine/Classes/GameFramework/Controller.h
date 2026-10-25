#pragma once
#include "GameFramework/Actor.h"
#include "Controller.generated.h"

UCLASS()
class ENGINE_API AController : public AActor
{
	GENERATED_BODY()
public:
	virtual void Possess(APawn* InPawn);
};
