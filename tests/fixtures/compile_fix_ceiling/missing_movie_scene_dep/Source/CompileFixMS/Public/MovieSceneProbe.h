#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "MovieScene.h"
#include "MovieSceneProbe.generated.h"

UCLASS()
class COMPILEFIXMS_API UMovieSceneProbe : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere)
	UMovieScene* TrackedScene;
};
