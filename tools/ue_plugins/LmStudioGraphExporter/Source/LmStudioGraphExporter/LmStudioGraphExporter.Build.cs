using UnrealBuildTool;

public class LmStudioGraphExporter : ModuleRules
{
    public LmStudioGraphExporter(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new string[]
        {
            "Core",
            "CoreUObject",
            "Engine"
        });

        PrivateDependencyModuleNames.AddRange(new string[]
        {
            "AssetRegistry",
            "BlueprintGraph",
            "Json",
            "JsonUtilities",
            "Kismet",
            "UnrealEd"
        });
    }
}
