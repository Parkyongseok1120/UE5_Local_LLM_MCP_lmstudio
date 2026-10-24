#include "DemoCharacter.h"
#include "InputAction.h"

ADemoCharacter::ADemoCharacter()
{
}

void ADemoCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	Super::SetupPlayerInputComponent(PlayerInputComponent);
}

UInputAction* ADemoCharacter::MakeFallbackAction(const FName& Name, EInputValueType Type) const
{
	return nullptr;
}
