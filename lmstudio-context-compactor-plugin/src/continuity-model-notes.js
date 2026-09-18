"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { buildObjectiveContinuity } = require("./continuity-objectives.js");
const { pathApiFor } = require("./continuity-file-observations.js");
const { normalizedTextKey } = require("./continuity-text.js");
const { sanitizeDerivedOperationalText } = require("./durable-memory-sanitizer.js");

const NOTE_START = "\n<!-- direct-continuity-note-v1 -->\n<continuity-note>\n";
const NOTE_END = "\n</continuity-note>";
const NOTE_MARKER = "[Prior assistant continuity note v1]";
const MAX_NOTE_CHARS = 1500;
const MAX_NOTE_ITEMS = 4;
const MAX_STORED_NOTES = 128;
const NOTE_INSTRUCTION = [
  "You may append a private continuity note only when a decision, rejected hypothesis, or open question materially changes.",
  "Use only these optional JSON arrays: decisions [{statement, rationale, refs?}], rejectedHypotheses [{hypothesis, reason, refs?}], openQuestions [{question, refs?}]. No other keys. refs may contain only tool-call:<id> values for completed tool calls already in this conversation; omit uncertain refs.",
  "Write the normal user-facing answer first. Then append this footer with only your JSON object between the tags:",
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

function parseItems(value, keys, textFields) {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > MAX_NOTE_ITEMS) return null;
  const items = [];
  for (const item of value) {
    if (!isRecord(item) || !exactKeys(item, [...keys, "refs"])) return null;
    const parsed = {};
    for (const [field, limit] of textFields) {
      const text = shortText(item[field], limit);
      if (!text) return null;
      parsed[field] = text;
    }
    const refs = parseRefs(item.refs);
    if (refs === null) return null;
    if (refs !== undefined) parsed.refs = refs;
    items.push(parsed);
  }
  return items;
}

function validateDraftNote(value) {
  if (!isRecord(value) || !exactKeys(value, ["decisions", "rejectedHypotheses", "openQuestions"])) return null;
  const decisions = parseItems(value.decisions, ["statement", "rationale"], [["statement", 300], ["rationale", 400]]);
  const rejectedHypotheses = parseItems(value.rejectedHypotheses, ["hypothesis", "reason"], [["hypothesis", 300], ["reason", 400]]);
  const openQuestions = parseItems(value.openQuestions, ["question"], [["question", 350]]);
  if (!decisions || !rejectedHypotheses || !openQuestions) return null;
  if (decisions.length + rejectedHypotheses.length + openQuestions.length > MAX_NOTE_ITEMS) return null;
  const note = { decisions, rejectedHypotheses, openQuestions };
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
  const start = source.lastIndexOf(NOTE_START);
  if (start < 0 || insideCodeFence(source, start)) {
    return { visibleText: source, hasFooter: false, note: null };
  }
  const visibleText = source.slice(0, start).trimEnd() || "The model did not provide a visible answer.";
  const suffix = source.slice(start + NOTE_START.length).trimEnd();
  if (!suffix.endsWith(NOTE_END)) return { visibleText, hasFooter: true, note: null };
  const rawJson = suffix.slice(0, -NOTE_END.length).trim();
  if (rawJson.length > 1800) return { visibleText, hasFooter: true, note: null };
  try {
    return { visibleText, hasFooter: true, note: validateDraftNote(JSON.parse(rawJson)) };
  } catch {
    return { visibleText, hasFooter: true, note: null };
  }
}

function objectiveFingerprint(messages) {
  const normalized = messages.map((message, index) => ({
    role: message.getRole(), text: message.getText(), index,
    toolRequests: message.getToolCallRequests(),
  }));
  const objective = buildObjectiveContinuity(normalized).activeObjective?.text;
  const key = normalizedTextKey(objective);
  return key ? crypto.createHash("sha256").update(key).digest("hex") : "";
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
  const projects = new Map();
  const requestCounts = new Map();
  const completedIds = new Set();
  for (let index = Math.max(0, objectiveIndex); index < messages.length; index += 1) {
    const message = messages[index];
    if (message.getRole() === "assistant") {
      for (const request of message.getToolCallRequests()) {
        const id = String(request.id || "");
        if (id) requestCounts.set(id, (requestCounts.get(id) || 0) + 1);
      }
    }
    if (message.getRole() !== "tool") continue;
    for (const result of message.getToolCallResults()) {
      if (result.toolCallId && requestCounts.has(String(result.toolCallId))) {
        completedIds.add(String(result.toolCallId));
      }
      const content = String(result.content || "");
      if (content.length > 262144) continue;
      let value;
      try { value = JSON.parse(content); } catch { continue; }
      if (!isRecord(value)) continue;
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
      if (unrealRoot && unrealRoot.toLowerCase().endsWith(".uproject")) {
        const identity = `unreal:${crypto.createHash("sha256").update(unrealRoot).digest("hex")}`;
        projects.set(identity, unrealRoot);
      }
    }
  }
  const verifiedToolIds = new Set([...completedIds].filter((id) => requestCounts.get(id) === 1));
  return {
    projectIdentity: projects.size === 1 ? [...projects.keys()][0] : "",
    projectState: projects.size === 0 ? "unobserved" : projects.size === 1 ? "single" : "mixed",
    verifiedToolIds,
  };
}

