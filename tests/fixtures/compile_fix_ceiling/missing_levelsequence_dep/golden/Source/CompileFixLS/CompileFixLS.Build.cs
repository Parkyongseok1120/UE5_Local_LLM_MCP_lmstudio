using UnrealBuildTool;

public class CompileFixLS : ModuleRules
{
	public CompileFixLS(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine", "LevelSequence" });
	}
}
