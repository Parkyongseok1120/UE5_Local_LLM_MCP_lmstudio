#pragma once
#include "InputMappingContext.generated.h"

UCLASS()
class ENHANCEDINPUT_API UInputMappingContext : public UObject
{
	GENERATED_BODY()
public:
	void MapKey(const UInputAction* Action, FKey ToKey);
};
