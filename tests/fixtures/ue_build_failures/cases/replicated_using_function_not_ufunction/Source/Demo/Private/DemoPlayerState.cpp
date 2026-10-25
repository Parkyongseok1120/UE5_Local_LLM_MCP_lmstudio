#include "DemoPlayerState.h"
#include "Net/UnrealNetwork.h"

void ADemoPlayerState::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
	Super::GetLifetimeReplicatedProps(OutLifetimeProps);
	DOREPLIFETIME(ADemoPlayerState, Team);
	DOREPLIFETIME(ADemoPlayerState, KillCount);
}

void ADemoPlayerState::OnRep_Team()
{
}

void ADemoPlayerState::OnRep_Score()
{
}
