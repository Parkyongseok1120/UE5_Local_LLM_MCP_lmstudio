#include "DemoCharacter.h"

ADemoCharacter::ADemoCharacter()
{
}

void ADemoCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	Super::SetupPlayerInputComponent(PlayerInputComponent);
	const float Yaw = 2.f * LookSensitivity;
	AddControllerYawInput(Yaw);
}
