#include "DemoPlayerState.h"

void ADemoPlayerState::ApplyDamage(float Amount, AController* Instigator)
{
	CurrentHealth = FMath::Max(0.f, CurrentHealth - Amount);
}
