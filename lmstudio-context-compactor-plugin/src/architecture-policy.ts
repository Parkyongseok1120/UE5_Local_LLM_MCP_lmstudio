import type { ArchitectureObservation, ArchitectureSelection, PatternCard } from "./architecture-contracts";
import {bundledArchitectureCatalog} from "./generated-design-guidance";
export function architectureEnabled(config:{observeOnly:boolean;designGuidanceMode:string;designGuidanceMaxTokens:number},scope:{engine:string;source:string}) {
  return !config.observeOnly && config.designGuidanceMode==="auto" && config.designGuidanceMaxTokens>0
    && scope.engine==="unreal" && !["available_tools","ambiguous"].includes(scope.source);
}
export function selectArchitectureContext(options:{enabled:boolean;objectiveKind:string;complete?:boolean;finalizing?:boolean;recovery?:boolean;
  observations:readonly ArchitectureObservation[];patterns:readonly PatternCard[];budgetAvailable?:boolean;objectiveText?:string}):ArchitectureSelection {
  if(!options.enabled || options.objectiveKind==="explain")return {depth:"none",reasons:["disabled_or_non_code_task"],selectedPatternIds:[],missingEvidence:[],disposition:"disabled"};
  if(options.finalizing || options.recovery)return {depth:"none",reasons:["phase_reuse_only"],selectedPatternIds:[],missingEvidence:[],disposition:"disabled"};
  const facets=new Set(options.observations.flatMap(o=>o.facets));
  const purpose=options.complete===false?"":(options.objectiveText||"").slice(0,4000);
  const lifetimeRequest=/(?:파괴|죽|소멸).{0,20}(?:뒤|후|이후|도).{0,20}(?:유지|남|살아)|(?:outlive|survive).{0,30}(?:actor|pawn)/iu.test(purpose);
  const crossOwner=/(?:여러|다른).{0,15}(?:액터|Actor|소유자).{0,20}(?:공유|옮|분리)|across\s+(?:actors|owners)/iu.test(purpose);
  if(lifetimeRequest || crossOwner)facets.add("scoped_service");
  const ranked=options.patterns.map(card=>({card,score:card.selection.facets.filter(f=>facets.has(f)).length}))
    .filter(x=>x.score>0).sort((a,b)=>b.score-a.score || a.card.patternId.localeCompare(b.card.patternId,"en"));
  return {depth:crossOwner?"cross_owner":ranked.length?"focused":"baseline",reasons:[crossOwner?"requested_cross_owner_comparison":lifetimeRequest?"requested_lifetime_comparison":ranked.length?"observed_lexical_facets":"ordinary_unreal_task"],
    selectedPatternIds:options.complete===false?[]:ranked.slice(0,2).map(x=>x.card.patternId),
    missingEvidence:options.observations.length?[]:["owner_lifetime_unobserved"],
    disposition:options.budgetAvailable===false?"deferred_budget":"ready"};
}
export const ARCHITECTURE_BASELINE = bundledArchitectureCatalog.baseline;
