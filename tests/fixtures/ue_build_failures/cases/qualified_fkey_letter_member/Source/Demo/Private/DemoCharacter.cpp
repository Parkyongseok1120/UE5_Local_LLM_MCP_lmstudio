#include "DemoCharacter.h"
#include "InputMappingContext.h"
#include "InputAction.h"

ADemoCharacter::ADemoCharacter()
{
}

void ADemoCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	Super::SetupPlayerInputComponent(PlayerInputComponent);
	UInputMappingContext* Mapping = NewObject<UInputMappingContext>(this);
	UInputAction* MoveAction = NewObject<UInputAction>(this);
	Mapping->MapKey(MoveAction, FKey::W);
}
