using UnrealBuildTool;

public class CompileFixGenHTarget : TargetRules
{
	public CompileFixGenHTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixGenH");
	}
}
