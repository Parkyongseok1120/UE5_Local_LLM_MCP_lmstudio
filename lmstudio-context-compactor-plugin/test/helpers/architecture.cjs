"use strict";
const {ArchitectureState}=require("../../dist/architecture-state");
const {renderArchitectureContext}=require("../../dist/architecture-input");
const catalog=require("../../dist/generated-design-guidance").bundledArchitectureCatalog;
const binding=(extra={})=>({executionId:"exec",lineage:"lineage",objectiveFingerprint:"a".repeat(64),projectIdentity:"C:/Fixture/Game.uproject",scopeRevision:0,catalogHash:catalog.catalogHash,...extra});
const evidence=(extra={})=>({ref:"tool-call:r1",sourceKey:"Source/A.cpp",observedVersion:"1".repeat(64),applicability:"same_observed_version",availability:"in_input",...extra});
const draft=(extra={})=>({id:"decision_a",subject:"Health feature",state:"selected",currentOwner:"Character",requiredLifetime:"Pawn",sharingScope:"one actor",definitionVsRuntime:"Defaults are shared; health is per instance.",options:[{candidate:"keep_current",reason:"Existing owner has the right lifetime.",cost:"No new abstraction."}],chosen:"keep_current",connectionPlan:"Initialize on spawn and clear timers on EndPlay.",refs:[],patternRefs:[],counterEvidence:["EndPlay already clears timers."],unknowns:[],...extra});
function issue(state,sources=[evidence()],patterns=[]) {return renderArchitectureContext(state.currentBinding(),"input-1",state.snapshot(),sources,patterns,"focused").issued;}
function populated(extra={}) {const state=new ArchitectureState(binding());const refs=issue(state);state.commit({upsert:[draft({refs:[...refs.evidence.keys()],...extra})]},refs);return state;}
module.exports={ArchitectureState,renderArchitectureContext,catalog,binding,evidence,draft,issue,populated};
