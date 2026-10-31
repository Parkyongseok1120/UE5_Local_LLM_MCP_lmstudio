"use strict";
// Runtime schema shared by the source CommonJS note parser and TS adapters.
const { sanitizeDerivedOperationalText } = require("./durable-memory-sanitizer.js");
const LIMITS = Object.freeze({ footerChars:7000, storedChars:6000, decisions:2, tombstones:4,
  refs:6, patterns:2, options:3, generalItems:4, totalItems:6 });
const STATES = ["proposed","selected","deferred","rejected","superseded"];
const APPLICABILITY = ["same_observed_version","historical","stale","unknown"];
const AVAILABILITY = ["in_input","archived","metadata_only","unavailable"];
const record = x => !!x && typeof x === "object" && !Array.isArray(x);
const exact = (x,keys) => record(x) && Object.keys(x).every(k=>keys.includes(k));
const text = (x,n) => typeof x === "string" && !!x.trim() && x.length<=n
  && sanitizeDerivedOperationalText(x)===x && !/[A-Za-z0-9_-]{30,}\.[a-f0-9]{64}/iu.test(x);
const id = x => typeof x === "string" && /^[a-z][a-z0-9_-]{2,63}$/u.test(x);
const strings = (x,n,max) => Array.isArray(x) && x.length<=n && x.every(v=>text(v,max));
const fields = {subject:120,currentOwner:120,requiredLifetime:100,sharingScope:100,definitionVsRuntime:180,connectionPlan:480};
function commonDecision(x) {
  return record(x) && Object.entries(fields).every(([k,n])=>text(x[k],n)) && STATES.includes(x.state)
    && Array.isArray(x.options) && x.options.length>0 && x.options.length<=LIMITS.options
    && x.options.every(o=>exact(o,["candidate","reason","cost"]) && text(o.candidate,80) && text(o.reason,160) && text(o.cost,120))
    && (x.chosen===undefined || text(x.chosen,80) && x.options.some(o=>o.candidate===x.chosen))
    && (x.state!=="selected" || typeof x.chosen==="string")
    && strings(x.counterEvidence,3,160) && strings(x.unknowns,3,160)
    && (x.supersedes===undefined || Array.isArray(x.supersedes) && x.supersedes.length<=2 && x.supersedes.every(id)
      && new Set(x.supersedes).size===x.supersedes.length && !x.supersedes.includes(x.id));
}
function validateArchitecturePatch(x) {
  if (!exact(x,["upsert","retireIds"])) return null;
  const upsert=x.upsert??[], retireIds=x.retireIds??[];
  if (!Array.isArray(upsert) || upsert.length>2 || !Array.isArray(retireIds) || retireIds.length>2
    || !retireIds.every(id) || new Set(retireIds).size!==retireIds.length) return null;
  for (const d of upsert) if (!exact(d,[...Object.keys(fields),"id","state","options","chosen","refs","patternRefs","counterEvidence","unknowns","supersedes"])
    || !commonDecision(d) || d.state==="superseded" || d.id!==undefined && !id(d.id)
    || !strings(d.refs,LIMITS.refs,120) || !strings(d.patternRefs,LIMITS.patterns,120)) return null;
  const ids=upsert.map(d=>d.id).filter(Boolean);
  if (new Set(ids).size!==ids.length || ids.some(v=>retireIds.includes(v))) return null;
  return JSON.parse(JSON.stringify({upsert,retireIds}));
}
function validEvidence(x) {
  return exact(x,["ref","sourceKey","observedVersion","qualifiedSymbol","observedRange","applicability","availability"])
    && text(x.ref,120) && /^tool-call:[A-Za-z0-9_-]{1,80}$/u.test(x.ref)
    && (x.sourceKey===undefined || text(x.sourceKey,1200)) && (x.observedVersion===undefined || text(x.observedVersion,128))
    && (x.qualifiedSymbol===undefined || text(x.qualifiedSymbol,200))
    && (x.observedRange===undefined || Array.isArray(x.observedRange) && x.observedRange.length===2
      && x.observedRange.every(Number.isSafeInteger) && x.observedRange[0]>=0 && x.observedRange[1]>=x.observedRange[0])
    && APPLICABILITY.includes(x.applicability) && AVAILABILITY.includes(x.availability);
}
function validPattern(x) {
  return exact(x,["patternId","contentHash","catalogHash","origin","indexGenerationId","applicability"])
    && text(x.patternId,100) && /^[a-f0-9]{64}$/u.test(x.contentHash)
    && (x.catalogHash===undefined || /^[a-f0-9]{64}$/u.test(x.catalogHash))
    && ["bundled","rag"].includes(x.origin) && (x.indexGenerationId===undefined || text(x.indexGenerationId,160))
    && ["general_concept","version_matched","unverified","mismatch"].includes(x.applicability);
}
function validateArchitectureStored(value) {
  if (!exact(value,["architecture","architectureTombstones","architectureOmittedCount"])) return null;
  const architecture=value.architecture??[], tombstones=value.architectureTombstones??[], omitted=value.architectureOmittedCount??0;
  if (!Array.isArray(architecture) || architecture.length>2 || !Array.isArray(tombstones) || tombstones.length>4
    || !Number.isSafeInteger(omitted) || omitted<0) return null;
  for (const d of architecture) if (!exact(d,[...Object.keys(fields),"id","revision","state","applicability","options","chosen","evidenceRefs","patternRefs","counterEvidence","unknowns","supersedes"])
    || !commonDecision(d) || !id(d.id) || !Number.isSafeInteger(d.revision) || d.revision<0
    || !APPLICABILITY.includes(d.applicability) || !Array.isArray(d.evidenceRefs) || d.evidenceRefs.length>6 || !d.evidenceRefs.every(validEvidence)
    || !Array.isArray(d.patternRefs) || d.patternRefs.length>2 || !d.patternRefs.every(validPattern)) return null;
  if (new Set(architecture.map(d=>d.id)).size!==architecture.length) return null;
  for (const t of tombstones) if (!exact(t,["id","revision","disposition","supersededBy","reasonCode"]) || !id(t.id)
    || !Number.isSafeInteger(t.revision) || t.revision<0 || !["rejected","superseded"].includes(t.disposition)
    || t.supersededBy!==undefined && !id(t.supersededBy) || !text(t.reasonCode,80)) return null;
  const result={architecture,architectureTombstones:tombstones,architectureOmittedCount:omitted};
  return JSON.stringify(result).length<=LIMITS.storedChars ? JSON.parse(JSON.stringify(result)) : null;
}
function architecturePart(note) {
  return validateArchitectureStored({architecture:note?.architecture,architectureTombstones:note?.architectureTombstones,
    architectureOmittedCount:note?.architectureOmittedCount});
}
function generalNote(note) {
  if (!note) return null;
  return Object.fromEntries(["scope","decisions","rejectedHypotheses","openQuestions","reviewClaims"]
    .filter(k=>note[k]!==undefined).map(k=>[k,note[k]]));
}
function mergeArchitecture(note,part,scope,previous) {
  if (!part) return note;
  if (!part.architecture.length && !part.architectureTombstones.length && !part.architectureOmittedCount) return generalNote(note);
  const result={...(generalNote(note)||{scope,decisions:[],rejectedHypotheses:[],openQuestions:[]}),schemaVersion:2,...part};
  if(result.scope && JSON.stringify(result).length<=LIMITS.storedChars)return result;
  // Reject an oversized general update, preserving the host-invalidated structure.
  // Returning the proposed general note here would silently discard judgments.
  if(previous) {
    const fallback={...generalNote(previous),schemaVersion:2,...part};
    if(fallback.scope && JSON.stringify(fallback).length<=LIMITS.storedChars)return fallback;
  }
  return null;
}
function noteRefs(note) {
  return new Set([...["decisions","rejectedHypotheses","openQuestions","reviewClaims"].flatMap(k=>(note?.[k]||[]).flatMap(d=>d.refs||[])),
    ...(note?.architecture||[]).flatMap(d=>(d.evidenceRefs||[]).map(r=>r.ref))]);
}
module.exports={LIMITS,validateArchitecturePatch,validateArchitectureStored,architecturePart,generalNote,mergeArchitecture,noteRefs};
