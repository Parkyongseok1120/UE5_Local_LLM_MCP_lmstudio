"use strict";
const { ENGINE_PROFILES, ENGINE_PROVIDERS } = require("./engine-profiles.js");

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { buildObjectiveContinuity, objectiveIdentity } = require("./continuity-objectives.js");
const { pathApiFor, canonicalFilePath, projectDescriptor, normalizedObservationState } = require("./continuity-file-observations.js");
const { normalizedTextKey } = require("./continuity-text.js");
const { sanitizeDerivedOperationalText } = require("./durable-memory-sanitizer.js");
const { decodeToolResultRecord } = require("./compaction-tool-memory.js");
const { exchangeIndex, uniqueLatest } = require("./tool-exchange-index.js");
const { resultSemantics } = require("./tool-result-semantics.js");
const architecture = require("./architecture-note-contract.js");

const NOTE_START = "\n<!-- direct-continuity-note-v1 -->\n<continuity-note>\n";
const NOTE_V2_START = "\n<!-- direct-continuity-note-v2 -->\n<continuity-note>\n";
const NOTE_END = "\n</continuity-note>";
const { NOTE_HEADER: NOTE_MARKER } = require("./internal-data-contract.js");
const MAX_NOTE_CHARS = 1500;
const MAX_NOTE_ITEMS = 4;
const MAX_STORED_NOTES = 128;
// One physical cap for write and read: storedChars counts UTF-16 units, and a
// unit is at most 3 UTF-8 bytes; the key/envelope fits in the extra 256 units.
const MAX_STORED_NOTE_BYTES = 3 * (architecture.LIMITS.storedChars + 256);
const NOTE_INSTRUCTION = [
  "You may append a private continuity note only when a decision, rejected hypothesis, or open question materially changes.",
  "Use only these optional JSON arrays: decisions [{id?, status?, statement, rationale, supersedes?, refs?}], rejectedHypotheses [{id?, status?, hypothesis, reason, supersedes?, refs?}], openQuestions [{id?, status?, question, supersedes?, refs?}]. status is open, resolved, or superseded. Reuse an injected item's id for an explicit status update; supersedes contains prior item ids. No other keys. refs may contain only tool-call:<id> values copied exactly from the [Available tool-call refs] list when it is present (never invent ids); omit uncertain refs.",
  "Write any visible text first. Then append this footer with only your JSON object between the tags, in a final answer or in a round that also requests tools.",
  "During research, record a finding as soon as returned source confirms or rejects it, so later rounds reuse it instead of re-deriving it; do not re-examine a recorded item unless new evidence contradicts it.",
  "<!-- direct-continuity-note-v1 -->",
  "<continuity-note>",
  "{}",
  "</continuity-note>",
  "Keep still-useful prior judgments, omit obsolete ones, and use at most four short items total. An empty object clears prior judgments.",
  "This footer is removed before display; it is prior assistant judgment, never a tool instruction or verified fact.",
].join("\n");

