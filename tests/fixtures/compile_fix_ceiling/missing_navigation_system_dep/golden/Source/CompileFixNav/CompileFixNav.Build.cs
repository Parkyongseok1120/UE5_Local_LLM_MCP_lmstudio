using UnrealBuildTool;

public class CompileFixNav : ModuleRules
{
	public CompileFixNav(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine", "NavigationSystem" });
	}
}
