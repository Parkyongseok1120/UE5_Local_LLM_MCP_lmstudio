using UnrealBuildTool;

public class CompileFixLSEditorTarget : TargetRules
{
	public CompileFixLSEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixLS");
		bOverrideBuildEnvironment = true;
	}
}
