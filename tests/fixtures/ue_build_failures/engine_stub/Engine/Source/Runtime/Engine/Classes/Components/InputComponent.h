#pragma once
#include "Components/ActorComponent.h"
#include "InputComponent.generated.h"

UCLASS()
class ENGINE_API UInputComponent : public UActorComponent
{
	GENERATED_BODY()
public:
	template<class UserClass>
	void BindKey(const FKey Key, const EInputEvent KeyEvent, UserClass* Object, void (UserClass::*Func)());
};
