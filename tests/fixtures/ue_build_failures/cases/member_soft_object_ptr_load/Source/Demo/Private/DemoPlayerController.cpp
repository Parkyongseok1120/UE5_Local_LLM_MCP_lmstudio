#include "DemoPlayerController.h"
#include "EnhancedInputSubsystems.h"
#include "InputMappingContext.h"

void ADemoPlayerController::SetupInputComponent()
{
	Super::SetupInputComponent();
	if (UInputMappingContext* MappingContext = CombatMappingContext.Load())
	{
		UE_LOG(LogTemp, Log, TEXT("%s"), *MappingContext->GetName());
	}
}
