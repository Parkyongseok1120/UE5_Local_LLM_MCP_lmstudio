using UnrealBuildTool;

public class CompileFixNavTarget : TargetRules
{
	public CompileFixNavTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixNav");
	}
}
