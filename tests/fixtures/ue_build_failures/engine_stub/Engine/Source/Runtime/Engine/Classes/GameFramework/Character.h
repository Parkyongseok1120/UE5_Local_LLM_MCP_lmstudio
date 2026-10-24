#pragma once
#include "GameFramework/Pawn.h"
#include "Character.generated.h"

class UCharacterMovementComponent;

UCLASS()
class ENGINE_API ACharacter : public APawn
{
	GENERATED_BODY()
public:
	UCharacterMovementComponent* GetCharacterMovement() const;
	virtual void Jump();
	virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;
};
