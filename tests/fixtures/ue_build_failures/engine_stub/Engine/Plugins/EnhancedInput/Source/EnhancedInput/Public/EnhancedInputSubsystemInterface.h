#pragma once
#include "EnhancedInputSubsystemInterface.generated.h"

UINTERFACE()
class ENHANCEDINPUT_API UEnhancedInputSubsystemInterface : public UInterface
{
	GENERATED_BODY()
};

class ENHANCEDINPUT_API IEnhancedInputSubsystemInterface
{
	GENERATED_BODY()
public:
	virtual void AddMappingContext(const UInputMappingContext* MappingContext, int32 Priority);
};