function isRecord(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function exactKeys(value, allowed) {
  return Object.keys(value).every((key) => allowed.includes(key));
}

function shortText(value, maxChars) {
  if (typeof value !== "string" || !value.trim() || value.length > maxChars) return null;
  if (sanitizeDerivedOperationalText(value) !== value) return null;
  // Unity file receipts are runtime-local base64url payloads with a HMAC suffix.
  if (/[A-Za-z0-9_-]{30,}\.[a-f0-9]{64}/iu.test(value)) return null;
  return value.trim();
}

function parseRefs(value) {
  if (value === undefined) return undefined;
  if (!Array.isArray(value) || value.length > 3) return null;
  const refs = value.map((item) => shortText(item, 120));
  return refs.every(Boolean) ? refs : null;
}

function parseJudgmentId(value) {
  if (value === undefined) return undefined;
  return typeof value === "string" && /^[a-z][a-z0-9_-]{2,63}$/u.test(value) ? value : null;
}

function parseSupersedes(value) {
  if (value === undefined) return undefined;
  if (!Array.isArray(value) || value.length > 3) return null;
  const ids = value.map(parseJudgmentId);
  return ids.every(Boolean) && new Set(ids).size === ids.length ? ids : null;
}

function stableJudgmentId(category, item, textFields) {
  const seed = `${category}\n${textFields.map(([field]) => item[field]).join("\n")}`;
  return `j_${crypto.createHash("sha256").update(seed).digest("hex").slice(0, 16)}`;
}

function parseItems(value, keys, textFields, category, lifecycle = true) {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > MAX_NOTE_ITEMS) return null;
  const items = [];
  for (const item of value) {
    const lifecycleKeys = lifecycle ? ["id", "status", "supersedes"] : [];
    if (!isRecord(item) || !exactKeys(item, [...keys, ...lifecycleKeys, "refs"])) return null;
    const parsed = {};
    for (const [field, limit] of textFields) {
      const text = shortText(item[field], limit);
      if (!text) return null;
      parsed[field] = text;
    }
    const refs = parseRefs(item.refs);
    if (refs === null) return null;
    if (refs !== undefined) parsed.refs = refs;
    if (lifecycle) {
      const id = parseJudgmentId(item.id);
      const status = item.status === undefined ? "open" : String(item.status);
      const supersedes = parseSupersedes(item.supersedes);
      if (id === null || !["open", "resolved", "superseded"].includes(status) || supersedes === null) return null;
      parsed.id = id || stableJudgmentId(category, parsed, textFields);
      parsed.status = status;
      if (supersedes !== undefined) parsed.supersedes = supersedes;
    }
    items.push(parsed);
  }
  return items;
}

function parseReviewClaims(value) {
  if (value === undefined) return [];
  const items = parseItems(value, ["path", "sha256", "reviewScope", "statement"],
    [["path", 300], ["sha256", 64], ["reviewScope", 80], ["statement", 300]], "reviewClaims", false);
  if (!items || items.some(x => !/^[a-f0-9]{64}$/i.test(x.sha256) || !x.refs?.length)) return null;
  return items;
}

function validateDraftNote(value) {
  if (!isRecord(value) || !exactKeys(value, ["decisions", "rejectedHypotheses", "openQuestions", "reviewClaims"])) return null;
  const decisions = parseItems(value.decisions, ["statement", "rationale"],
    [["statement", 300], ["rationale", 400]], "decisions");
  const rejectedHypotheses = parseItems(value.rejectedHypotheses, ["hypothesis", "reason"],
    [["hypothesis", 300], ["reason", 400]], "rejectedHypotheses");
  const openQuestions = parseItems(value.openQuestions, ["question"], [["question", 350]], "openQuestions");
  const reviewClaims = parseReviewClaims(value.reviewClaims);
  if (!decisions || !rejectedHypotheses || !openQuestions || !reviewClaims) return null;
  if (decisions.length + rejectedHypotheses.length + openQuestions.length + reviewClaims.length > MAX_NOTE_ITEMS) return null;
  const ids = [...decisions, ...rejectedHypotheses, ...openQuestions].map(item => item.id);
  if (new Set(ids).size !== ids.length) return null;
  const note = { decisions, rejectedHypotheses, openQuestions, ...(reviewClaims.length ? { reviewClaims } : {}) };
  return JSON.stringify(note).length <= MAX_NOTE_CHARS ? note : null;
}

