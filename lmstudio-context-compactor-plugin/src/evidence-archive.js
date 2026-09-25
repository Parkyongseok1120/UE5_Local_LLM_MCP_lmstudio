"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { sanitizeRawCapabilityText, isEphemeralCapabilityKey } = require("./durable-memory-sanitizer.js");
const hash = value => crypto.createHash("sha256").update(typeof value === "string" ? value : JSON.stringify(value)).digest("hex");
const ID = /^ev_[a-f0-9]{64}$/u;
const SECRET = /^(?:receipt|approval|approvaltoken|capability|authorization|password|secret|apikey|accesstoken|refreshtoken|nextcursor|cursor)$/iu;

// Preserve arrays and ordinary source identifiers; the existing durable fact
// sanitizer's 80-item truncation is intentionally NOT an archive operation.
function redact(value, depth = 0) {
  if (depth > 40) throw new Error("archive_depth");
  if (typeof value === "string") {
    let text = sanitizeRawCapabilityText(value)
      .replace(/[A-Za-z0-9_-]{30,}\.[a-f0-9]{64}/giu, "[redacted capability]")
      .replace(/\bBearer\s+[A-Za-z0-9_.~+\/-]+=*/giu, "Bearer [redacted]")
      .replace(/\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})\b/gu, "[redacted secret]")
      .replace(/((?:api[_-]?key|password|secret|access[_-]?token|approval[_-]?token)\s*[=:]\s*)[^\s,;]+/giu, "$1[redacted]");
    try { const parsed = JSON.parse(text); if (parsed && typeof parsed === "object") text = JSON.stringify(redact(parsed, depth + 1)); } catch { /* Plain source text. */ }
    return text;
  }
  if (Array.isArray(value)) return value.map(item => redact(item, depth + 1));
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
    SECRET.test(key.replace(/[^a-z]/giu, "")) || isEphemeralCapabilityKey(key, item)
      ? "[redacted capability or transport token]" : redact(item, depth + 1)]));
}

function safeDirectory(directory, create = false) {
  const resolved = path.resolve(directory);
  if (create) fs.mkdirSync(resolved, { recursive: true, mode: 0o700 });
  const stat = fs.lstatSync(resolved);
  // The trusted storage root is canonicalized by EvidenceArchive. Reject a
  // link at the archive directory itself, while allowing OS-level aliases in
  // ancestors such as macOS /var -> /private/var.
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error("unsafe_directory");
  return resolved;
}

function readFile(file, limit) {
  const stat = fs.lstatSync(file);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 || stat.size > limit) throw new Error("unsafe_file");
  return fs.readFileSync(file, "utf8");
}

