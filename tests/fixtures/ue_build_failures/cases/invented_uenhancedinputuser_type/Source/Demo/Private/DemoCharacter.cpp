#include "DemoCharacter.h"
#include "GameFramework/PlayerController.h"
#include "EnhancedInputSubsystems.h"

ADemoCharacter::ADemoCharacter()
{
}

void ADemoCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	Super::SetupPlayerInputComponent(PlayerInputComponent);
	APlayerController* PC = Cast<APlayerController>(GetController());
	if (UEnhancedInputUser* InputUser = Cast<UEnhancedInputUser>(PC))
	{
	}
}
