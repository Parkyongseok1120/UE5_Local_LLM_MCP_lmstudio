using UnrealBuildTool;

public class CompileFixEdEditorTarget : TargetRules
{
	public CompileFixEdEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixEd");
		bOverrideBuildEnvironment = true;
	}
}
