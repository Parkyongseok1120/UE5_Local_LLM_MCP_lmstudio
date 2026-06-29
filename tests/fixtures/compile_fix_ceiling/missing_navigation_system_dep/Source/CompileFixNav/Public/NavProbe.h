#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "NavigationSystem.h"
#include "NavProbe.generated.h"

UCLASS()
class COMPILEFIXNAV_API UNavProbe : public UActorComponent
{
	GENERATED_BODY()

public:
	void ProbeNav();
};
