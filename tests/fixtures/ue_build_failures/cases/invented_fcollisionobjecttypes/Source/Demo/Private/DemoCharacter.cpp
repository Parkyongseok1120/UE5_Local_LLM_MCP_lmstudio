#include "DemoCharacter.h"
#include "Engine/World.h"

ADemoCharacter::ADemoCharacter()
{
}

void ADemoCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
	Super::SetupPlayerInputComponent(PlayerInputComponent);
	FHitResult Hit;
	const FVector Start = GetActorLocation();
	const FVector End = Start + GetActorForwardVector() * 1000.f;
	GetWorld()->LineTraceSingleByObjectType(Hit, Start, End, FCollisionObjectTypes::AllStatic);
}