function insideCodeFence(text, offset) {
  let fence = null;
  for (const line of text.slice(0, offset).split(/\r?\n/u)) {
    const match = /^\s{0,3}(`{3,}|~{3,})/u.exec(line);
    if (!match) continue;
    if (!fence) fence = { character: match[1][0], length: match[1].length };
    else if (match[1][0] === fence.character && match[1].length >= fence.length) fence = null;
  }
  return Boolean(fence);
}

function splitVisibleAnswer(text) {
  const source = String(text || "");
  const v2 = source.lastIndexOf(NOTE_V2_START)>source.lastIndexOf(NOTE_START);
  const marker = v2 ? NOTE_V2_START : NOTE_START;
  const start = source.lastIndexOf(marker);
  if(start<0) {
    const reserved=/\n<!-- direct-continuity-note-v[12](?: -->|$)/gu;
    const partial=[...source.matchAll(reserved)].at(-1);
    if(partial && !insideCodeFence(source,partial.index))return {visibleText:source.slice(0,partial.index).trimEnd() || "The model did not provide a visible answer.",hasFooter:true,note:null};
  }
  if (start < 0 || insideCodeFence(source, start)) {
    return { visibleText: source, hasFooter: false, note: null };
  }
  const visibleText = source.slice(0, start).trimEnd() || "The model did not provide a visible answer.";
  const suffix = source.slice(start + marker.length).trimEnd();
  if (!suffix.endsWith(NOTE_END)) return { visibleText, hasFooter: true, note: null };
  const rawJson = suffix.slice(0, -NOTE_END.length).trim();
  if (rawJson.length > (v2 ? architecture.LIMITS.footerChars : 1800)) return { visibleText, hasFooter: true, note: null };
  try {
    const parsed=JSON.parse(rawJson);
    if(!v2)return { visibleText, hasFooter: true, note: validateDraftNote(parsed) };
    if(!isRecord(parsed) || parsed.schemaVersion!==2 || !exactKeys(parsed,["schemaVersion","decisions","rejectedHypotheses","openQuestions","reviewClaims","architecture"]))
      return {visibleText,hasFooter:true,note:null,architectureError:"invalid_v2_envelope"};
    const general=Object.fromEntries(["decisions","rejectedHypotheses","openQuestions","reviewClaims"].filter(k=>Object.hasOwn(parsed,k)).map(k=>[k,parsed[k]]));
    const patch=parsed.architecture===undefined?undefined:architecture.validateArchitecturePatch(parsed.architecture);
    return {visibleText,hasFooter:true,note:Object.keys(general).length?validateDraftNote(general):null,
      architecturePatch:patch || undefined,architectureError:patch===null?"invalid_architecture_patch":undefined,footerVersion:2};
  } catch {
    return { visibleText, hasFooter: true, note: null };
  }
}

function objectiveFingerprint(messages) {
  const normalized = messages.map((message, index) => ({
    role: message.getRole(), text: message.getText(), index,
    toolRequests: message.getToolCallRequests(),
  }));
  const prior = require("./continuity-memory.js").extractPriorContinuityState(normalized);
  return objectiveIdentity(buildObjectiveContinuity(normalized, prior));
}

function normalizedProjectRoot(value) {
  const api = pathApiFor(value);
  if (typeof value !== "string" || !api.isAbsolute(value)) return "";
  const resolved = api.resolve(value);
  return api === path.win32 ? resolved.toLowerCase() : resolved;
}

function provenanceFromMessages(messages) {
  const normalized = messages.map((message, index) => ({
    role: message.getRole(), text: message.getText(), index,
    toolRequests: message.getToolCallRequests(),
  }));
  const objectiveIndex = buildObjectiveContinuity(normalized).activeObjective?.messageIndex ?? 0;
  const startIndex = Math.max(0, objectiveIndex);
  const pairing = exchangeIndex(messages.slice(startIndex));
  const projects = new Map();
  const requestCounts = new Map();
  const completedIds = new Set();
  const observations = new Map();
  const observationBatches = new Map();
  const toolNames = new Map();
  for (let index = startIndex; index < messages.length; index += 1) {
    const message = messages[index];
    if (message.getRole() === "assistant") {
      for (const request of message.getToolCallRequests()) {
        const id = String(request.id || "");
        if (id) requestCounts.set(id, (requestCounts.get(id) || 0) + 1);
      }
    }
    if (message.getRole() !== "tool") continue;
    for (const [ri, result] of message.getToolCallResults().entries()) {
      if (!pairing.matches.has(`${index - startIndex}:${ri}`)) continue;
      completedIds.add(String(result.toolCallId));
      const paired = pairing.matches.get(`${index - startIndex}:${ri}`);
      if (paired && typeof paired.name === "string") toolNames.set(String(result.toolCallId), paired.name);
      const content = String(result.content || "");
      if (content.length > 262144) continue;
      const decoded = decodeToolResultRecord(content);
      if (!decoded.value) continue;
      const value = decoded.value;
      if (result.toolCallId) {
        observations.set(String(result.toolCallId), value);
        observationBatches.set(String(result.toolCallId), pairing.batches.get(`${index - startIndex}:${ri}`));
      }
      const unityRoot = normalizedProjectRoot(value.canonicalProjectRoot);
      if (unityRoot && typeof value.projectIdentity === "string"
        && /^[a-f0-9]{64}$/iu.test(value.projectIdentity)) {
        const identity = `unity:${value.projectIdentity.toLowerCase()}`;
        if (projects.has(identity) && projects.get(identity) !== unityRoot) projects.set(`${identity}:conflict`, unityRoot);
        projects.set(identity, unityRoot);
        continue;
      }
      const unrealDescriptor = String(value.canonicalProject || value.projectPath || value.project
        || (typeof value.activeProject === "string" ? value.activeProject : ""));
      const unrealRoot = normalizedProjectRoot(unrealDescriptor);
      if (unrealRoot && unrealRoot.toLowerCase().endsWith(ENGINE_PROFILES.unreal.descriptorExtension)) {
        const identity = `unreal:${crypto.createHash("sha256").update(unrealRoot).digest("hex")}`;
        projects.set(identity, unrealRoot);
      }
    }
  }
  const verifiedToolIds = new Set([...completedIds].filter((id) => requestCounts.get(id) === 1));
  return {
    projectIdentity: projects.size === 1 ? [...projects.keys()][0] : "",
    projectDescriptor: projects.size === 1 ? [...projects.values()][0] : "",
    projectState: projects.size === 0 ? "unobserved" : projects.size === 1 ? "single" : "mixed",
    verifiedToolIds,
    observations: new Map([...observations].filter(([id]) => verifiedToolIds.has(id))),
    observationBatches,
    toolNames,
  };
}

// A structure validator proves a packet is well formed, never that its facts are true.
const { namesForRole } = require("./tool-capability-core.js");
const STRUCTURE_ONLY_TOOLS = new Set(namesForRole("structure_validation"));
function structureOnlyResult(value, toolName) {
  return STRUCTURE_ONLY_TOOLS.has(String(toolName || ""))
    || (isRecord(value) && typeof value.validationScope === "string" && value.factualEvidenceVerified === false);
}

function availableFileObservation(value) { return value.ok !== false && !value.errorCode
    && !["deleted", "conflict_observed", "unavailable"].includes(normalizedObservationState(value))
    && !["failed", "error", "canceled", "cancelled", "outcome_unknown"].includes(String(value.status || "").toLowerCase()); }

function noteFileIdentity(value, fallback) {
  const descriptor = projectDescriptor(value, fallback);
  const canonical = canonicalFilePath(value, descriptor);
  if (!canonical) return "";
  return JSON.stringify([normalizedProjectRoot(descriptor),
    pathApiFor(descriptor) === path.win32 ? canonical.toLowerCase() : canonical]);
}

function verifiedRefs(draft, verifiedToolIds, provenance = {}, additionalVerifiedRefs = new Set()) {
  const result = Object.fromEntries(["decisions", "rejectedHypotheses", "openQuestions"].map((key) => [
    key, (draft[key] || []).flatMap((item) => {
      const { refs: _unverifiedRefs, ...body } = item;
      const refs = (item.refs || []).filter((ref) => {
        const match = /^tool-call:([A-Za-z0-9_-]{1,80})$/u.exec(ref);
        return match && (verifiedToolIds.has(match[1]) || additionalVerifiedRefs.has(ref));
      });
      if (item.refs?.length && refs.length === 0) return [];
      return [refs.length ? { ...body, refs } : body];
    }),
  ]));
  const records = [...(provenance.observations || new Map()).entries()].filter(([, value]) => value.kind !== "git_observation");
  const fileKey = value => noteFileIdentity(value, provenance.projectDescriptor);
  const claims = (draft.reviewClaims || []).filter(claim => {
    if (provenance.projectState !== "single") return false;
    const identity = fileKey(claim);
    if (!identity) return false;
    const latest = uniqueLatest(records.filter(([, value]) => fileKey(value) === identity),
      ([id]) => provenance.observationBatches?.get(id));
    if (!latest || !availableFileObservation(latest[1])
      || String(latest[1].sha256 || latest[1].hash).toLowerCase() !== claim.sha256.toLowerCase()) return false;
    return claim.refs.some(ref => {
      const observed = provenance.observations.get(ref.replace(/^tool-call:/, ""));
      return /^tool-call:/.test(ref) && observed && observed.kind !== "git_observation" && availableFileObservation(observed) && fileKey(observed) === identity
        && String(observed.sha256 || observed.hash).toLowerCase() === claim.sha256.toLowerCase();
    });
  }).map(claim => ({ ...claim, refs: claim.refs.filter(ref => verifiedToolIds.has(ref.replace(/^tool-call:/, ""))) }));
  if (claims.length) result.reviewClaims = claims;
  return result;
}

function attachScope(draft, fingerprint, messages = [], additionalVerifiedRefs = new Set()) {
  if (!draft || !fingerprint) return null;
  const validated = validateDraftNote(draft);
  if (!validated) return null;
  const provenance = provenanceFromMessages(messages);
  const note = { scope: { objectiveFingerprint: fingerprint,
    ...(provenance.projectIdentity ? { projectIdentity: provenance.projectIdentity } : {}) },
  ...verifiedRefs(validated, provenance.verifiedToolIds, provenance, additionalVerifiedRefs) };
  return JSON.stringify(note).length <= MAX_NOTE_CHARS ? note : null;
}

function noteItemCount(note) {
  return ["decisions", "rejectedHypotheses", "openQuestions", "reviewClaims"]
    .reduce((count, key) => count + (note?.[key]?.length || 0), 0);
}

// v1 is a complete snapshot, not an implicit merge. Reject an incomplete
// snapshot atomically so a malformed update cannot erase valid prior work.
function resolveNoteUpdate(draft, fingerprint, messages = [], additionalVerifiedRefs = new Set(), options = {}) {
  const validated = validateDraftNote(draft);
  if (!validated) return { kind: "rejected", reason: "invalid_note" };
  if (!noteItemCount(validated)) return options.allowClear === false
    ? { kind: "rejected", reason: "empty_summary" } : { kind: "explicit_clear", note: null };
  const scoped = attachScope(validated, fingerprint, messages, additionalVerifiedRefs);
  if (!scoped || noteItemCount(scoped) !== noteItemCount(validated)) {
    return { kind: "rejected", reason: "invalid_or_unavailable_evidence" };
  }
  return { kind: "accepted_update", note: scoped };
}

function reconcileStoredNote(note, messages, additionalVerifiedRefs = new Set(), options = {}) {
  const validated = validateStoredNote(note);
  if (!validated) return null;
  const currentObjective = options.objectiveFingerprint ?? objectiveFingerprint(messages);
  if (!currentObjective || validated.scope.objectiveFingerprint !== currentObjective) return null;
  const provenance = provenanceFromMessages(messages);
  if (validated.scope.projectIdentity && validated.scope.projectIdentity !== provenance.projectIdentity) return null;
  const currentProject = normalizedProjectRoot(options.projectDescriptor);
  if (validated.scope.projectIdentity && currentProject
    && currentProject !== normalizedProjectRoot(provenance.projectDescriptor)) return null;
  const reconciled = { scope: validated.scope,
    ...verifiedRefs(validated, provenance.verifiedToolIds, provenance, additionalVerifiedRefs) };
  if (options.discardReviewClaims) delete reconciled.reviewClaims;
  return validateStoredNote(architecture.mergeArchitecture(reconciled, architecture.architecturePart(validated), validated.scope));
}

function validateStoredNote(value) {
  if (isRecord(value) && value.schemaVersion===2) {
    if(!exactKeys(value,["schemaVersion","scope","decisions","rejectedHypotheses","openQuestions","reviewClaims","architecture","architectureTombstones","architectureOmittedCount"]))return null;
    const general=validateStoredNote(architecture.generalNote(value));
    if(!general || JSON.stringify(value).length>architecture.LIMITS.storedChars)return null;
    const part=architecture.architecturePart(value);
    return part?architecture.mergeArchitecture(general,part,general.scope):general;
  }
  if (!isRecord(value) || !exactKeys(value, ["scope", "decisions", "rejectedHypotheses", "openQuestions", "reviewClaims"])) return null;
  if (!isRecord(value.scope) || !exactKeys(value.scope, ["objectiveFingerprint", "projectIdentity"])) return null;
  const fingerprint = value.scope.objectiveFingerprint;
  if (typeof fingerprint !== "string" || !/^[a-f0-9]{64}$/u.test(fingerprint)) return null;
  if (value.scope.projectIdentity !== undefined
    && (typeof value.scope.projectIdentity !== "string" || value.scope.projectIdentity.length > 128)) return null;
  const draft = validateDraftNote({
    decisions: value.decisions,
    rejectedHypotheses: value.rejectedHypotheses,
    openQuestions: value.openQuestions,
    reviewClaims: value.reviewClaims,
  });
  if (!draft) return null;
  const note = { scope: { objectiveFingerprint: fingerprint,
    ...(value.scope.projectIdentity ? { projectIdentity: value.scope.projectIdentity } : {}) }, ...draft };
  return JSON.stringify(note).length <= MAX_NOTE_CHARS ? note : null;
}

function renderAssistantNote(note, messages = []) {
  // Persisted architecture is optional reference data, never part of the base note input.
  note = architecture.generalNote(note);
  const provenance = provenanceFromMessages(messages);
  const records = [...provenance.observations];
  const identity = value => noteFileIdentity(value, provenance.projectDescriptor);
  const applicability = [];
  for (const item of [...note.decisions, ...note.rejectedHypotheses, ...note.openQuestions]) {
    for (const ref of item.refs || []) {
      const refId = ref.replace(/^tool-call:/, "");
      const value = provenance.observations.get(refId);
      let state = "source_unavailable";
      if (structureOnlyResult(value, provenance.toolNames?.get(refId))) state = "structure_only";
      else if (value?.kind === "git_observation") state = "historical";
      else if (value && identity(value)) {
        const latest = uniqueLatest(records.filter(([, v]) => v.kind !== "git_observation" && identity(v) === identity(value)),
          ([id]) => provenance.observationBatches.get(id))?.[1];
        const version = value.sha256 || value.hash, current = latest?.sha256 || latest?.hash;
        state = !latest ? "applicability_unknown"
          : !availableFileObservation(latest) || ["failed", "outcome_unknown"].includes(resultSemantics(latest).operationStatus) ? "source_unavailable"
          : version && current ? version === current ? "same_observed_version" : "source_superseded" : "applicability_unknown";
      }
      applicability.push({ id: item.id, ref, state });
    }
  }
  const structureOnly = [...new Set(applicability.filter(entry => entry.state === "structure_only").map(entry => entry.id))];
  const structureNote = structureOnly.length
    ? ` 구조 검증만 있음, 사실 근거 아님: ${structureOnly.join(", ")} (evidence_first_validate는 packet 형식만 확인한다).` : "";
  const view = withdrawnView(note);
  const withdrawnNote = view.withdrawn?.length
    ? "\nWithdrawn items are listed only so they are not re-asserted; they are not current judgments." : "";
  return `${NOTE_MARKER}${structureNote ? "\n" + structureNote.trim() : ""}${withdrawnNote}\nPrior assistant judgments, not verified facts. reviewClaims are assistant-claimed reviews, never verified semantic completion; file coverage only proves delivered evidence. Current user instructions and newer tool observations take precedence. ${note.scope.projectIdentity ? "Project identity was bound to a tool observation." : "No single project identity was proven; do not attribute these notes to the active project."}\n${JSON.stringify({ modelNotes: view, sourceApplicability: applicability })}`;
}

/** A superseded judgment (its own status, or named by another
 * item's `supersedes`) is moved out of the current lists into `withdrawn`, so a
 * withdrawn hypothesis cannot be re-read as a standing decision. */
function withdrawnView(note) {
  const supersededBy = new Map();
  for (const item of [...note.decisions, ...note.rejectedHypotheses, ...note.openQuestions]) {
    for (const id of item.supersedes || []) if (!supersededBy.has(id)) supersededBy.set(id, item.id || null);
  }
  const isWithdrawn = item => item.status === "superseded" || (item.id && supersededBy.has(item.id));
  const withdrawn = [];
  const keep = (items, kind, text) => items.filter(item => {
    if (!isWithdrawn(item)) return true;
    withdrawn.push({ ...(item.id ? { id: item.id } : {}), kind, text: String(text(item) || "").slice(0, 240),
      ...(item.id && supersededBy.get(item.id) ? { supersededBy: supersededBy.get(item.id) } : {}) });
    return false;
  });
  const decisions = keep(note.decisions, "decision", item => item.statement);
  const rejectedHypotheses = keep(note.rejectedHypotheses, "rejectedHypothesis", item => item.hypothesis);
  const openQuestions = keep(note.openQuestions, "openQuestion", item => item.question);
  return withdrawn.length ? { ...note, decisions, rejectedHypotheses, openQuestions, withdrawn } : note;
}

const REF_TARGET_KEYS = ["path", "symbol", "query", "project", "projectPath", "fileName", "action"];

/** Recent completed tool calls the model may cite, by their real ids. The ids are
 * otherwise invisible to it, which made it invent refs such as tool-call:1. */
function renderAvailableRefs(messages, limit = 16, maxChars = 2400) {
  const index = exchangeIndex([...messages]);
  const counts = new Map();
  messages.forEach(message => { if (message.getRole() === "assistant") for (const request of message.getToolCallRequests()) {
    const id = String(request.id || ""); if (id) counts.set(id, (counts.get(id) || 0) + 1); } });
  const rows = [];
  messages.forEach((message, messageIndex) => {
    if (message.getRole() !== "tool") return;
    message.getToolCallResults().forEach((result, resultIndex) => {
      const request = index.matches.get(`${messageIndex}:${resultIndex}`);
      const id = String(result.toolCallId || "");
      if (!request || !/^[A-Za-z0-9_-]{1,80}$/u.test(id) || counts.get(id) !== 1) return;
      const args = isRecord(request.arguments) ? request.arguments : {};
      const key = REF_TARGET_KEYS.find(name => typeof args[name] === "string" && args[name]);
      const raw = key ? String(args[key]).replace(/\s+/gu, " ").slice(0, 100) : "";
      const target = raw && sanitizeDerivedOperationalText(raw) === raw ? ` ${key}=${raw}` : "";
      rows.push(`- tool-call:${id} ${String(request.name || "tool").slice(0, 60)}${target}`);
    });
  });
  const header = "[Available tool-call refs] Cite evidence only with these exact tool-call:<id> values; other ids are rejected.";
  const kept = [];
  for (const row of rows.slice(-Math.max(0, limit)).reverse()) {
    if ([header, ...kept, row].join("\n").length > maxChars) break;
    kept.push(row);
  }
  return kept.length ? [header, ...kept.reverse()].join("\n") : "";
}

function historyKey(messages, workingDirectory) {
  if (!workingDirectory || messages.some((message) => message.hasFiles())) return "";
  try {
    // Same layout-independent transcript identity as the durable window, so
    // the key written at the end of a turn matches the host's stored history.
    const history = require("./transcript-canonical.js").canonicalTranscript(messages).entries;
    return crypto.createHash("sha256")
      .update(JSON.stringify(["canonical-v1", path.resolve(workingDirectory), history]))
      .digest("hex");
  } catch {
    return "";
  }
}

class ContinuityNoteStore {
  constructor(directory = path.join(os.homedir(), ".lmstudio", "unreal-context-compactor", "notes-v1")) {
    this.directory = directory;
  }

  read(key) {
    if (!/^[a-f0-9]{64}$/u.test(key)) return null;
    try {
      const file = path.join(this.directory, `${key}.json`);
      if (fs.statSync(file).size > MAX_STORED_NOTE_BYTES) return null;
      const stored = JSON.parse(fs.readFileSync(file, "utf8"));
      return stored?.key === key ? validateStoredNote(stored.note) : null;
    } catch {
      return null;
    }
  }

  write(key, note) {
    if (!/^[a-f0-9]{64}$/u.test(key) || !validateStoredNote(note)) return false;
    const payload = JSON.stringify({ key, note });
    // A note this store could not read back is not reported as stored.
    if (Buffer.byteLength(payload, "utf8") > MAX_STORED_NOTE_BYTES) return false;
    let temporary;
    try {
      fs.mkdirSync(this.directory, { recursive: true, mode: 0o700 });
      temporary = path.join(this.directory, `${key}.${crypto.randomUUID()}.tmp`);
      const descriptor = fs.openSync(temporary, "wx", 0o600);
      try {
        fs.writeFileSync(descriptor, payload);
        fs.fsyncSync(descriptor);
      } finally {
        fs.closeSync(descriptor);
      }
      fs.renameSync(temporary, path.join(this.directory, `${key}.json`));
    } catch {
      if (temporary) {
        try { fs.unlinkSync(temporary); } catch { /* The temporary file may already be renamed. */ }
      }
      return false;
    }
    this.prune();
    return true;
  }

  // Best effort: the note is already stored, so a pruning error (a locked or
  // concurrently removed file) does not turn the write into a failure.
  prune() {
    try {
      const entries = fs.readdirSync(this.directory)
        .filter((name) => /^[a-f0-9]{64}\.json$/u.test(name))
        .map((name) => {
          try { return { name, modified: fs.statSync(path.join(this.directory, name)).mtimeMs }; } catch { return null; }
        })
        .filter(Boolean)
        .sort((left, right) => right.modified - left.modified);
      for (const entry of entries.slice(MAX_STORED_NOTES)) {
        try { fs.unlinkSync(path.join(this.directory, entry.name)); } catch { /* Retried on the next write. */ }
      }
    } catch { /* The listing is retried on the next write. */ }
  }
}

module.exports = {
  generalNote: architecture.generalNote,
  mergeArchitecture: architecture.mergeArchitecture,
  architecturePart: architecture.architecturePart,
  noteRefs: architecture.noteRefs,
  resolveNoteUpdate,
  NOTE_INSTRUCTION,
  NOTE_MARKER,
  ContinuityNoteStore,
  attachScope,
  historyKey,
  objectiveFingerprint,
  provenanceFromMessages,
  reconcileStoredNote,
  renderAssistantNote,
  renderAvailableRefs,
  splitVisibleAnswer,
  validateDraftNote,
  validateStoredNote,
};
