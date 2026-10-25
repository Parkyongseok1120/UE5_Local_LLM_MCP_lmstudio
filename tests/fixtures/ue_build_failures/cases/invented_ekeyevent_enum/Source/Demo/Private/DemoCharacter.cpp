#include "DemoCharacter.h"
#include "Components/InputComponent.h"
#include "InputCoreTypes.h"

ADemoCharacter::ADemoCharacter()
{
}

void ADemoCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	Super::SetupPlayerInputComponent(PlayerInputComponent);
	PlayerInputComponent->BindKey(EKeys::SpaceBar, EKeyEvent::Pressed, this, &ACharacter::Jump);
}
