#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "NiagaraSystem.h"
#include "NiagaraHost.generated.h"

UCLASS()
class COMPILEFIXNIAGARA_API UNiagaraHost : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere)
	UNiagaraSystem* BurstSystem;
};
