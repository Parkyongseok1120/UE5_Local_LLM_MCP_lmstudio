using UnrealBuildTool;

public class CompileFixSlateEditorTarget : TargetRules
{
	public CompileFixSlateEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixSlate");
		bOverrideBuildEnvironment = true;
	}
}
