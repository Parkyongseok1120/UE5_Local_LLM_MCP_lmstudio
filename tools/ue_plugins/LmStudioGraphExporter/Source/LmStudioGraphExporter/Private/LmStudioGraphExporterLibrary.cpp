#include "LmStudioGraphExporterLibrary.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Class.h"
#include "UObject/Package.h"

static FString LmStudioJsonString(const TSharedRef<FJsonObject>& Object)
{
    FString Output;
    const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
        TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Output);
    FJsonSerializer::Serialize(Object, Writer);
    return Output;
}

static FString LmStudioObjectName(const UObject* Object)
{
    return Object ? Object->GetName() : FString();
}

static FString LmStudioClassName(const UObject* Object)
{
    return Object ? Object->GetClass()->GetName() : FString();
}

static FString LmStudioPinDirection(const EEdGraphPinDirection Direction)
{
    switch (Direction)
    {
    case EGPD_Input:
        return TEXT("input");
    case EGPD_Output:
        return TEXT("output");
    default:
        return TEXT("unknown");
    }
}

static TSharedRef<FJsonObject> LmStudioPinToJson(const UEdGraphPin* Pin)
{
    TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
    if (!Pin)
    {
        return Row;
    }

    Row->SetStringField(TEXT("name"), Pin->PinName.ToString());
    Row->SetStringField(TEXT("direction"), LmStudioPinDirection(Pin->Direction));
    Row->SetStringField(TEXT("category"), Pin->PinType.PinCategory.ToString());
    Row->SetStringField(TEXT("subcategory"), Pin->PinType.PinSubCategory.ToString());
    Row->SetNumberField(TEXT("linked_to_count"), Pin->LinkedTo.Num());
    if (!Pin->DefaultValue.IsEmpty())
    {
        Row->SetStringField(TEXT("default_value"), Pin->DefaultValue);
    }
    if (Pin->DefaultObject)
    {
        Row->SetStringField(TEXT("default_object"), Pin->DefaultObject->GetPathName());
    }

    TArray<TSharedPtr<FJsonValue>> Links;
    for (const UEdGraphPin* LinkedPin : Pin->LinkedTo)
    {
        if (!LinkedPin)
        {
            continue;
        }
        const UEdGraphNode* LinkedNode = LinkedPin->GetOwningNode();
        if (!LinkedNode)
        {
            continue;
        }
        TSharedRef<FJsonObject> Link = MakeShared<FJsonObject>();
        Link->SetStringField(TEXT("node"), LinkedNode->GetName());
        Link->SetStringField(TEXT("node_title"), LinkedNode->GetNodeTitle(ENodeTitleType::ListView).ToString());
        Link->SetStringField(TEXT("pin"), LinkedPin->PinName.ToString());
        Links.Add(MakeShared<FJsonValueObject>(Link));
    }
    if (Links.Num() > 0)
    {
        Row->SetArrayField(TEXT("links"), Links);
    }
    return Row;
}

static TSharedRef<FJsonObject> LmStudioNodeToJson(const UEdGraphNode* Node, const FString& GraphName, TArray<TSharedPtr<FJsonValue>>& GraphLinks)
{
    TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
    if (!Node)
    {
        return Row;
    }

    Row->SetStringField(TEXT("name"), Node->GetName());
    Row->SetStringField(TEXT("class"), LmStudioClassName(Node));
    Row->SetStringField(TEXT("title"), Node->GetNodeTitle(ENodeTitleType::ListView).ToString());

    TArray<TSharedPtr<FJsonValue>> Pins;
    for (const UEdGraphPin* Pin : Node->Pins)
    {
        if (!Pin)
        {
            continue;
        }
        Pins.Add(MakeShared<FJsonValueObject>(LmStudioPinToJson(Pin)));
        if (Pin->Direction != EGPD_Output)
        {
            continue;
        }
        for (const UEdGraphPin* LinkedPin : Pin->LinkedTo)
        {
            if (!LinkedPin)
            {
                continue;
            }
            const UEdGraphNode* LinkedNode = LinkedPin->GetOwningNode();
            if (!LinkedNode)
            {
                continue;
            }
            TSharedRef<FJsonObject> Link = MakeShared<FJsonObject>();
            Link->SetStringField(TEXT("graph"), GraphName);
            Link->SetStringField(TEXT("from_node"), Node->GetName());
            Link->SetStringField(TEXT("from_pin"), Pin->PinName.ToString());
            Link->SetStringField(TEXT("to_node"), LinkedNode->GetName());
            Link->SetStringField(TEXT("to_pin"), LinkedPin->PinName.ToString());
            GraphLinks.Add(MakeShared<FJsonValueObject>(Link));
        }
    }
    Row->SetArrayField(TEXT("pins"), Pins);
    return Row;
}

static TSharedRef<FJsonObject> LmStudioGraphToJson(const UEdGraph* Graph, TArray<TSharedPtr<FJsonValue>>& GraphLinks)
{
    TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
    if (!Graph)
    {
        return Row;
    }

    const FString GraphName = Graph->GetName();
    Row->SetStringField(TEXT("name"), GraphName);
    Row->SetNumberField(TEXT("node_count"), Graph->Nodes.Num());

    TArray<TSharedPtr<FJsonValue>> Nodes;
    for (const UEdGraphNode* Node : Graph->Nodes)
    {
        if (Node)
        {
            Nodes.Add(MakeShared<FJsonValueObject>(LmStudioNodeToJson(Node, GraphName, GraphLinks)));
        }
    }
    Row->SetArrayField(TEXT("nodes"), Nodes);
    return Row;
}

