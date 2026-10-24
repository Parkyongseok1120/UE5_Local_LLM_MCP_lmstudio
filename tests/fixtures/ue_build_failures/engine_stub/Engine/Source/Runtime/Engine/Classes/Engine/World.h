#pragma once
#include "UObject/Object.h"

class ENGINE_API UWorld : public UObject
{
public:
	bool LineTraceSingleByObjectType(FHitResult& OutHit, const FVector& Start, const FVector& End, const FCollisionObjectQueryParams& ObjectQueryParams) const;
	template<class T>
	T* SpawnActor(UClass* Class, const FVector& Location, const FRotator& Rotation);
};
