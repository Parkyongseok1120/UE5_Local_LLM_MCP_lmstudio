using UnrealBuildTool;

public class CompileFixAIEditorTarget : TargetRules
{
	public CompileFixAIEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixAI");
		bOverrideBuildEnvironment = true;
	}
}
