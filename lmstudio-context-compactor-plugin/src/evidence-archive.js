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
  let current = resolved;
  for (;;) {
    const stat = fs.lstatSync(current);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error("unsafe_directory");
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
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
    this.directory = options.durable ? path.join(options.root || path.join(os.homedir(), ".lmstudio", "unreal-context-compactor", "hybrid-v1"), this.scope) : null;
    this.stats = { captured: 0, reads: 0, returnedChars: 0, unavailable: 0 };
  }

  load(id) {
    if (!ID.test(id)) return { ok: false, errorCode: "invalid_evidence_id" };
    try {
      let record = this.records.get(id);
      if (this.directory) {
        safeDirectory(this.directory);
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

  put(body, metadata = {}) {
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
      let entries = [...this.records].map(([key, item]) => ({ key, bytes: Buffer.byteLength(JSON.stringify(item)) }));
      if (this.directory) {
        safeDirectory(this.directory, true);
        entries = fs.readdirSync(this.directory).filter(name => /^ev_[a-f0-9]{64}\.json$/u.test(name))
          .map(name => ({ key: name.slice(0, -5), bytes: Buffer.byteLength(readFile(path.join(this.directory, name), this.options.maxBytes)) }));
      }
      // Refuse quota overflow instead of evicting evidence referenced by a live window.
      if (entries.length >= this.options.maxRecords || entries.reduce((n, e) => n + e.bytes, 0) + bytes > this.options.maxBytes)
        return { ok: false, errorCode: "quota" };
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
    const end = Math.min(r.body.length, start + maxChars);
    this.stats.returnedChars += end - start;
    return { ok: true, kind: "historical_evidence_range", evidenceId: id, version,
      sourceHash: r.sourceHash, redacted: r.redacted, currentFile: false, grantsMutation: false,
      rangeUnit: r.rangeUnit, returnedRange: [start, end], totalChars: r.body.length,
      hasMore: end < r.body.length, metadata: r.metadata, content: r.body.slice(start, end) };
  }
}

module.exports = { EvidenceArchive, hash, redact, safeDirectory, readFile, atomicWrite };
