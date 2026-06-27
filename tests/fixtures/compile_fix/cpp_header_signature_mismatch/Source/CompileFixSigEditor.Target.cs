using UnrealBuildTool;

public class CompileFixSigEditorTarget : TargetRules
{
	public CompileFixSigEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixSig");
		bOverrideBuildEnvironment = true;
	}
}
