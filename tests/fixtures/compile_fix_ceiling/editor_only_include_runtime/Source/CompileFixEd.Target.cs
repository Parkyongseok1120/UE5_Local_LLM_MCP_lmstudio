using UnrealBuildTool;

public class CompileFixEdTarget : TargetRules
{
	public CompileFixEdTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixEd");
	}
}
