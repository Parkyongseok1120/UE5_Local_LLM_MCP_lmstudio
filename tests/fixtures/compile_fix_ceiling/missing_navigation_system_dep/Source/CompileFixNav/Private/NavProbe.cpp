#include "NavProbe.h"

void UNavProbe::ProbeNav()
{
	if (UWorld* World = GetWorld())
	{
		UNavigationSystemV1::GetCurrent(World);
	}
}
