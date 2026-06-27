using UnrealBuildTool;

public class SampleGame : ModuleRules
{
    public SampleGame(ReadOnlyTargetRules Target) : base(Target)
    {
        PublicDependencyModuleNames.AddRange(new string[]
        {
            "Core",
            "Engine"
        });
    }
}
