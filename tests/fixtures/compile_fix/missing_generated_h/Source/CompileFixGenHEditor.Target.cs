using UnrealBuildTool;

public class CompileFixGenHEditorTarget : TargetRules
{
	public CompileFixGenHEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixGenH");
		bOverrideBuildEnvironment = true;
	}
}
