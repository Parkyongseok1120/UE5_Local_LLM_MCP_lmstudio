#pragma once
#include "GameFramework/Actor.h"
#include "Pawn.generated.h"

UCLASS()
class ENGINE_API APawn : public AActor
{
	GENERATED_BODY()
public:
	virtual float TakeDamage(float Damage, struct FDamageEvent const& DamageEvent, AController* EventInstigator, AActor* DamageCauser) override;
	virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent);
	AController* GetController() const;
	void AddControllerYawInput(float Val);
};