function verifiedRefs(draft, verifiedToolIds) {
  return Object.fromEntries(["decisions", "rejectedHypotheses", "openQuestions"].map((key) => [
    key, (draft[key] || []).map((item) => {
      const { refs: _unverifiedRefs, ...body } = item;
      const refs = (item.refs || []).filter((ref) => {
        const match = /^tool-call:([A-Za-z0-9_-]{1,80})$/u.exec(ref);
        return match && verifiedToolIds.has(match[1]);
      });
      return refs.length ? { ...body, refs } : body;
    }),
  ]));
}

function attachScope(draft, fingerprint, messages = []) {
  if (!draft || !fingerprint) return null;
  const validated = validateDraftNote(draft);
  if (!validated) return null;
  const provenance = provenanceFromMessages(messages);
  const note = { scope: { objectiveFingerprint: fingerprint,
    ...(provenance.projectIdentity ? { projectIdentity: provenance.projectIdentity } : {}) },
  ...verifiedRefs(validated, provenance.verifiedToolIds) };
  return JSON.stringify(note).length <= MAX_NOTE_CHARS ? note : null;
}

function reconcileStoredNote(note, messages) {
  const validated = validateStoredNote(note);
  if (!validated) return null;
  const provenance = provenanceFromMessages(messages);
  if (validated.scope.projectIdentity && validated.scope.projectIdentity !== provenance.projectIdentity) return null;
  const reconciled = { scope: validated.scope, ...verifiedRefs(validated, provenance.verifiedToolIds) };
  return validateStoredNote(reconciled);
}

function validateStoredNote(value) {
  if (!isRecord(value) || !exactKeys(value, ["scope", "decisions", "rejectedHypotheses", "openQuestions"])) return null;
  if (!isRecord(value.scope) || !exactKeys(value.scope, ["objectiveFingerprint", "projectIdentity"])) return null;
  const fingerprint = value.scope.objectiveFingerprint;
  if (typeof fingerprint !== "string" || !/^[a-f0-9]{64}$/u.test(fingerprint)) return null;
  if (value.scope.projectIdentity !== undefined
    && (typeof value.scope.projectIdentity !== "string" || value.scope.projectIdentity.length > 128)) return null;
  const draft = validateDraftNote({
    decisions: value.decisions,
    rejectedHypotheses: value.rejectedHypotheses,
    openQuestions: value.openQuestions,
  });
  if (!draft) return null;
  const note = { scope: { objectiveFingerprint: fingerprint,
    ...(value.scope.projectIdentity ? { projectIdentity: value.scope.projectIdentity } : {}) }, ...draft };
  return JSON.stringify(note).length <= MAX_NOTE_CHARS ? note : null;
}

function renderAssistantNote(note) {
  return `${NOTE_MARKER}\nPrior assistant judgments, not verified facts. Current user instructions and newer tool observations take precedence. ${note.scope.projectIdentity ? "Project identity was bound to a tool observation." : "No single project identity was proven; do not attribute these notes to the active project."}\n${JSON.stringify({ modelNotes: note })}`;
}

function historyKey(messages, workingDirectory) {
  if (!workingDirectory || messages.some((message) => message.hasFiles())) return "";
  try {
    const history = messages.map((message) => ({
      role: message.getRole(), text: message.getText(),
      requests: message.getToolCallRequests(), results: message.getToolCallResults(),
    }));
    return crypto.createHash("sha256")
      .update(JSON.stringify([path.resolve(workingDirectory), history]))
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
      if (fs.statSync(file).size > 4096) return null;
      const stored = JSON.parse(fs.readFileSync(file, "utf8"));
      return stored?.key === key ? validateStoredNote(stored.note) : null;
    } catch {
      return null;
    }
  }

  write(key, note) {
    if (!/^[a-f0-9]{64}$/u.test(key) || !validateStoredNote(note)) return false;
    let temporary;
    try {
      fs.mkdirSync(this.directory, { recursive: true, mode: 0o700 });
      temporary = path.join(this.directory, `${key}.${crypto.randomUUID()}.tmp`);
      fs.writeFileSync(temporary, JSON.stringify({ key, note }), { mode: 0o600, flag: "wx" });
      fs.renameSync(temporary, path.join(this.directory, `${key}.json`));
      const entries = fs.readdirSync(this.directory)
        .filter((name) => /^[a-f0-9]{64}\.json$/u.test(name))
        .map((name) => ({ name, modified: fs.statSync(path.join(this.directory, name)).mtimeMs }))
        .sort((left, right) => right.modified - left.modified);
      for (const entry of entries.slice(MAX_STORED_NOTES)) fs.unlinkSync(path.join(this.directory, entry.name));
      return true;
    } catch {
      if (temporary) {
        try { fs.unlinkSync(temporary); } catch { /* The temporary file may already be renamed. */ }
      }
      return false;
    }
  }
}

module.exports = {
  NOTE_INSTRUCTION,
  NOTE_MARKER,
  ContinuityNoteStore,
  attachScope,
  historyKey,
  objectiveFingerprint,
  provenanceFromMessages,
  reconcileStoredNote,
  renderAssistantNote,
  splitVisibleAnswer,
  validateDraftNote,
  validateStoredNote,
};
