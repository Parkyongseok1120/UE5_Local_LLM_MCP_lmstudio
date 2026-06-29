#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "InputCoreTypes.h"
#include "InputProbe.generated.h"

UCLASS()
class COMPILEFIXIC_API UInputProbe : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere)
	FKey ActionKey;
};
