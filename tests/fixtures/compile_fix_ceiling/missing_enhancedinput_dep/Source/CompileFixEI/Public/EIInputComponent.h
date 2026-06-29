#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "EIInputComponent.generated.h"

class UInputAction;
class UInputComponent;

UCLASS()
class COMPILEFIXEI_API UEIInputComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	void BindEnhancedInput(UInputComponent* PlayerInputComponent);

	UPROPERTY(EditAnywhere, Category = "Input")
	UInputAction* JumpAction;
};
