#include "DemoCharacter.h"

ADemoCharacter::ADemoCharacter()
{
}

void ADemoCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	Super::SetupPlayerInputComponent(PlayerInputComponent);
	if (ACharacter* Target = Cast<ACharacter>(GetOwner()))
	{
		Target->TakeDamage(10.f, FDamageEvent(), GetController(), this);
	}
}
