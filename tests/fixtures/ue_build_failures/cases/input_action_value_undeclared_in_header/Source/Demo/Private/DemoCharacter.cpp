#include "DemoCharacter.h"
#include "InputActionValue.h"

ADemoCharacter::ADemoCharacter()
{
}

void ADemoCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	Super::SetupPlayerInputComponent(PlayerInputComponent);
}

void ADemoCharacter::Move(const FInputActionValue& Value)
{
}
