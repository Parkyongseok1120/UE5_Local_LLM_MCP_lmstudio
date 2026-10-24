#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Character.h"
#include "Data/CharacterStats.h"
#include "DemoCharacter.generated.h"

UCLASS()
class DEMO_API ADemoCharacter : public ACharacter
{
	GENERATED_BODY()

public:
	ADemoCharacter();

protected:
	virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;

protected:
	UPROPERTY(EditDefaultsOnly, Category = "Stats")
	FCharacterStats CharacterStats;
};
