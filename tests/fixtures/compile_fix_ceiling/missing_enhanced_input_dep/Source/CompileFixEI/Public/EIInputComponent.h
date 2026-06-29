#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "EIInputComponent.generated.h"

class UInputAction;

UCLASS()
class COMPILEFIXEI_API UEIInputComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	virtual void SetupPlayerInputComponent(class UInputComponent* PlayerInputComponent) override;

	UPROPERTY(EditAnywhere, Category = "Input")
	UInputAction* JumpAction;

private:
	void OnJump();
};
