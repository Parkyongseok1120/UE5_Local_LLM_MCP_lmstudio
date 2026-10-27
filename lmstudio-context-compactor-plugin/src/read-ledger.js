"use strict";

// Per-file read ledger, keyed by path + sourceVersion: which ranges of which
// version a source read already returned to the model, in which round, and
// which audit checks were recorded against that version. Projection and
// compaction drop bodies from the input, not the fact that they were read, so
// the ledger is persisted with the working window. Three facts stay separate:
// delivered (this ledger), visible in this input (derived from the input
// itself) and claimed (assistant statements tied to an observed version).
// A changed source hash is always a new entry; nothing here blocks a read.

const { normalizeRanges } = require("./evidence-ranges.js");
const { fileSourceKey } = require("./evidence-identity.js");

const { namesForRole } = require("./tool-capability-core.js");
const SOURCE_READ_TOOLS = new Set(namesForRole("source_read"));
const SHA256 = /^[A-Fa-f0-9]{64}$/u;
const MAX_ENTRIES = 64;
const MAX_RANGES = 16;
const MAX_EVENTS = 64;
const MAX_CHECKS = 16;
const MAX_RESULT_SIZES = 128;
const MAX_FACT_FILES = 12;
const MAX_FACT_CHARS = 2600;

const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
const short = value => String(value || "").slice(0, 8);

