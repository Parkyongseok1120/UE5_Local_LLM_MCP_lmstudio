using UnrealBuildTool;

public class CompileFixTagsTarget : TargetRules
{
	public CompileFixTagsTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixTags");
	}
}
