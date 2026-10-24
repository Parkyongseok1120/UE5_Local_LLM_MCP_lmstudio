#include "DemoGameMode.h"
#include "Engine/World.h"

void ADemoGameMode::HandleDeath(AController* Controller)
{
	GetWorldTimerManager().SetTimer(FTimerHandle(), [this, Controller]() { RespawnPlayer(Controller); }, 3.f, false);
}

void ADemoGameMode::RespawnPlayer(AController* Controller)
{
}