/** One spelling per file: scheme-relative, forward slashes, case-folded. */
function pathKey(value) {
  return String(value || "").trim().replace(/\\/gu, "/").replace(/^project:\/\//iu, "")
    .replace(/^\.\//u, "").toLowerCase();
}

function bodyOf(value) {
  for (const field of ["content", "text", "body", "code"]) if (typeof value?.[field] === "string") return value[field];
  return null;
}

function rangeText(ranges, unit) {
  if (!ranges?.length) return "";
  const shown = ranges.slice(0, 4).map(([start, end]) => unit === "line"
    ? (start === end ? `${start}` : `${start}-${end}`) : `${start}-${end + 1}`).join(", ");
  return `${unit === "line" ? "lines" : "bytes"} ${shown}${ranges.length > 4 ? ` (+${ranges.length - 4} more)` : ""}`;
}

function covers(ranges, target) {
  return Boolean(target) && normalizeRanges(ranges || []).some(([start, end]) => start <= target[0] && end >= target[1]);
}

/** The returned range of one successful source read: lines when the result
 * states them, else its byte window. A whole-file read is complete in either
 * unit. A result without a 64-hex source hash has no comparable version. */
function readFacts(value) {
  if (!value || typeof value !== "object" || value.ok === false || value.errorCode) return null;
  const path = typeof value.path === "string" ? value.path : "";
  const sourceVersion = String(value.sha256 || value.sourceVersion || "");
  if (!path || !SHA256.test(sourceVersion)) return null;
  const start = integer(value.startLine), end = integer(value.endLine), totalLines = integer(value.totalLines);
  const lines = start !== null && start >= 1 && end !== null && end >= start ? [start, end] : null;
  const offset = integer(value.offsetBytes), next = integer(value.nextOffsetBytes), size = integer(value.size);
  const bytes = offset !== null && next !== null && next > offset ? [offset, next - 1] : null;
  const complete = Boolean(lines && start === 1 && totalLines !== null && end >= totalLines)
    || Boolean(bytes && offset === 0 && size !== null && next >= size && value.hasMore !== true);
  if (!lines && !bytes && !complete) return null;
  const body = bodyOf(value);
  return { path, key: pathKey(path), sourceKey: value.sourceKey || fileSourceKey(value), sourceVersion: sourceVersion.toLowerCase(), lines, bytes, complete,
    totalLines, size, chars: body === null ? 0 : body.length };
}

/** A reported range is not proof that its body was returned. Receipt matching
 * uses readFacts; visibility and delivery use this stricter contract. */
function verifiedReadFacts(value) {
  const facts = readFacts(value), body = bodyOf(value);
  if (!facts || body === null || value.bodyOmitted || value.duplicate) return null;
  if (facts.bytes) {
    if (body.includes("\uFFFD") || Buffer.byteLength(body, "utf8") !== facts.bytes[1] - facts.bytes[0] + 1
      || facts.size !== null && facts.bytes[1] >= facts.size) return null;
    // A verified whole UTF-8 body establishes its source line origin and EOF.
    // Partial byte windows retain only byte coverage; no guessed conversion.
    if (facts.bytes[0] === 0 && facts.size === facts.bytes[1] + 1) {
      const totalLines = body.split(/\r\n|\n|\r/u).length;
      return { ...facts, lines: [1, totalLines], totalLines };
    }
    return facts;
  }
  const count = body.split(/\r\n|\n|\r/u).length;
  return facts.lines && count === facts.lines[1] - facts.lines[0] + 1
    && (facts.totalLines === null || facts.lines[1] <= facts.totalLines) ? facts : null;
}

/** The source read a body projection stands for. */
function projectionFacts(value) {
  if (value?.kind !== "archived_tool_result_projection") return null;
  const facts = value.sourceFacts && typeof value.sourceFacts === "object" ? value.sourceFacts : {};
  const read = readFacts({ ...facts, ...value.sourceIdentity, sourceKey: value.sourceKey, path: value.sourceIdentity?.path, sha256: value.sourceVersion,
    ok: true, errorCode: undefined, content: undefined });
  return read && { ...read, chars: Number(value.bodyTotalChars) || 0 };
}

/** What one archive page re-delivers: its source path, version and lines. A
 * character page of a body without verified line numbers covers the record's
 * whole source range only when it returned the whole body. */
function archivePageFacts(value) {
  if (value?.kind !== "historical_evidence_range" || value.ok === false) return null;
  const facts = value.sourceFacts && typeof value.sourceFacts === "object" ? value.sourceFacts : {};
  const path = value.sourceIdentity?.path;
  const read = readFacts({ ...facts, ...value.sourceIdentity, sourceKey: value.sourceKey, path, sha256: value.sourceVersion, ok: true, errorCode: undefined });
  if (!read) return null;
  if (value.bodyComplete === true && value.representation === "sanitized_body") {
    const whole = verifiedReadFacts({ ...facts, ...value.sourceIdentity, sourceKey: value.sourceKey,
      path, sha256: value.sourceVersion, content: bodyOf(value) });
    if (whole) return { ...whole, bodyVerified: true, partialPage: false };
  }
  const returned = Array.isArray(value.returnedLines) && Number.isSafeInteger(value.returnedLines[0])
    && value.returnedLines[1] >= value.returnedLines[0] ? [value.returnedLines[0], value.returnedLines[1]] : null;
  const body = bodyOf(value), range = returned || (value.bodyComplete === true ? read.lines : null);
  const lineCount = body === null ? 0 : body.split("\n").length - (body.endsWith("\n") ? 1 : 0);
  const bodyVerified = Boolean(body !== null && range && range[0] >= 1 && lineCount === range[1] - range[0] + 1
    && (value.bodyComplete === true || value.lineRangeComplete === true || body.endsWith("\n")));
  if (returned) return { ...read, lines: returned, bytes: null, complete: false,
    bodyVerified, chars: Number(value.returnedContentChars) || 0 };
  return { ...read, bodyVerified, partialPage: value.bodyComplete !== true, chars: Number(value.returnedContentChars) || 0 };
}

class ReadLedger {
  constructor() { this.reset(); }

  reset() {
    this.items = new Map();
    this.events = [];
    this.resultSizes = new Map();
    this.counters = { redundantRereads: 0, duplicatedChars: 0, archiveRepages: 0, deliveries: 0 };
  }

  get size() { return this.items.size; }

  entryKey(facts) { return `${facts.sourceKey || facts.key}\u0000${facts.sourceVersion}`; }

  get(path, sourceVersion, sourceKey) {
    if (sourceKey) return this.items.get(`${sourceKey}\u0000${String(sourceVersion || "").toLowerCase()}`) || null;
    const matches = this.entries().filter(entry => entry.key === pathKey(path) && entry.sourceVersion === String(sourceVersion || "").toLowerCase());
    return matches.length === 1 ? matches[0] : null;
  }

  /** The most recently delivered version of a path. */
  latestFor(path, sourceKey) {
    const key = pathKey(path);
    let latest = null;
    const candidates = this.entries().filter(entry => entry.key === key && (!sourceKey || entry.sourceKey === sourceKey));
    if (!sourceKey && new Set(candidates.map(entry => entry.sourceKey || entry.key)).size > 1) return null;
    for (const entry of candidates) if (!latest || entry.sequence > latest.sequence) latest = entry;
    return latest;
  }

  entries() { return [...this.items.values()].sort((a, b) => a.sequence - b.sequence); }

  /** How a returned read relates to what was already delivered. A different
   * hash is always new. "narrower" is a focused read strictly inside an
   * already delivered range: its body is kept for a needed function. */
  classify(facts) {
    if (!facts) return { status: "unknown", entry: null };
    const entry = this.items.get(this.entryKey(facts)) || null;
    const latest = this.latestFor(facts.path, facts.sourceKey);
    if (!entry) return { status: latest ? "new_version" : "new_file", entry: null, previous: latest };
    if (latest && latest.sourceVersion !== facts.sourceVersion)
      return { status: "new_version", entry, previous: latest };
    const covered = entry.complete || covers(entry.lineRanges, facts.lines) || covers(entry.byteRanges, facts.bytes);
    if (!covered) return { status: "new_range", entry };
    if (facts.complete) return { status: "redundant", entry };
    const container = facts.lines
      ? (entry.complete && entry.totalLines ? [1, entry.totalLines] : normalizeRanges(entry.lineRanges).find(([s, e]) => s <= facts.lines[0] && e >= facts.lines[1]))
      : (entry.complete && entry.size ? [0, entry.size - 1] : normalizeRanges(entry.byteRanges).find(([s, e]) => s <= facts.bytes[0] && e >= facts.bytes[1]));
    const target = facts.lines || facts.bytes;
    const narrower = Boolean(container && target && target[1] - target[0] < container[1] - container[0]);
    const exact = JSON.stringify([facts.lines, facts.bytes]);
    return { status: narrower && !(entry.deliveredReads || []).includes(exact) ? "narrower" : "redundant", entry };
  }

  #sequence = 0;

  /** One delivered read (or projection) of a version: merge its range. */
  record(facts, { round = null, turn = "current", toolName = "", archiveRef = null } = {}) {
    if (!facts) return null;
    const id = this.entryKey(facts);
    const changed = this.latestFor(facts.path, facts.sourceKey)?.sourceVersion !== facts.sourceVersion;
    const entry = this.items.get(id) || { path: facts.path, key: facts.key, sourceKey: facts.sourceKey, sourceVersion: facts.sourceVersion,
      lineRanges: [], byteRanges: [], complete: false, totalLines: null, size: null, chars: 0,
      firstRound: round, firstTurn: turn, lastRound: round, deliveries: 0, redundant: 0, duplicatedChars: 0,
      archivePages: 0, checks: [], sequence: 0 };
    if (changed) entry.checks = [];
    entry.deliveredReads = [...new Set([...(entry.deliveredReads || []), JSON.stringify([facts.lines, facts.bytes])])].slice(-MAX_EVENTS);
    if (facts.lines) entry.lineRanges = normalizeRanges([...entry.lineRanges, facts.lines]).slice(-MAX_RANGES);
    if (facts.bytes) entry.byteRanges = normalizeRanges([...entry.byteRanges, facts.bytes]).slice(-MAX_RANGES);
    entry.complete = entry.complete || facts.complete;
    entry.totalLines = facts.totalLines ?? entry.totalLines;
    entry.size = facts.size ?? entry.size;
    entry.chars = Math.max(entry.chars, facts.chars || 0);
    entry.lastRound = round ?? entry.lastRound;
    entry.lastTurn = turn;
    entry.deliveries += 1;
    if (toolName) entry.toolName = toolName;
    if (archiveRef?.evidenceId && archiveRef?.version) entry.archiveRef = { evidenceId: archiveRef.evidenceId, version: archiveRef.version };
    entry.sequence = ++this.#sequence;
    this.items.delete(id); this.items.set(id, entry);
    this.counters.deliveries += 1;
    while (this.items.size > MAX_ENTRIES) this.items.delete(this.items.keys().next().value);
    return entry;
  }

  attachArchiveRef(facts, archiveRef) {
    const entry = facts && this.items.get(this.entryKey(facts));
    if (entry && archiveRef?.evidenceId && archiveRef?.version) {
      // Keep a complete recovery record when a later focused read is archived.
      if (entry.archiveRef && !facts.complete && (entry.archiveCoverage?.complete || !entry.archiveCoverage && entry.complete)) return entry;
      entry.archiveRef = { evidenceId: archiveRef.evidenceId, version: archiveRef.version };
      entry.archiveCoverage = { complete: facts.complete, lines: facts.lines, bytes: facts.bytes };
    }
    return entry || null;
  }

  #event(type, entry, round, extra = {}) {
    this.events.push({ type, path: entry.path, sourceVersion: entry.sourceVersion, round, ...extra });
    if (this.events.length > MAX_EVENTS) this.events.splice(0, this.events.length - MAX_EVENTS);
  }

  /** A same-hash read of an already delivered range. */
  noteRedundant(entry, round, duplicatedChars = 0, extra = {}) {
    if (!entry) return;
    entry.redundant += 1; entry.duplicatedChars += duplicatedChars; entry.lastRound = round ?? entry.lastRound;
    this.counters.redundantRereads += 1; this.counters.duplicatedChars += duplicatedChars;
    this.#event("redundant_reread", entry, round, { duplicatedChars, ...extra });
  }

  /** An archive page of a delivered version. True when it re-delivers lines
   * the ledger already holds (restoring is not new evidence). */
  notePage(facts, round) {
    if (!facts) return false;
    const entry = this.items.get(this.entryKey(facts));
    if (!entry) return false;
    const redelivery = entry.complete || (facts.lines ? covers(entry.lineRanges, facts.lines)
      : facts.bytes ? covers(entry.byteRanges, facts.bytes) : false);
    entry.archivePages += 1;
    if (redelivery) {
      this.counters.archiveRepages += 1;
      this.#event("archive_repage", entry, round, { chars: facts.chars || 0 });
    }
    return redelivery;
  }

  /** A check recorded at the current version of the file a location names.
   * Returns false when no delivered file matches the location. */
  recordCheck(location, check, round = null, claim = "", sourceVersion = "") {
    const name = String(check || "").trim().slice(0, 48);
    const cite = /^(.*):(\d+)(?:\s*-\s*(\d+))?\s*$/u.exec(String(location || ""));
    if (!name || !cite) return false;
    const target = pathKey(cite[1]), range = [Number(cite[2]), Number(cite[3] || cite[2])];
    const matches = [...new Set(this.entries().map(entry => entry.key))].filter(key => key === target
      || key.endsWith(`/${target}`) || (target.includes("/") && target.endsWith(`/${key}`)));
    if (matches.length !== 1) return false;
    const versions = this.entries().filter(item => item.key === matches[0]);
    // A path-only claim cannot choose between observations of different hashes.
    const version = sourceVersion || (versions.length === 1 ? versions[0].sourceVersion : "");
    const entry = this.get(matches[0], version);
    if (!entry || entry !== this.latestFor(matches[0]) || range[0] < 1 || range[1] < range[0]
      || !covers(entry.lineRanges, range)) return false;
    const statement = String(claim).slice(0, 160);
    const existing = entry.checks.find(item => item.check === name && item.claim === statement
      && JSON.stringify(item.range) === JSON.stringify(range));
    if (existing) { existing.round = round; return true; }
    entry.checks.push({ kind: "assistant_claim", check: name, round, claim: statement, sourceVersion: version, range });
    if (entry.checks.length > MAX_CHECKS) entry.checks.splice(0, entry.checks.length - MAX_CHECKS);
    return true;
  }

  /** Checks done against an older version than the latest delivered one. */
  staleChecks(entry) {
    return this.entries().filter(other => (other.sourceKey || other.key) === (entry.sourceKey || entry.key) && other.sourceVersion !== entry.sourceVersion)
      .flatMap(other => other.checks.map(item => item.check)).filter(check => !entry.checks.some(item => item.check === check));
  }

  /** The envelope size the same request returned before, for budgeting. */
  rememberResultSize(requestKey, bytes) {
    if (!requestKey || !Number.isSafeInteger(bytes) || bytes <= 0) return;
    this.resultSizes.delete(requestKey); this.resultSizes.set(requestKey, bytes);
    while (this.resultSizes.size > MAX_RESULT_SIZES) this.resultSizes.delete(this.resultSizes.keys().next().value);
  }

  knownResultBytes(requestKey) { return this.resultSizes.get(requestKey); }

  stats() { return { files: this.items.size, ...this.counters }; }

  toJSON() {
    return { schemaVersion: 2, entries: this.entries().map(({ sequence: _sequence, ...entry }) => entry),
      events: this.events.slice(-MAX_EVENTS), counters: { ...this.counters },
      resultSizes: [...this.resultSizes].slice(-MAX_RESULT_SIZES) };
  }

  /** Restore a persisted ledger in place (owners keep their reference). The
   * restored entries belong to an earlier turn of the conversation. */
  load(value) {
    this.reset();
    if (!value || ![1, 2].includes(value.schemaVersion) || !Array.isArray(value.entries)) return false;
    for (const item of value.entries.slice(-MAX_ENTRIES)) {
      if (!item || typeof item.path !== "string" || !SHA256.test(String(item.sourceVersion || ""))) continue;
      const entry = { ...item, key: pathKey(item.path), sourceVersion: String(item.sourceVersion).toLowerCase(),
        sourceKey: value.schemaVersion === 2 && typeof item.sourceKey === "string" ? item.sourceKey : `legacy:${pathKey(item.path)}`,
        lineRanges: normalizeRanges(item.lineRanges || []).slice(-MAX_RANGES),
        byteRanges: normalizeRanges(item.byteRanges || []).slice(-MAX_RANGES),
        // Old manifests called self-declared claims verified. Do not migrate that authority.
        checks: Array.isArray(item.checks) ? item.checks.filter(check => check?.kind === "assistant_claim"
          && check.sourceVersion === item.sourceVersion && typeof check.check === "string").slice(-MAX_CHECKS) : [],
        deliveredReads: Array.isArray(item.deliveredReads) ? item.deliveredReads.filter(x => typeof x === "string").slice(-MAX_EVENTS) : [],
        firstTurn: "earlier", lastTurn: "earlier", sequence: ++this.#sequence };
      this.items.set(this.entryKey(entry), entry);
    }
    if (Array.isArray(value.events)) this.events = value.events.filter(item => item && typeof item.type === "string").slice(-MAX_EVENTS);
    for (const key of Object.keys(this.counters)) if (Number.isSafeInteger(value.counters?.[key])) this.counters[key] = value.counters[key];
    if (Array.isArray(value.resultSizes)) for (const [key, bytes] of value.resultSizes.slice(-MAX_RESULT_SIZES))
      if (typeof key === "string" && Number.isSafeInteger(bytes) && bytes > 0) this.resultSizes.set(key, bytes);
    return true;
  }
}

function whenDelivered(entry) {
  return entry.firstTurn === "earlier" ? "an earlier turn" : entry.firstRound !== null && entry.firstRound !== undefined
    ? `round ${entry.firstRound}` : "an earlier round";
}

function deliveredText(entry) {
  if (entry.complete) return `${entry.totalLines ? `lines 1-${entry.totalLines}` : "whole file"} (complete)`;
  return [rangeText(entry.lineRanges, "line"), rangeText(entry.byteRanges, "byte")].filter(Boolean).join("; ") + " (partial)";
}

/** The fact that replaces a same-hash re-read body. bodyVisible: the earlier
 * body is still in this input (otherwise only a repeat without the receipt
 * returns it). */
function unchangedReadNotice(entry, locator, bodyVisible = true) {
  const where = locator?.readHandle
    ? ` Page only the lines you need with evidence_first_read_context(action=read, readHandle=${locator.readHandle}, view=body, startLine, endLine${locator.outline ? ", or symbol from sourceOutline" : ""}), or read a narrower line range.`
    : " Read a narrower line range for the lines you need.";
  const visibility = bodyVisible ? "" : " That body is not in this input: repeat the read without repeatReceipt to receive it.";
  return `UNCHANGED: ${entry.path} sha ${short(entry.sourceVersion)} is the same version already returned in ${whenDelivered(entry)} `
    + `(${deliveredText(entry)}). The file was not modified; its body is not repeated here.${visibility}${where}`;
}

/** Ranges of each path+version shown verbatim in this input. */
function visibleRanges(observations) {
  const visible = new Map();
  for (const item of observations || []) {
    if (!item?.rawVerified || !item.versionRef) continue;
    const id = `${item.sourceKey || pathKey(item.path)}\u0000${String(item.versionRef).toLowerCase()}`;
    const current = visible.get(id) || { line: [], utf8_byte: [] };
    if (item.rangeUnit === "line" || item.rangeUnit === "utf8_byte")
      current[item.rangeUnit] = normalizeRanges([...current[item.rangeUnit], ...(item.verifiedRanges || [])]);
    visible.set(id, current);
  }
  return visible;
}

function visibleText(entry, shown, excerpt) {
  if (shown && (entry.complete
    ? (entry.totalLines && covers(shown.line, [1, entry.totalLines])) || (entry.size && covers(shown.utf8_byte, [0, entry.size - 1]))
    : entry.lineRanges.every(range => covers(shown.line, range)) && entry.byteRanges.every(range => covers(shown.utf8_byte, range))))
    return "in full";
  const parts = shown ? [rangeText(shown.line, "line"), rangeText(shown.utf8_byte, "byte")].filter(Boolean) : [];
  if (parts.length) return `${parts.join("; ")} only`;
  return excerpt ? "a compacted excerpt only" : "none (archived)";
}

/** One runtime fact for the input being assembled: per file, what was
 * delivered, what this input still shows, and what the assistant claimed, plus the
 * locator to page from. Null when every delivered body is still visible. */
function ledgerFact(ledger, { observations = [], excerpts = new Set(), handles = new Map(), reportNow = false } = {}) {
  const latest = new Map();
  for (const entry of ledger.entries()) latest.set(entry.sourceKey || entry.key, entry);
  const shown = visibleRanges(observations);
  const rows = [];
  let hidden = 0;
  for (const entry of [...latest.values()].reverse()) {
    const id = `${entry.sourceKey || entry.key}\u0000${entry.sourceVersion}`;
    const visible = visibleText(entry, shown.get(id), excerpts.has(id));
    const stale = ledger.staleChecks(entry);
    if (visible === "in full" && !reportNow) continue;
    hidden += visible === "in full" ? 0 : 1;
    const handle = handles.get(id);
    // Only claims that exist are listed: a "none" on every row is the same
    // text each round and reads as an instruction to re-verify the file.
    const claims = entry.checks.map(item => item.check).join(", ");
    rows.push(`- ${entry.path} sha ${short(entry.sourceVersion)}: delivered ${deliveredText(entry)} in ${whenDelivered(entry)}`
      + `${entry.deliveries > 1 ? `, ${entry.deliveries} deliveries` : ""}${entry.redundant ? `, ${entry.redundant} repeated read(s) answered UNCHANGED` : ""}`
      + `; visible now: ${visible}`
      + `${handle ? `; readHandle=${handle}` : ""}${claims ? `; recorded claims: ${claims}` : ""}`
      + `${stale.length ? `; earlier-version claims need re-check: ${stale.join(", ")}` : ""}`);
  }
  if (!hidden && !reportNow) return null;
  const lines = [];
  let used = 0;
  for (const row of rows.slice(0, MAX_FACT_FILES)) {
    if (used + row.length > MAX_FACT_CHARS) break;
    lines.push(row); used += row.length;
  }
  const more = rows.length - lines.length;
  return [
    "Runtime read ledger (kept across compaction). delivered = returned earlier; visible = in this input; recorded claims = assistant claims with checked citations, not factual verification or file-level audit completion.",
    ...lines, ...(more > 0 ? [`- ${more} more file(s) not listed`] : []),
    "These are last-observed versions, not a fresh disk check. Repeating a read whose body is still visible returns a short "
      + "UNCHANGED fact; a body that is not visible now comes back from the same read. To restore only part of it, page the "
      + "lines you need from the readHandle or the row's path (view=body with startLine/endLine) or read a narrower line range. "
      + "Delivered earlier is not visible now: quote only text present in this input.",
  ].join("\n");
}

module.exports = { ReadLedger, SOURCE_READ_TOOLS, archivePageFacts, deliveredText, ledgerFact, pathKey, projectionFacts,
  readFacts, verifiedReadFacts, unchangedReadNotice, visibleRanges };
