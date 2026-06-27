using UnrealBuildTool;

public class TagsGame : ModuleRules
{
    public TagsGame(ReadOnlyTargetRules Target) : base(Target)
    {
        PublicDependencyModuleNames.Add("GameplayTags");
        PrivateDependencyModuleNames.Add("UMG");
    }
}
