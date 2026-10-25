#pragma once
#include "UObject/Object.h"
#include "Actor.generated.h"

UCLASS()
class ENGINE_API AActor : public UObject
{
	GENERATED_BODY()
public:
	UPROPERTY(Replicated)
	TObjectPtr<APawn> Instigator;

	UPROPERTY()
	uint8 bAutoDestroyWhenFinished:1;

	UPROPERTY(Replicated)
	uint8 bReplicates:1;

	virtual void BeginPlay();
	virtual void Tick(float DeltaSeconds);
	virtual void GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const;
	virtual float TakeDamage(float DamageAmount, struct FDamageEvent const& DamageEvent, AController* EventInstigator, AActor* DamageCauser);
	AActor* GetOwner() const;
	FVector GetActorLocation() const;
	FVector GetActorForwardVector() const;
	FTimerManager& GetWorldTimerManager() const;
};
