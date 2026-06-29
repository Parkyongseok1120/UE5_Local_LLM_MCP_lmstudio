using UnrealBuildTool;

public class CompileFixMS : ModuleRules
{
	public CompileFixMS(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine", "MovieScene" });
	}
}
