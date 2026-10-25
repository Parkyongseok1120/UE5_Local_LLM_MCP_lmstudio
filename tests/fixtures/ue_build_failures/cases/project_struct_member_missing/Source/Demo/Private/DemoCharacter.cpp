#include "DemoCharacter.h"

ADemoCharacter::ADemoCharacter()
{
}

void ADemoCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	Super::SetupPlayerInputComponent(PlayerInputComponent);
	const FCharacterStats& Stats = CharacterStats;
	UE_LOG(LogTemp, Log, TEXT("%f"), Stats.Health);
}
