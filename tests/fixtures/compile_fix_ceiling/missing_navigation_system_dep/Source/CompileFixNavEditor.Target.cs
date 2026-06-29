using UnrealBuildTool;

public class CompileFixNavEditorTarget : TargetRules
{
	public CompileFixNavEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixNav");
		bOverrideBuildEnvironment = true;
	}
}
