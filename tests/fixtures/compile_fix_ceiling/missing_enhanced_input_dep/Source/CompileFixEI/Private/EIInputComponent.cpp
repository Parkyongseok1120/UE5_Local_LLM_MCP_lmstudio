#include "EIInputComponent.h"
#include "EnhancedInputComponent.h"
#include "InputAction.h"

void UEIInputComponent::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	if (UEnhancedInputComponent* EnhancedInput = Cast<UEnhancedInputComponent>(PlayerInputComponent))
	{
		EnhancedInput->BindAction(JumpAction, ETriggerEvent::Started, this, &UEIInputComponent::OnJump);
	}
}

void UEIInputComponent::OnJump()
{
}
