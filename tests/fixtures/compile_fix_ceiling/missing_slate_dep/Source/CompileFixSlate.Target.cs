using UnrealBuildTool;

public class CompileFixSlateTarget : TargetRules
{
	public CompileFixSlateTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixSlate");
	}
}
