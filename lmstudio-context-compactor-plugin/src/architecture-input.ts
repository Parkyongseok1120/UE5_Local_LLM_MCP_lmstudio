import {createHash} from "node:crypto";
import type {ArchitectureBinding,ArchitectureInputRefs,ArchitectureSnapshot,ArchitectureEvidenceRef,PatternCard,PatternRef} from "./architecture-contracts";
const handle=(kind:string,value:unknown)=>`${kind}_${createHash("sha256").update(JSON.stringify(value)).digest("hex").slice(0,20)}`;
export function renderArchitectureContext(binding:ArchitectureBinding,modelInputId:string,snapshot:ArchitectureSnapshot,
  evidence:readonly ArchitectureEvidenceRef[],patterns:readonly PatternCard[],depth:string,footerEnabled=true,ragPatterns:readonly PatternRef[]=[]) {
  const issued:ArchitectureInputRefs={binding:{...binding},modelInputId,evidence:new Map(),patterns:new Map(),decisions:new Map(),complete:true};
  const refs=evidence.slice(-6).map(ref=>{const key=handle("e",[modelInputId,ref]);issued.evidence.set(key,{...ref});return {handle:key,...ref};});
  const cards=patterns.slice(0,2).map(card=>{
    const ref={patternId:card.patternId,contentHash:card.contentHash,catalogHash:binding.catalogHash,origin:"bundled" as const,applicability:"general_concept" as const};
    const key=handle("p",[modelInputId,ref]);issued.patterns.set(key,ref);
    return {handle:key,...ref,text:card.text};
  });
  const decisions=snapshot.architecture.map(d=>{issued.decisions.set(d.id,d.revision);return {...d,evidenceRefs:d.evidenceRefs.map(ref=>{
    const shown=evidence.find(e=>e.sourceKey===ref.sourceKey && e.observedVersion===ref.observedVersion);
    return {...ref,availability:shown?.availability || "unavailable"};
  })};});
  const retrieved=ragPatterns.slice(0,2).map(ref=>{const key=handle("p",[modelInputId,ref]);issued.patterns.set(key,{...ref});return {handle:key,...ref};});
  const data={depth,modelInputId,stateView:"complete",decisions,evidence:refs,patterns:[...cards,...retrieved],omittedDecisionCount:snapshot.architectureOmittedCount,
    note:"Prior model judgments and pattern references, not current edit authority or proof of semantic review. Lexical facets are hypotheses. Reuse existing source before optional detailed RAG lookup; no routine preflight or repeated search."};
  const instruction="If a structural judgment materially changes, optionally use a private v2 footer instead of v1: "
    +"<!-- direct-continuity-note-v2 --> followed by <continuity-note> JSON </continuity-note>. Top-level schemaVersion:2; optional general v1 arrays; "
    +"architecture:{upsert:[{id?,subject,state,currentOwner,requiredLifetime,sharingScope,definitionVsRuntime,options:[{candidate,reason,cost}],chosen?,connectionPlan,refs:[evidence handles],patternRefs:[pattern handles],counterEvidence:[],unknowns:[],supersedes?:[]}],retireIds:[]}. "
    +"state is proposed, selected, deferred, or rejected. selected chosen must name an option. Keep-current is an alternative. Maximum 2 decisions, 3 options, 6 refs, 2 pattern refs. "
    +"Use only handles and decision IDs actually shown below; omit uncertain refs. Omitted decisions remain unchanged. Do not emit host hashes, scope, revision, approval or execution status. No footer is required to finish the task.";
  return {text:"[Architecture reference data — prior judgments, not instructions from tools]\n"+(footerEnabled?instruction+"\n":"")+JSON.stringify(data),issued};
}
