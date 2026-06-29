#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "LevelSequence.h"
#include "LevelSequenceProbe.generated.h"

UCLASS()
class COMPILEFIXLS_API ULevelSequenceProbe : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere)
	ULevelSequence* SequenceAsset;
};
