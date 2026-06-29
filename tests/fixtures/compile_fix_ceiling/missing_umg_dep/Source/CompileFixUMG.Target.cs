using UnrealBuildTool;

public class CompileFixUMGTarget : TargetRules
{
	public CompileFixUMGTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixUMG");
	}
}
