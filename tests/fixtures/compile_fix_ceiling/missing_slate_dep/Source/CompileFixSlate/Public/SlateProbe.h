#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "SlateProbe.generated.h"

UCLASS()
class COMPILEFIXSLATE_API USlateProbe : public UActorComponent
{
	GENERATED_BODY()

public:
	void TouchBrush();
};
