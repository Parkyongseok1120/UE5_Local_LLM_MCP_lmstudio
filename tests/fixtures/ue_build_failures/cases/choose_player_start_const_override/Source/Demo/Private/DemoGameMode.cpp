#include "DemoGameMode.h"

AActor* ADemoGameMode::ChoosePlayerStart_Implementation(AController* Player) const
{
	return Super::ChoosePlayerStart_Implementation(Player);
}
