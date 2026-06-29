#include "EIInputComponent.h"
#include "EnhancedInputComponent.h"
#include "InputAction.h"

void UEIInputComponent::BindEnhancedInput(UInputComponent* PlayerInputComponent)
{
	UEnhancedInputComponent* EnhancedInput = Cast<UEnhancedInputComponent>(PlayerInputComponent);
	(void)EnhancedInput;
	(void)JumpAction;
}
