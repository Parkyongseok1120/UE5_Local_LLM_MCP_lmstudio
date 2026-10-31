import type {Chat,ChatMessage} from "@lmstudio/sdk";
import {ArchitectureState} from "./architecture-state";
export type {ArchitectureState};
import {architectureEnabled} from "./architecture-policy";
import {bundledArchitectureCatalog} from "./generated-design-guidance";
import {referenceGuidanceScope} from "./reference-context";
import {telemetryFingerprint} from "./evidence-telemetry";
import {modelNotes} from "./context-ports";
import {capabilityFor} from "./tool-capability-registry";
import type {EvidenceManager} from "./evidence-manager";
import type {DirectConfig,ContinuityNote} from "./execution-contracts";
import type {ToolScope} from "./tool-scope";
import type {ArchitectureBinding,ArchitectureInputRefs} from "./architecture-contracts";

export function reconcileArchitectureRound(state:ArchitectureState|null,note:ContinuityNote,messages:ChatMessage[],refs:Set<string>|undefined,
  scope:{objectiveFingerprint:string;projectDescriptor:string}) {
  const reconciled=modelNotes.reconcileStoredNote(note,messages,refs,scope);
  if(!reconciled){state?.close();return {state:null,note:null};}
  return {state,note:reconciled};
}

/** Thin connection adapter. It neither dispatches tools nor owns storage. */
export function beginArchitectureRound(state:ArchitectureState|null,config:DirectConfig,noteEnabled:boolean,scope:ToolScope,
  evidence:EvidenceManager,history:Chat,binding:Pick<ArchitectureBinding,"executionId"|"lineage"|"objectiveFingerprint"|"projectIdentity">,note:ContinuityNote|null) {
  // Off means no projection or snapshot work, not only no final injection.
  if(!noteEnabled || config.observeOnly || config.designGuidanceMode!=="auto" || config.designGuidanceMaxTokens<=0)return {state,active:false};
  const snapshot=evidence.referenceSnapshot(history),effective=referenceGuidanceScope(scope,snapshot);
  if(!architectureEnabled(config,effective)){state?.invalidateUnknown();return {state,active:false};}
  const next={...binding,scopeRevision:0,catalogHash:bundledArchitectureCatalog.catalogHash,engineIdentityKey:telemetryFingerprint(snapshot.applicability)};
  if(!state)state=new ArchitectureState(next,modelNotes.architecturePart(note));else state.syncBinding(next);
  state.invalidate(evidence.architectureVersions());
  return {state,active:true};
}

export function finishArchitectureRound(state:ArchitectureState,evidence:EvidenceManager,history:Chat,messages:ChatMessage[],
  planningCommitted:boolean,trace:Iterable<Record<string,unknown>>,issued:ArchitectureInputRefs|undefined,patches:unknown[],note:ContinuityNote|null,
  scope:ContinuityNote["scope"]) {
  const before=state.snapshot();
  const reference=evidence.referenceSnapshot(history);
  state.syncBinding({...state.currentBinding(),engineIdentityKey:telemetryFingerprint(reference.applicability)});
  if(reference.projectObservation?.reason==="returned_target_mismatch") {
    state.invalidateUnknown();issued=undefined;
  }
  const changes=[...evidence.architectureChanges(messages),...evidence.architectureVersions()];
  state.invalidate(changes);
  const uncertainWrite=[...trace].some(t=>t.guardAllowed && t.executionState!=="not_executed" && evidence.tools.some(tool=>
    tool.name===t.toolName && capabilityFor(tool)?.effect==="mutation"));
  if(!planningCommitted && uncertainWrite){state.invalidateUnknown();evidence.invalidateReturnedSources();}
  const results=[],events:Record<string,unknown>[]=[];
  for(const decision of state.snapshot().architecture) {
    const prior=before.architecture.find(d=>d.id===decision.id);
    if(prior && prior.revision!==decision.revision)events.push({event:"architecture_invalidation",decisionId:decision.id,
      revision:decision.revision,reason:decision.applicability,affectedRefs:decision.evidenceRefs.map(r=>r.ref),
      observedVersions:decision.evidenceRefs.map(r=>r.observedVersion || "unknown")});
  }
  if(issued && planningCommitted) {
    state.revalidateIssued(issued,changes);
    for(const patch of patches) {
      const result=state.commit(patch,issued,snapshot=>{
      const merged=modelNotes.mergeArchitecture(note,snapshot,scope);
      return Boolean(merged?.schemaVersion===2 && JSON.stringify(merged.architecture)===JSON.stringify(snapshot.architecture));
      });
      results.push(result);
      events.push({event:"architecture_decision",...result,decisions:state.snapshot().architecture.map(d=>({decisionId:d.id,
        revision:d.revision,dependencyCount:d.evidenceRefs.length,sourceApplicability:d.applicability,
        patternHashes:d.patternRefs.map(p=>p.contentHash)}))});
    }
  }
  return {note:modelNotes.mergeArchitecture(note,state.snapshot(),scope),results,events};
}
