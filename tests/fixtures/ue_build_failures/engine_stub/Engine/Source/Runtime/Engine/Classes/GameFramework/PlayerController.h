#pragma once
#include "GameFramework/Controller.h"
#include "PlayerController.generated.h"

UCLASS()
class ENGINE_API APlayerController : public AController
{
	GENERATED_BODY()
public:
	ULocalPlayer* GetLocalPlayer() const;
	virtual void SetupInputComponent();

	UPROPERTY()
	TObjectPtr<UInputComponent> InputComponent;
};
