import { createHash } from "node:crypto";
import type { ArchitectureBinding, ArchitectureInputRefs, ArchitecturePatch, ArchitectureSnapshot, ArchitectureEvidenceRef } from "./architecture-contracts";
const contract = require("./architecture-note-contract.js");
const clone = <T>(x:T):T => JSON.parse(JSON.stringify(x));
const sameBinding = (a:ArchitectureBinding,b:ArchitectureBinding) => JSON.stringify(a)===JSON.stringify(b);
/** Execution-local judgments. Source authority, storage and tool lifetime stay with their existing owners. */
export class ArchitectureState {
  private value:ArchitectureSnapshot={architecture:[],architectureTombstones:[],architectureOmittedCount:0};
  private closed=false;
  private nextRevision=0;
  constructor(private binding:ArchitectureBinding, restored?:ArchitectureSnapshot|null) {
    const valid=restored && contract.validateArchitectureStored(restored);
    if(valid) {
      this.value=clone(valid);
      for(const d of this.value.architecture) { d.applicability="historical"; d.revision=++this.nextRevision;
        for(const ref of d.evidenceRefs) {ref.applicability="historical";ref.availability="unavailable";} }
    }
  }
  snapshot():ArchitectureSnapshot { return clone(this.value); }
  currentBinding():ArchitectureBinding { return {...this.binding}; }
  close() {this.closed=true;}
  invalidateUnknown() {
    if(this.closed)return;
    // Idempotent: a repeated host invalidation is not a new revision.
    for(const d of this.value.architecture){
      if(d.applicability==="unknown" && d.evidenceRefs.every(ref=>ref.applicability==="unknown"))continue;
      d.applicability="unknown";d.revision=++this.nextRevision;
      for(const ref of d.evidenceRefs)ref.applicability="unknown";}
  }
  revalidateIssued(issued:ArchitectureInputRefs,observations:readonly ArchitectureEvidenceRef[]) {
    for(const ref of issued.evidence.values()) {
      const matches=observations.filter(x=>x.sourceKey && x.sourceKey===ref.sourceKey);
      if(matches.some(x=>["stale","unknown"].includes(x.applicability)
        || x.observedVersion && ref.observedVersion && x.observedVersion!==ref.observedVersion))ref.applicability="stale";
    }
  }
  syncBinding(next:ArchitectureBinding) {
    if(this.closed || sameBinding(this.binding,next))return;
    if(this.binding.projectIdentity!==next.projectIdentity || this.binding.objectiveFingerprint!==next.objectiveFingerprint
      || this.binding.lineage!==next.lineage) this.value={architecture:[],architectureTombstones:[],architectureOmittedCount:0};
    else for(const d of this.value.architecture) {
      if(this.binding.engineIdentityKey===next.engineIdentityKey && !d.patternRefs.length)continue;
      const state=this.binding.engineIdentityKey!==next.engineIdentityKey ? "unknown" : "stale";
      d.applicability=state; d.revision=++this.nextRevision;
    }
    this.binding={...next};
  }
  invalidate(observations:readonly ArchitectureEvidenceRef[]) {
    if(this.closed)return;
    for(const d of this.value.architecture) for(const old of d.evidenceRefs) {
      const changes=observations.filter(x=>x.sourceKey && x.sourceKey===old.sourceKey);
      if(!changes.length)continue;
      const uncertain=changes.some(x=>x.applicability==="unknown" || x.applicability==="stale");
      const changed=changes.some(x=>x.observedVersion && old.observedVersion && x.observedVersion!==old.observedVersion);
      old.availability=changes.at(-1)!.availability;
      const state=uncertain ? "unknown" : changed ? "stale" : old.applicability;
      if(state!==old.applicability || state!==d.applicability && ["unknown","stale"].includes(state)) {
        old.applicability=state;
        if(state==="unknown" || state==="stale")d.applicability=state;
        d.revision=++this.nextRevision;
      }
    }
  }
  commit(patch:unknown,issued:ArchitectureInputRefs,accept:(snapshot:ArchitectureSnapshot)=>boolean=()=>true) {
    if(this.closed || !sameBinding(this.binding,issued.binding))return {disposition:"reject",reason:"stale_binding"};
    const parsed=contract.validateArchitecturePatch(patch) as ArchitecturePatch|null;
    if(!parsed)return {disposition:"reject",reason:"invalid_patch"};
    const next=this.snapshot(); let revision=this.nextRevision;
    const visible=(id:string)=>issued.decisions.has(id) && next.architecture.find(d=>d.id===id)?.revision===issued.decisions.get(id);
    const retire=(id:string,by?:string)=> {
      if(!visible(id))return false;
      const old=next.architecture.find(d=>d.id===id)!;
      next.architecture=next.architecture.filter(d=>d.id!==id);
      next.architectureTombstones.push({id,revision:++revision,disposition:by?"superseded":"rejected",...(by?{supersededBy:by}:{}),reasonCode:by?"explicit_supersede":"explicit_retire"});
      return !!old;
    };
    for(const id of parsed.retireIds)if(!retire(id))return {disposition:"reject",reason:"unseen_or_stale_decision"};
    for(const draft of parsed.upsert) {
      const id=draft.id || `a_${createHash("sha256").update(this.binding.objectiveFingerprint+"\n"+draft.subject).digest("hex").slice(0,16)}`;
      if(next.architectureTombstones.some(t=>t.id===id))return {disposition:"reject",reason:"retired_decision"};
      if(next.architecture.some(d=>d.id===id) && !visible(id))return {disposition:"reject",reason:"unseen_or_stale_decision"};
      const refs=draft.refs.map(ref=>issued.evidence.get(ref)); const patterns=draft.patternRefs.map(ref=>issued.patterns.get(ref));
      if(refs.some(x=>!x) || patterns.some(x=>!x))return {disposition:"reject",reason:"unissued_reference"};
      if(refs.some(ref=>ref!.applicability==="stale" || ref!.applicability==="unknown") || patterns.some(p=>p!.applicability==="mismatch"))
        return {disposition:"reject",reason:"superseded_reference"};
      for(const old of draft.supersedes||[])if(!retire(old,id))return {disposition:"reject",reason:"invalid_supersedes"};
      const {refs:_refs,patternRefs:_patterns,...body}=draft;
      next.architecture=next.architecture.filter(d=>d.id!==id);
      if(next.architecture.length>=2)return {disposition:"reject",reason:"capacity"};
      const applicability=refs.length && refs.every(r=>r!.applicability==="same_observed_version")
        ? "same_observed_version" : refs.some(r=>r!.applicability==="historical")?"historical":"unknown";
      next.architecture.push({...body,id,revision:++revision,applicability,evidenceRefs:clone(refs) as ArchitectureEvidenceRef[],patternRefs:clone(patterns) as any});
    }
    while(next.architectureTombstones.length>4){next.architectureTombstones.shift();next.architectureOmittedCount++;}
    if(!contract.validateArchitectureStored(next) || !accept(next))return {disposition:"reject",reason:"storage_budget"};
    this.value=next;this.nextRevision=revision;
    return {disposition:"commit",reason:"accepted"};
  }
}
