using UnrealBuildTool;

public class CompileFixICEditorTarget : TargetRules
{
	public CompileFixICEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixIC");
		bOverrideBuildEnvironment = true;
	}
}
