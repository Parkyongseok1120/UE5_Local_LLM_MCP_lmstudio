using UnrealBuildTool;

public class CompileFixTagsEditorTarget : TargetRules
{
	public CompileFixTagsEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixTags");
		bOverrideBuildEnvironment = true;
	}
}
