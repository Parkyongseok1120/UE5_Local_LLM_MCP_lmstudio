#pragma once
#include "GameFramework/Info.h"
#include "GameModeBase.generated.h"

UCLASS()
class ENGINE_API AGameModeBase : public AInfo
{
	GENERATED_BODY()
public:
	UPROPERTY(EditAnywhere, NoClear, BlueprintReadOnly, Category=Classes)
	TSubclassOf<AGameStateBase> GameStateClass;

	UPROPERTY(EditAnywhere, NoClear, BlueprintReadOnly, Category=Classes)
	TSubclassOf<APlayerState> PlayerStateClass;

	UPROPERTY(EditAnywhere, NoClear, BlueprintReadOnly, Category=Classes)
	TSubclassOf<APawn> DefaultPawnClass;

	UFUNCTION(BlueprintNativeEvent, Category=Game)
	AActor* ChoosePlayerStart(AController* Player);
};
