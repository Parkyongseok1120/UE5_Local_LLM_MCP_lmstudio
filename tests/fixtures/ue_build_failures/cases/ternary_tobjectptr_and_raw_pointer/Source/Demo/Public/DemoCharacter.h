#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Character.h"
#include "DemoCharacter.generated.h"

class UInputAction;

UCLASS()
class DEMO_API ADemoCharacter : public ACharacter
{
	GENERATED_BODY()

public:
	ADemoCharacter();

protected:
	virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;
	UInputAction* MakeFallbackAction() const;

	UPROPERTY(EditDefaultsOnly, Category = "Input")
	TObjectPtr<UInputAction> MoveAction;
};
