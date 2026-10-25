#pragma once
#include "Components/InputComponent.h"
#include "EnhancedInputComponent.generated.h"

UCLASS()
class ENHANCEDINPUT_API UEnhancedInputComponent : public UInputComponent
{
	GENERATED_BODY()
public:
	template<class UserClass, typename FuncType>
	FEnhancedInputActionEventBinding& BindAction(const UInputAction* Action, ETriggerEvent TriggerEvent, UserClass* Object, FuncType Func);
};
