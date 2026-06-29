using UnrealBuildTool;

public class CompileFixMSTarget : TargetRules
{
	public CompileFixMSTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixMS");
	}
}