function atomicWrite(directory, name, value) {
  safeDirectory(directory, true);
  const target = path.join(directory, name);
  if (fs.existsSync(target)) readFile(target, 16 * 1024 * 1024);
  const temporary = path.join(directory, `${crypto.randomUUID()}.tmp`);
  try {
    fs.writeFileSync(temporary, JSON.stringify(value), { mode: 0o600, flag: "wx" });
    fs.renameSync(temporary, target);
  } finally {
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}

class EvidenceArchive {
  constructor(scope, options = {}) {
    if (!scope || !scope.conversation || !scope.workspace || !scope.repository || !scope.lineage)
      throw new Error("incomplete_archive_scope");
    this.scope = hash(scope);
    this.options = { maxBytes: 8 * 1024 * 1024, maxRecords: 128, ttlMs: 24 * 3600 * 1000, ...options };
    this.now = options.now || Date.now;
    this.records = new Map();
    this.root = null;
    this.directory = null;
    if (options.durable) {
      const requestedRoot = path.resolve(options.root || path.join(os.homedir(), ".lmstudio", "unreal-context-compactor", "hybrid-v1"));
      fs.mkdirSync(requestedRoot, { recursive: true, mode: 0o700 });
      const canonicalRoot = fs.realpathSync.native(requestedRoot);
      const rootStat = fs.statSync(canonicalRoot);
      if (!rootStat.isDirectory()) throw new Error("unsafe_archive_root");
      this.root = canonicalRoot;
      this.directory = path.join(canonicalRoot, this.scope);
    }
    this.stats = { captured: 0, reads: 0, returnedChars: 0, unavailable: 0 };
  }

  ensureDirectory(create = false) {
    if (!this.directory || !this.root) return null;
    const rootStat = fs.lstatSync(this.root);
    if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) throw new Error("unsafe_archive_root");
    const directory = safeDirectory(this.directory, create);
    const canonicalDirectory = fs.realpathSync.native(directory);
    const relative = path.relative(this.root, canonicalDirectory);
    if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("unsafe_directory");
    if (path.resolve(canonicalDirectory) !== path.resolve(directory)) throw new Error("unsafe_directory");
    return directory;
  }

  entries() {
    if (!this.directory) return [...this.records].map(([key, record]) => ({ key, record,
      bytes: Buffer.byteLength(JSON.stringify(record)) }));
    this.ensureDirectory(true);
    return fs.readdirSync(this.directory).filter(name => /^ev_[a-f0-9]{64}\.json$/u.test(name)).map(name => {
      const key = name.slice(0, -5);
      const text = readFile(path.join(this.directory, name), this.options.maxBytes);
      let record = null;
      try { record = JSON.parse(text); } catch { /* Count malformed records but never trust them. */ }
      return { key, record, bytes: Buffer.byteLength(text) };
    });
  }

  removeEntry(id) {
    if (!ID.test(id)) return false;
    if (!this.directory) return this.records.delete(id);
    const target = path.join(this.directory, `${id}.json`);
    if (!fs.existsSync(target)) return false;
    readFile(target, this.options.maxBytes);
    fs.unlinkSync(target);
    return true;
  }

  load(id) {
    if (!ID.test(id)) return { ok: false, errorCode: "invalid_evidence_id" };
    try {
      let record = this.records.get(id);
      if (this.directory) {
        this.ensureDirectory();
        record = JSON.parse(readFile(path.join(this.directory, `${id}.json`), this.options.maxBytes));
      }
      if (!record) return { ok: false, errorCode: "unavailable" };
      if (record.schemaVersion !== 1 || record.scope !== this.scope || record.evidenceId !== id)
        return { ok: false, errorCode: "scope_or_schema_mismatch" };
      const { recordHash, ...body } = record;
      if (hash(body) !== recordHash || hash(record.body) !== record.archivedBodyHash)
        return { ok: false, errorCode: "integrity_failed" };
      if (this.now() - record.createdAt >= this.options.ttlMs) return { ok: false, errorCode: "expired" };
      return { ok: true, record };
    } catch { return { ok: false, errorCode: "unavailable" }; }
  }

  catalog({ path: sourcePath, startOffset = 0, maxChars = 4096 } = {}) {
    if (!Number.isInteger(startOffset) || startOffset < 0 || !Number.isInteger(maxChars) || maxChars < 1)
      return { ok: false, errorCode: "invalid_range", currentFile: false, grantsMutation: false };
    // Enumerate only this archive's scope, and revalidate record integrity and
    // TTL through the same reader used for body access.
    const records = this.entries().map(e => this.load(e.key)).filter(e => e.ok).map(e => e.record)
      .filter(r => !sourcePath || r.metadata?.sourceIdentity?.path === sourcePath)
      .sort((a, b) => a.createdAt - b.createdAt || a.evidenceId.localeCompare(b.evidenceId));
    const result = { ok: true, kind: "historical_evidence_index", currentFile: false, grantsMutation: false,
      entries: [], startOffset, nextOffset: null, hasMore: false, total: records.length,
      retention: { coverage: "currently_available_records_only", maxRecords: this.options.maxRecords,
        maxBytes: this.options.maxBytes, ttlMs: this.options.ttlMs, unreferencedRecordsMayBeEvicted: true } };
    for (let i = startOffset; i < records.length; i++) {
      const r = records[i];
      const entry = { evidenceId: r.evidenceId, version: r.archivedBodyHash, toolName: r.metadata?.toolName,
        path: r.metadata?.sourceIdentity?.path, sourceVersion: r.metadata?.sourceVersion,
        sourceRange: r.metadata?.originRanges, resultStatus: r.metadata?.resultStatus };
      result.entries.push(entry); result.hasMore = i + 1 < records.length;
      result.nextOffset = result.hasMore ? i + 1 : null;
      if (JSON.stringify(result).length > maxChars) {
        result.entries.pop(); result.hasMore = true; result.nextOffset = i;
        break;
      }
    }
    if (JSON.stringify(result).length > maxChars || (result.hasMore && result.entries.length === 0))
      return { ok: false, errorCode: "response_budget_too_small", currentFile: false, grantsMutation: false };
    return result;
  }

  put(body, metadata = {}, lifecycle = {}) {
    if (typeof body !== "string" || Buffer.byteLength(body) > this.options.maxBytes / 2) return { ok: false, errorCode: "quota" };
    try {
      const sanitized = redact(body);
      const safeMetadata = redact(metadata);
      const id = `ev_${hash([this.scope, body, safeMetadata.callKey])}`;
      const existing = this.load(id);
      if (existing.ok) return existing;
      const record = { schemaVersion: 1, scope: this.scope, evidenceId: id,
        createdAt: this.now(), sourceHash: hash(body), archivedBodyHash: hash(sanitized),
        redacted: sanitized !== body, sourceChars: body.length,
        archivedRanges: [[0, sanitized.length]], rangeUnit: "utf16_code_units",
        archiveState: this.directory ? "durable" : "session_only", metadata: safeMetadata, body: sanitized };
      record.recordHash = hash(record);
      const bytes = Buffer.byteLength(JSON.stringify(record));
      const liveRefs = lifecycle.liveRefs instanceof Set ? lifecycle.liveRefs : new Set(lifecycle.liveRefs || []);
      let entries = this.entries();
      const expiredDead = entries.filter(entry => !liveRefs.has(entry.key)
        && Number.isFinite(entry.record?.createdAt)
        && this.now() - entry.record.createdAt >= this.options.ttlMs);
      for (const entry of expiredDead) this.removeEntry(entry.key);
      entries = this.entries();
      const overQuota = () => entries.length >= this.options.maxRecords
        || entries.reduce((n, entry) => n + entry.bytes, 0) + bytes > this.options.maxBytes;
      // Deterministically reclaim only dead records. A manifest-referenced
      // record remains protected even after TTL so a failed re-read is
      // explicit rather than silently pointing at unrelated evidence.
      const reclaimable = entries.filter(entry => !liveRefs.has(entry.key)).sort((left, right) => (
        Number(left.record?.createdAt || 0) - Number(right.record?.createdAt || 0)
        || left.key.localeCompare(right.key)
      ));
      while (overQuota() && reclaimable.length) {
        const victim = reclaimable.shift();
        this.removeEntry(victim.key);
        entries = entries.filter(entry => entry.key !== victim.key);
      }
      if (overQuota()) return { ok: false, errorCode: "quota" };
      if (this.directory) atomicWrite(this.directory, `${id}.json`, record);
      else this.records.set(id, record);
      const verified = this.load(id);
      if (verified.ok) this.stats.captured++;
      return verified;
    } catch { return { ok: false, errorCode: "archive_write_failed" }; }
  }

  read(id, version, start = 0, maxChars = 4096) {
    this.stats.reads++;
    if (!Number.isSafeInteger(start) || start < 0 || !Number.isSafeInteger(maxChars) || maxChars < 1 || maxChars > 8192)
      return { ok: false, errorCode: "invalid_range" };
    const loaded = this.load(id);
    if (!loaded.ok) { this.stats.unavailable++; return loaded; }
    const r = loaded.record;
    if (version !== r.archivedBodyHash) return { ok: false, errorCode: "version_mismatch" };
    if (start > r.body.length) return { ok: false, errorCode: "invalid_range" };
    const identity = r.metadata?.sourceIdentity || {};
    const sourceFacts = r.metadata?.semanticFacts || {};
    const base = { ok: true, kind: "historical_evidence_range", evidenceId: id, version,
      originalCallId: r.metadata?.providerRequestId,
      sourceHash: r.sourceHash, sourceVersion: r.metadata?.sourceVersion,
      sourceIdentity: identity, sourceIdentityDigest: hash(identity),
      toolName: r.metadata?.toolName, resultStatus: r.metadata?.resultStatus,
      errorCode: r.metadata?.errorCode ?? null,
      sourceRange: r.metadata?.originRanges,
      sourceAction: sourceFacts.action,
      sourceSince: sourceFacts.since,
      sourceUntil: sourceFacts.until,
      sourceAuthorQuery: sourceFacts.authorQuery,
      sourceAuthorQuerySemantics: sourceFacts.authorQuerySemantics,
      sourcePageStart: sourceFacts.pageStart,
      sourcePageEnd: sourceFacts.pageEnd,
      sourcePageHasMore: sourceFacts.pageHasMore,
      sourceResultComplete: sourceFacts.sourceResultComplete,
      sourceReturnedCount: sourceFacts.returnedCount,
      sourceTotal: sourceFacts.total,
      redacted: r.redacted, currentFile: false, grantsMutation: false,
      rangeUnit: r.rangeUnit, representation: "sanitized_archived_tool_envelope",
      totalChars: r.body.length, availableRange: [0, r.body.length] };
    const totalBudget = maxChars;
    const overhead = JSON.stringify({ ...base, returnedRange: [start, start], hasMore: true,
      reachedEnd: false, fullRawProvided: false, coverageState: "partial", content: "" }).length;
    const contentBudget = Math.max(0, totalBudget - overhead);
    let end = Math.min(r.body.length, start + contentBudget);
    const response = () => {
      const reachedEnd = end === r.body.length;
      const fullRawProvided = start === 0 && reachedEnd && r.redacted === false;
      return { ...base, returnedRange: [start, end],
        nextOffset: reachedEnd ? null : end,
        archiveHasMore: !reachedEnd, archiveReachedEnd: reachedEnd,
        hasMore: !reachedEnd, reachedEnd,
        fullRawProvided, coverageState: fullRawProvided ? "complete" : "partial",
        content: r.body.slice(start, end) };
    };
    let result = response(), serialized = JSON.stringify(result);
    while (serialized.length > totalBudget && end > start) {
      end = Math.max(start, end - Math.max(16, serialized.length - totalBudget + 16));
      result = response();
      serialized = JSON.stringify(result);
    }
    if (serialized.length > totalBudget || (end === start && end < r.body.length)) {
      return { ok: false, errorCode: "response_budget_too_small", evidenceId: id,
        currentFile: false, grantsMutation: false };
    }
    this.stats.returnedChars += end - start;
    return result;
  }
}

module.exports = { EvidenceArchive, hash, redact, safeDirectory, readFile, atomicWrite };