static TArray<TSharedPtr<FJsonValue>> LmStudioStringArray(const TArray<FString>& Values)
{
    TArray<TSharedPtr<FJsonValue>> Items;
    for (const FString& Value : Values)
    {
        Items.Add(MakeShared<FJsonValueString>(Value));
    }
    return Items;
}

static TSharedRef<FJsonObject> LmStudioBlueprintToJson(const FAssetData& Asset, UBlueprint* Blueprint, IAssetRegistry& AssetRegistry)
{
    TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
    Row->SetStringField(TEXT("asset_path"), Asset.PackageName.ToString());
    Row->SetStringField(TEXT("asset_type"), Asset.AssetClassPath.GetAssetName().ToString());
    Row->SetStringField(TEXT("generated_class"), Blueprint && Blueprint->GeneratedClass ? Blueprint->GeneratedClass->GetName() : Asset.AssetName.ToString());

    if (!Blueprint)
    {
        return Row;
    }

    if (Blueprint->ParentClass)
    {
        Row->SetStringField(TEXT("parent_class"), Blueprint->ParentClass->GetName());
    }

    TArray<FString> Variables;
    for (const FBPVariableDescription& Variable : Blueprint->NewVariables)
    {
        Variables.Add(Variable.VarName.ToString());
    }
    if (Variables.Num() > 0)
    {
        Row->SetArrayField(TEXT("variables"), LmStudioStringArray(Variables));
    }

    TArray<UEdGraph*> Graphs;
    Blueprint->GetAllGraphs(Graphs);

    TArray<FString> Functions;
    TArray<TSharedPtr<FJsonValue>> GraphRows;
    TArray<TSharedPtr<FJsonValue>> GraphLinks;
    for (const UEdGraph* Graph : Graphs)
    {
        if (!Graph)
        {
            continue;
        }
        const FString GraphName = Graph->GetName();
        if (GraphName != TEXT("EventGraph") && GraphName != TEXT("UserConstructionScript") && GraphName != TEXT("ConstructionScript"))
        {
            Functions.Add(GraphName);
        }
        GraphRows.Add(MakeShared<FJsonValueObject>(LmStudioGraphToJson(Graph, GraphLinks)));
    }
    if (Functions.Num() > 0)
    {
        Row->SetArrayField(TEXT("functions"), LmStudioStringArray(Functions));
    }
    if (GraphRows.Num() > 0)
    {
        Row->SetArrayField(TEXT("graphs"), GraphRows);
        Row->SetStringField(TEXT("graph_access"), TEXT("Blueprint graph nodes exported by LM Studio C++ editor plugin."));
    }
    if (GraphLinks.Num() > 0)
    {
        Row->SetArrayField(TEXT("graph_links"), GraphLinks);
    }

    TArray<FName> Dependencies;
    if (AssetRegistry.GetDependencies(Asset.PackageName, Dependencies))
    {
        TArray<FString> DependencyStrings;
        for (const FName& Dependency : Dependencies)
        {
            DependencyStrings.Add(Dependency.ToString());
        }
        Row->SetArrayField(TEXT("dependencies"), LmStudioStringArray(DependencyStrings));
    }
    return Row;
}

FString ULmStudioGraphExporterLibrary::ExportBlueprintMetadata(const FString& ContentPath, const FString& OutputPath)
{
    TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("ok"), false);
    Result->SetStringField(TEXT("outputPath"), OutputPath);

    FAssetRegistryModule& RegistryModule = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
    IAssetRegistry& AssetRegistry = RegistryModule.Get();

    TArray<FAssetData> Assets;
    AssetRegistry.GetAssetsByPath(FName(*ContentPath), Assets, true);

    FString Lines;
    int32 RowCount = 0;
    for (const FAssetData& Asset : Assets)
    {
        const FString ClassName = Asset.AssetClassPath.GetAssetName().ToString();
        if (!ClassName.Contains(TEXT("Blueprint")) && !ClassName.Contains(TEXT("Widget")))
        {
            continue;
        }

        UObject* LoadedAsset = Asset.GetAsset();
        UBlueprint* Blueprint = Cast<UBlueprint>(LoadedAsset);
        TSharedRef<FJsonObject> Row = LmStudioBlueprintToJson(Asset, Blueprint, AssetRegistry);
        Lines += LmStudioJsonString(Row);
        Lines += LINE_TERMINATOR;
        ++RowCount;
    }

    const FString Directory = FPaths::GetPath(OutputPath);
    IFileManager::Get().MakeDirectory(*Directory, true);
    if (!FFileHelper::SaveStringToFile(Lines, *OutputPath, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
    {
        Result->SetStringField(TEXT("error"), FString::Printf(TEXT("Failed to write %s"), *OutputPath));
        return LmStudioJsonString(Result);
    }

    Result->SetBoolField(TEXT("ok"), true);
    Result->SetNumberField(TEXT("rows"), RowCount);
    return LmStudioJsonString(Result);
}
