#include "DemoCharacter.h"
#include "InputAction.h"

ADemoCharacter::ADemoCharacter()
{
}

void ADemoCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	Super::SetupPlayerInputComponent(PlayerInputComponent);
	UInputAction* Action = MoveAction ? MoveAction : MakeFallbackAction();
}

UInputAction* ADemoCharacter::MakeFallbackAction() const
{
	return nullptr;
}
