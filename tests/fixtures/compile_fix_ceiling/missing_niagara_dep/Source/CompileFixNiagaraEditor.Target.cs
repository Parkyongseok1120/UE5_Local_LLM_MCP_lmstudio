using UnrealBuildTool;

public class CompileFixNiagaraEditorTarget : TargetRules
{
	public CompileFixNiagaraEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixNiagara");
		bOverrideBuildEnvironment = true;
	}
}
