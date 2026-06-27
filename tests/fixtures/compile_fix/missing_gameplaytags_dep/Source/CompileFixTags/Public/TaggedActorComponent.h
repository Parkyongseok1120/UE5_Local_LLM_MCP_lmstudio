#pragma once

#include "CoreMinimal.h"
#include "GameplayTagContainer.h"
#include "TaggedActorComponent.generated.h"
#include "Components/ActorComponent.h"

UCLASS()
class COMPILEFIXTAGS_API UTaggedActorComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere)
	FGameplayTagContainer Tags;
};
