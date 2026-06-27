#include "Combat/SoulsCombatComponent.h"
#include "Engine/World.h"

void USoulsCombatComponent::ApplyStagger(float Amount)
{
	StaggerMeter += Amount;
}

void USoulsCombatComponent::PerformDodge()
{
	// Dodge implementation
}

void USoulsCombatComponent::AttackTrace()
{
	FHitResult Hit;
	GetWorld()->LineTraceSingleByChannel(Hit, FVector::ZeroVector, FVector(100.f, 0.f, 0.f), ECC_Visibility);
}
