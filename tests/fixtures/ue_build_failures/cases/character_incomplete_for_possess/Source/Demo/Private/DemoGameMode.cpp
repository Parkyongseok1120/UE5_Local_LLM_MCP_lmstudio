#include "DemoGameMode.h"
#include "Engine/World.h"

void ADemoGameMode::RespawnPlayer(AController* Controller)
{
	ACharacter* NewCharacter = GetWorld()->SpawnActor<ACharacter>(DefaultPawnClass, FVector::ZeroVector, FRotator::ZeroRotator);
	Controller->Possess(NewCharacter);
}
