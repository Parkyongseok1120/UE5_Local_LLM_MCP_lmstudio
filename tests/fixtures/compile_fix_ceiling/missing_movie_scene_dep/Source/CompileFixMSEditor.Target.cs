using UnrealBuildTool;

public class CompileFixMSEditorTarget : TargetRules
{
	public CompileFixMSEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixMS");
		bOverrideBuildEnvironment = true;
	}
}
