#include "DemoGameState.h"
#include "Net/UnrealNetwork.h"

void ADemoGameState::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& O) const
{
	Super::GetLifetimeReplicatedProps(O);
	DOREPLIFETIME(ADemoGameState, RedTeamScore);
}
