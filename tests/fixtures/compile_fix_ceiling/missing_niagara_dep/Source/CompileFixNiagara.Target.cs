using UnrealBuildTool;

public class CompileFixNiagaraTarget : TargetRules
{
	public CompileFixNiagaraTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("CompileFixNiagara");
	}
}
