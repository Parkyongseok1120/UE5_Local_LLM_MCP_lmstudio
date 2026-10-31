import type { ArchitectureObservation } from "./architecture-contracts";
/** Bounded projection of already returned source, not an AST or a project scan. */
export function projectArchitectureEvidence(sources:readonly {ref:ArchitectureObservation["ref"];body?:string}[]):ArchitectureObservation[] {
  return sources.slice(-16).map(source=>{
    const body=(source.body||"").slice(0,262144),facets:string[]=[];
    const has=(pattern:RegExp)=>pattern.test(body);
    if(has(/\bUActorComponent\b|CreateDefaultSubobject\s*</u))facets.push("component_owner");
    if(has(/\b(?:UPrimaryDataAsset|UDataAsset|UDataTable)\b/u))facets.push("shared_definition","mutable_instance_state");
    if(has(/\b(?:UWorldSubsystem|UGameInstanceSubsystem|ULocalPlayerSubsystem)\b/u))facets.push("scoped_service");
    if(has(/\b(?:AddDynamic|AddUObject|AddLambda|BindAction|SetTimer)\s*\(/u))facets.push("registration");
    if(has(/\b(?:EndPlay|Deinitialize|RemoveDynamic|ClearTimer|Logout)\s*\(/u))facets.push("teardown_present");
    if(has(/\b(?:RequestAsyncLoad|FStreamableHandle|TSoftObjectPtr)\b/u))facets.push("async_asset");
    if(has(/\b(?:UAbilitySystemComponent|FGameplayAbilitySpec)\b/u))facets.push("ability_system");
    if(has(/\b(?:FGameplayTag|FGameplayTagContainer)\b/u))facets.push("semantic_tags");
    return {ref:{...source.ref},facets,confidence:"hypothesis" as const};
  });
}
