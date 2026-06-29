using UnrealBuildTool;

public class CompileFixUMGEditorTarget : TargetRules
{
	public CompileFixUMGEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixUMG");
		bOverrideBuildEnvironment = true;
	}
}
