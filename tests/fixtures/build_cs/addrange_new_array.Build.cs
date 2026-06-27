using UnrealBuildTool;

public class CoreGame : ModuleRules
{
    public CoreGame(ReadOnlyTargetRules Target) : base(Target)
    {
        PublicDependencyModuleNames.AddRange(new[]
        {
            "Core",
            "CoreUObject",
            "Engine"
        });
    }
}
