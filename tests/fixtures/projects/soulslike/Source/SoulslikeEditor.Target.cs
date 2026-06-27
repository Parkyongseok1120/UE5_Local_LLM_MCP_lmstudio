using UnrealBuildTool;

public class SoulslikeEditorTarget : TargetRules
{
	public SoulslikeEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("Soulslike");
		bOverrideBuildEnvironment = true;
	}
}
