using UnrealBuildTool;

public class CompileFixEIEditorTarget : TargetRules
{
	public CompileFixEIEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixEI");
		bOverrideBuildEnvironment = true;
	}
}
