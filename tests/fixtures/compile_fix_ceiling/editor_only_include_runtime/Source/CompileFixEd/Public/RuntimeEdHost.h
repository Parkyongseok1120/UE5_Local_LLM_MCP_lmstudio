#pragma once

#include "CoreMinimal.h"
#include "EditorUtilityWidget.h"
#include "Components/ActorComponent.h"
#include "RuntimeEdHost.generated.h"

UCLASS()
class COMPILEFIXED_API URuntimeEdHost : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere, Category = "Editor")
	TSubclassOf<UEditorUtilityWidget> WidgetClass;
};
