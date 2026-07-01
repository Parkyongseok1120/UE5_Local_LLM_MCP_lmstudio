#pragma once

#include "Kismet/BlueprintFunctionLibrary.h"
#include "LmStudioGraphExporterLibrary.generated.h"

UCLASS()
class LMSTUDIOGRAPHEXPORTER_API ULmStudioGraphExporterLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintCallable, CallInEditor, Category = "LM Studio|Metadata")
    static FString ExportBlueprintMetadata(const FString& ContentPath, const FString& OutputPath);
};
