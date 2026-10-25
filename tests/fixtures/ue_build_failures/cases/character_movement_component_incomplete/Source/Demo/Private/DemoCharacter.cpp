#include "DemoCharacter.h"

ADemoCharacter::ADemoCharacter()
{
	GetCharacterMovement()->JumpZVelocity = 420.f;
}

void ADemoCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	Super::SetupPlayerInputComponent(PlayerInputComponent);
}
