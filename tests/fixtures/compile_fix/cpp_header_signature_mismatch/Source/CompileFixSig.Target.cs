using UnrealBuildTool;

public class CompileFixSigTarget : TargetRules
{
	public CompileFixSigTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixSig");
	}
}
