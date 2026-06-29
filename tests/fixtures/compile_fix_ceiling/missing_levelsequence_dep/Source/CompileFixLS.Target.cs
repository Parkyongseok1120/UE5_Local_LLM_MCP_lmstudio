using UnrealBuildTool;

public class CompileFixLSTarget : TargetRules
{
	public CompileFixLSTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixLS");
	}
}
