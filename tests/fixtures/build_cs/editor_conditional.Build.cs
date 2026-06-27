using UnrealBuildTool;

public class EditorGame : ModuleRules
{
    public EditorGame(ReadOnlyTargetRules Target) : base(Target)
    {
        PublicDependencyModuleNames.Add("Core");
        if (Target.bBuildEditor)
        {
            PrivateDependencyModuleNames.Add("UnrealEd");
        }
    }
}
