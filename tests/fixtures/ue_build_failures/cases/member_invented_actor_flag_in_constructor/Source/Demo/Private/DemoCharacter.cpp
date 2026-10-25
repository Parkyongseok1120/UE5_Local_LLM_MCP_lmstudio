#include "DemoCharacter.h"

ADemoCharacter::ADemoCharacter()
{
	bReplicates = true;
	bAutoDestroyWhenHealthLessThanOrEqualToZero = false;
}

void ADemoCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	Super::SetupPlayerInputComponent(PlayerInputComponent);
}
