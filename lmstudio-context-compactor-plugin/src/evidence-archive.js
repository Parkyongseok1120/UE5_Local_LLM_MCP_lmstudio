"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { ARCHIVE_READ_CONTRACT } = require("./evidence-read-contract.js");
const { sanitizeRawCapabilityText, isEphemeralCapabilityKey } = require("./durable-memory-sanitizer.js");
const hash = value => crypto.createHash("sha256").update(typeof value === "string" ? value : JSON.stringify(value)).digest("hex");
const ID = /^ev_[a-f0-9]{64}$/u;
const SECRET = /^(?:receipt|approval|approvaltoken|capability|authorization|password|secret|apikey|accesstoken|refreshtoken|nextcursor|cursor)$/iu;

// Both projection and rehydration select the same sanitized representation.
function evidenceBodyView(payload) {
  if (!payload || typeof payload !== "object") return null;
  for (const field of ["content", "text", "body", "code"])
    if (typeof payload[field] === "string") return { field, body: payload[field] };
  for (const field of ["items", "rows", "entries", "matches"])
    if (Array.isArray(payload[field])) return { field, body: JSON.stringify(payload[field]) };
  return null;
}

function evidenceSemanticFacts(payload) {
  const facts = {};
  if (!payload || typeof payload !== "object") return facts;
  for (const key of ["kind", "ok", "status", "errorCode", "action", "comparison", "base", "head",
    "since", "until", "authorQuery", "authorQuerySemantics", "pageStart", "pageEnd", "pageHasMore",
    "hasMore", "sourceResultComplete", "returnedRange", "lineRange", "startLine", "endLine",
    "totalLines", "returnedCount", "total", "sourceMode", "evidenceSource", "coverage", "declarationProof",
    "matchCount", "matchMetadataOmitted", "readBytes", "offsetBytes", "nextOffsetBytes", "size",
    "rangeUnit", "truncated", "requestedEndLine", "nextStartLine", "representation", "symbol",
    "rangeComplete", "requestedRangeComplete", "moreLinesInFile",
    "candidateCount", "candidateLines", "selection", "ambiguity", "signatureVerification", "absenceProof",
    // Projection and later historical indexes must retain the validator's
    // proof boundary even after its raw excerpt has been discarded.
    "validationScope", "factualEvidenceVerified", "wholeTaskVerified"]) {
    if (payload[key] !== undefined) facts[key] = payload[key];
  }
  if (facts.pageHasMore === undefined && payload.hasMore !== undefined) facts.pageHasMore = payload.hasMore;
  return redact(facts);
}

// Preserve arrays and ordinary source identifiers; the existing durable fact
// sanitizer's 80-item truncation is intentionally NOT an archive operation.
function redact(value, depth = 0) {
  if (depth > 40) throw new Error("archive_depth");
  if (typeof value === "string") {
    let text = sanitizeRawCapabilityText(value)
      .replace(/(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{30,}\.[a-f0-9]{64}/giu, "[redacted capability]")
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

// Locks are held only for synchronous, millisecond-scale sections. A lock file
// older than this belongs to a process that died mid-section; its PID may have
// been reused (common on Windows), so liveness alone cannot clear it.
const LOCK_STALE_MS = 30 * 1000;
const LOCK_RETRY_DELAYS_MS = Object.freeze([10, 20, 40, 80, 80]);
const LEASE_STALE_MS = 6 * 3600 * 1000;

function sleepSync(ms) { Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms); }

function withDirectoryLock(directory, action) {
  safeDirectory(directory, true);
  // Brief contention is normal between executions sharing a scope; retry a
  // few times before reporting the archive as unavailable.
  for (let attempt = 0; ; attempt += 1) {
    try { return tryDirectoryLock(directory, action); }
    catch (error) {
      if (!(error instanceof LockContention) || attempt >= LOCK_RETRY_DELAYS_MS.length) {
        throw error instanceof LockContention ? new Error("archive_lock_unavailable") : error;
      }
      sleepSync(LOCK_RETRY_DELAYS_MS[attempt]);
    }
  }
}

class LockContention extends Error {}

function tryDirectoryLock(directory, action) {
  // A bounded bakery lock has no shared stale-reclamation lock. Each
  // contender owns a unique file; dead contenders can be ignored without
  // unlinking a file another writer might have replaced.
  const nonce = crypto.randomUUID(), name = `lock-${nonce}.json`;
  const file = path.join(directory, name);
  const owner = { pid: process.pid, nonce, choosing: true, ticket: 0 };
  const contenders = () => {
    const names = fs.readdirSync(directory).filter(n => /^lock-[a-f0-9-]+\.json$/.test(n));
    if (names.length > 256) throw new Error("archive_lock_capacity");
    const live = [];
    for (const entry of names) {
      const target = path.join(directory, entry);
      let value, stale = false;
      try {
        stale = entry !== name && Date.now() - fs.lstatSync(target).mtimeMs > LOCK_STALE_MS;
        if (!stale) value = JSON.parse(readFile(target, 1024));
      } catch (error) { if (error.code === "ENOENT") continue; throw error; }
      if (!stale && (!Number.isSafeInteger(value.pid) || value.pid < 1 || !Number.isSafeInteger(value.ticket)
        || value.ticket < 0 || typeof value.nonce !== "string")) throw new Error("archive_lock_unavailable");
      let dead = stale;
      if (!dead) { try { process.kill(value.pid, 0); } catch (error) { dead = error.code === "ESRCH"; } }
      if (dead) {
        // Unique owner paths are never reused. Cleanup failure does not
        // make a dead or stale process a live contender.
        try { fs.unlinkSync(target); } catch { /* optional */ }
      } else live.push(value);
    }
    return live;
  };
  try {
    atomicWrite(directory, name, owner);
    const ticket = Math.max(0, ...contenders().map(value => value.ticket)) + 1;
    if (!Number.isSafeInteger(ticket)) throw new Error("archive_lock_capacity");
    atomicWrite(directory, name, { ...owner, choosing: false, ticket });
    for (const other of contenders()) {
      if (other.nonce === nonce) continue;
      if (other.choosing || other.ticket < ticket || other.ticket === ticket && other.nonce < nonce)
        throw new LockContention("archive_lock_contended");
    }
    return action();
  } finally {
    try { fs.unlinkSync(file); } catch (error) { if (error.code !== "ENOENT") throw error; }
  }
}

class EvidenceArchive {
  constructor(scope, options = {}) {
    if (!scope || !scope.conversation || !scope.workspace || !scope.repository || !scope.lineage)
      throw new Error("incomplete_archive_scope");
    this.scope = hash(scope);
    // Seven days lets a conversation resume on a later day; quotas below still
    // bound each scope, and idle scopes are reclaimed after the same period.
    this.options = { maxBytes: 8 * 1024 * 1024, maxRecords: 128, ttlMs: 7 * 24 * 3600 * 1000,
      maxWindows: 128, maxWindowBytes: 8 * 1024 * 1024, ...options };
    this.now = options.now || Date.now;
    this.records = new Map();
    this.catalogSnapshots = new Map();
    this.root = null;
    this.directory = null;
    if (options.durable) {
      const requestedRoot = path.resolve(options.root || path.join(os.homedir(), ".lmstudio", "unreal-context-compactor", "hybrid-v1"), "contract-v2");
      fs.mkdirSync(requestedRoot, { recursive: true, mode: 0o700 });
      const canonicalRoot = fs.realpathSync.native(requestedRoot);
      const rootStat = fs.statSync(canonicalRoot);
      if (!rootStat.isDirectory()) throw new Error("unsafe_archive_root");
      this.root = canonicalRoot;
      this.directory = path.join(canonicalRoot, this.scope);
    }
    this.stats = { captured: 0, reads: 0, returnedChars: 0, unavailable: 0 };
    this.protectedWindows = new Set();
    this.lineage = options.lineage || scope.lineage;
    this.membership = new Map();
    this.ownerId = crypto.randomUUID();
    this.pins = new Set();
    this.protectedRecords = new Set();
    this.lockDepth = 0;
    if (this.root) this.cleanupScopes();
  }

  // A separate namespace fences old GC writers that do not understand leases.
  setAccess(lineage, inherited = []) {
    this.catalogSnapshots.clear();
    this.lineage = lineage || this.lineage;
    this.membership = new Map(inherited.slice(-this.options.maxRecords * 2));
  }

  exportMembership() {
    const inherited = [...this.membership];
    for (const entry of this.entries()) {
      if (entry.record?.lineage === this.lineage) {
        const loaded = this.load(entry.key);
        if (loaded.ok) inherited.push([entry.key, loaded.record.archivedBodyHash]);
      }
    }
    return [...new Map(inherited)].slice(-this.options.maxRecords * 2);
  }

  withStorageLock(action) {
    if (!this.directory || this.lockDepth) return action();
    this.ensureDirectory(true);
    return withDirectoryLock(this.directory, () => {
      this.lockDepth++;
      try { return action(); } finally { this.lockDepth--; }
    });
  }

  leaseFacts(directory = this.directory, reclaim = this.lockDepth > 0) {
    if (!directory) return { refs: new Set(this.pins), windows: new Set(this.protectedWindows) };
    const refs = new Set(), windows = new Set();
    for (const name of fs.readdirSync(directory).filter(n => /^lease-[a-f0-9-]+\.json$/.test(n))) {
      const file = path.join(directory, name);
      let lease;
      try { lease = JSON.parse(readFile(file, 256 * 1024)); }
      catch (error) { if (error.code === "ENOENT") continue; throw error; }
      if (!Number.isSafeInteger(lease.pid) || lease.pid < 1) throw new Error("lease_liveness_unknown");
      // Live executions refresh their lease on every pin update; one untouched
      // for hours belongs to a dead process even if its PID was reused.
      let dead = Number.isFinite(lease.updatedAt) && this.now() - lease.updatedAt > LEASE_STALE_MS;
      if (!dead) { try { process.kill(lease.pid, 0); } catch (error) { dead = error.code === "ESRCH"; } }
      if (dead) {
        if (reclaim) try { fs.unlinkSync(file); } catch (error) { if (error.code !== "ENOENT") throw error; }
        continue;
      }
      for (const id of lease.refs || []) if (ID.test(id)) refs.add(id);
      for (const name of lease.windows || []) windows.add(name);
    }
    return { refs, windows };
  }

  setPins(ids) {
    return this.withStorageLock(() => {
      const next = new Set(ids);
      if (next.size > this.options.maxRecords * 2) throw new Error("pin_capacity");
      if (this.directory) atomicWrite(this.directory, `lease-${this.ownerId}.json`, {
        schemaVersion: 2, pid: process.pid, owner: this.ownerId, lineage: this.lineage,
        refs: [...next], windows: [...this.protectedWindows], updatedAt: this.now(),
      });
      this.pins = next;
    });
  }

  close() {
    this.catalogSnapshots.clear();
    // This unique owner path is never reused. Releasing it does not compete
    // with publication/GC and must work while another instance holds the lock.
    if (this.directory) {
      const file = path.join(this.directory, `lease-${this.ownerId}.json`);
      try { readFile(file, 256 * 1024); fs.unlinkSync(file); }
      catch (error) { if (error.code !== "ENOENT") throw error; }
    }
    this.pins.clear();
  }

  // Passive retention stays at the existing storage owner. Current scopes and
  // explicitly active lineage/parent files are never candidates for cleanup.
  cleanupScopes() {
    if (!this.root) return;
    try {
      const cutoff = this.now() - this.options.ttlMs;
      let removed = 0;
      for (const name of fs.readdirSync(this.root)) {
        if (removed >= 16) break;
        if (!/^[a-f0-9]{64}$/u.test(name) || name === this.scope) continue;
        const directory = path.join(this.root, name), stat = fs.lstatSync(directory);
        if (!stat.isDirectory() || stat.isSymbolicLink() || stat.mtimeMs > cutoff) continue;
        if (fs.realpathSync.native(directory) !== directory) continue;
        try {
          withDirectoryLock(directory, () => {
            // Re-enumerate under the target scope's lock. A stale pre-lock
            // directory listing cannot authorize deletion after reactivation.
            const live = this.leaseFacts(directory, true);
            if (live.refs.size || live.windows.size) return;
            const files = fs.readdirSync(directory).filter(file => !/^lock-[a-f0-9-]+\.json$/.test(file));
            if (files.some(file => !/^(?:ev_[a-f0-9]{64}|window(?:-[A-Za-z0-9-]+)?)\.json$/u.test(file))) return;
            const paths = files.map(file => path.join(directory, file));
            if (paths.some(file => { const current = fs.lstatSync(file);
              return !current.isFile() || current.isSymbolicLink() || current.nlink !== 1 || current.mtimeMs > cutoff;
            })) return;
            for (const file of paths) {
              safeDirectory(directory);
              if (fs.realpathSync.native(directory) !== directory) throw new Error("unsafe_scope_directory");
              fs.unlinkSync(file);
            }
          });
          // A new owner may have started after unlock. Removing only an empty
          // directory is safe; a new lock/lease makes this optional step fail.
          fs.rmdirSync(directory); removed++;
        } catch { /* Busy or reactivated scope: keep it. */ }
      }
    } catch { /* Cleanup is optional and must not turn a valid read into failure. */ }
  }

  windowMetadata() {
    if (!this.directory) return [];
    this.ensureDirectory(true);
    return fs.readdirSync(this.directory).filter(name => /^window(?:-[A-Za-z0-9-]+)?\.json$/u.test(name)).map(name => {
      const file = path.join(this.directory, name), stat = fs.lstatSync(file);
      return { name, file, bytes: stat.size, modified: stat.mtimeMs,
        safe: stat.isFile() && !stat.isSymbolicLink() && stat.nlink === 1 };
    });
  }

  windowEntries(metadata = this.windowMetadata()) {
    return metadata.map(entry => {
      const source = readFile(entry.file, 16 * 1024 * 1024);
      let body = null;
      try { const parsed = JSON.parse(source), { digest, ...value } = parsed;
        if (digest === hash(value) && value.scope === this.scope) body = value;
      } catch { /* Corrupt files are counted but never supply protected refs. */ }
      return { ...entry, body, bytes: Buffer.byteLength(source) };
    });
  }

  removeWindow(entry) {
    this.ensureDirectory();
    const stat = fs.lstatSync(entry.file);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1) throw new Error("unsafe_window_file");
    fs.unlinkSync(entry.file);
  }

  cleanupWindows() {
    if (this.directory && !this.lockDepth) {
      try { return this.withStorageLock(() => this.cleanupWindows()); } catch { return null; }
    }
    let entries, manifestsRead = false;
    try {
      // Legacy archives may contain thousands of manifests. Prune by cheap
      // file metadata before reading bodies, while keeping active/parent files.
      entries = this.windowMetadata();
      let bytes = entries.reduce((n, item) => n + item.bytes, 0);
      let count = entries.length;
      const removed = new Set();
      const protectedWindows = new Set([...this.protectedWindows, ...this.leaseFacts().windows]);
      const oldest = entries.filter(entry => entry.safe && !protectedWindows.has(entry.name))
        .sort((a, b) => a.modified - b.modified || a.name.localeCompare(b.name));
      for (const entry of oldest) {
        if (this.now() - entry.modified < this.options.ttlMs && count <= this.options.maxWindows
          && bytes <= this.options.maxWindowBytes) continue;
        try { this.removeWindow(entry); } catch { continue; }
        removed.add(entry.name); count--; bytes -= entry.bytes;
      }
      entries = this.windowEntries(entries.filter(entry => !removed.has(entry.name))); manifestsRead = true;
      const expired = entry => this.now() - (entry.body?.createdAt ?? entry.modified) >= this.options.ttlMs;
      const candidates = entries.filter(entry => !protectedWindows.has(entry.name))
        .sort((a, b) => a.modified - b.modified || a.name.localeCompare(b.name));
      for (const entry of candidates) {
        const over = entries.length > this.options.maxWindows
          || entries.reduce((n, item) => n + item.bytes, 0) > this.options.maxWindowBytes;
        if (!expired(entry) && !over) continue;
        try { this.removeWindow(entry); }
        catch { continue; }
        entries = entries.filter(item => item.name !== entry.name);
      }
      return entries;
    } catch { return manifestsRead ? entries : null; }
  }

  windowRefs() {
    const refs = new Set();
    const windows = this.cleanupWindows();
    if (!windows) throw new Error("window_retention_unavailable");
    for (const entry of windows) for (const pair of entry.body?.refs || []) {
      if (Array.isArray(pair) && ID.test(pair[0])) refs.add(pair[0]);
    }
    return refs;
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
    // A read is activity too; passive scope expiration must not remove a
    // concurrently used archive just because no new records were written.
    try { fs.utimesSync(directory, new Date(this.now()), new Date(this.now())); } catch { /* Advisory only. */ }
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

  /** Records referenced by a restored sealed window stay readable beyond
   * their TTL for this execution, matching put()'s retention rule. */
  protectRecords(ids) { for (const id of ids) if (ID.test(id)) this.protectedRecords.add(id); }

  load(id, { allowExpired = false } = {}) {
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
      if (record.lineage !== this.lineage && this.membership.get(id) !== record.archivedBodyHash)
        return { ok: false, errorCode: "lineage_access_denied" };
      if (this.now() - record.createdAt >= this.options.ttlMs && !allowExpired && !this.protectedRecords.has(id))
        return { ok: false, errorCode: "expired" };
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

  readHandleFor(record, view = "body") {
    return `h_${hash([this.scope, this.lineage, record.evidenceId, record.archivedBodyHash, view]).slice(0, 24)}`;
  }

  readByHandle(handle, start = 0, maxChars = 4096, view = "body") {
    if (!/^h_[a-f0-9]{24}$/.test(String(handle)) || !["body", "envelope"].includes(view))
      return { ok: false, errorCode: "invalid_read_handle" };
    const candidates = this.entries().map(e => this.load(e.key)).filter(e => e.ok
      && this.readHandleFor(e.record, view) === handle);
    if (candidates.length !== 1) return { ok: false, errorCode: "read_handle_unavailable", currentFile: false, grantsMutation: false };
    const record = candidates[0].record;
    return this.read(record.evidenceId, record.archivedBodyHash, start, maxChars, view);
  }

  // Bounded execution-local snapshots make continuation insensitive to GC and
  // new records. Access and version are still revalidated for every page.
  catalogPage({ path: sourcePath, continuation, maxChars = 4096 } = {}) {
    if (!Number.isSafeInteger(maxChars) || maxChars < 1 || maxChars > ARCHIVE_READ_CONTRACT.maximumEnvelopeChars)
      return { ok: false, errorCode: "invalid_range" };
    const now = this.now();
    for (const [key, value] of this.catalogSnapshots) if (now >= value.expires) this.catalogSnapshots.delete(key);
    const match = typeof continuation === "string" && /^catalog_([a-f0-9]{24})\.(\d+)$/.exec(continuation);
    const key = continuation ? match?.[1] : crypto.randomBytes(12).toString("hex");
    let snapshot = key && this.catalogSnapshots.get(key);
    let index = match ? Number(match[2]) : 0;
    if (continuation && (!snapshot || !Number.isSafeInteger(index) || index > snapshot.refs.length
      || (sourcePath !== undefined && sourcePath !== snapshot.path)))
      return { ok: false, errorCode: "catalog_continuation_unavailable", retryAction: "restart_catalog" };
    if (!snapshot) snapshot = { path: sourcePath, expires: now + Math.min(this.options.ttlMs, 300000),
      refs: this.entries().map(e => this.load(e.key)).filter(e => e.ok).map(e => e.record)
        .filter(r => !sourcePath || r.metadata?.sourceIdentity?.path === sourcePath)
        .sort((a, b) => a.createdAt - b.createdAt || a.evidenceId.localeCompare(b.evidenceId))
        .map(r => [r.evidenceId, r.archivedBodyHash]) };
    const result = { ok: true, kind: "historical_evidence_index", catalogVersion: 2,
      currentFile: false, grantsMutation: false, entries: [], hasMore: false, nextContinuation: null,
      retention: "snapshot_membership; records may expire; restart catalog after execution restart" };
    for (; index < snapshot.refs.length; index++) {
      const [id, version] = snapshot.refs[index], loaded = this.load(id);
      if (!loaded.ok || loaded.record.archivedBodyHash !== version) continue;
      const r = loaded.record;
      const entry = { evidenceId: id, version, readHandle: this.readHandleFor(r), view: "body",
        path: r.metadata?.sourceIdentity?.path, toolName: r.metadata?.toolName,
        sourceVersion: r.metadata?.sourceVersion, sourceRange: r.metadata?.originRanges };
      result.entries.push(entry);
      result.hasMore = index + 1 < snapshot.refs.length;
      result.nextContinuation = result.hasMore ? `catalog_${key}.${index + 1}` : null;
      if (JSON.stringify(result).length > maxChars) {
        result.entries.pop(); result.hasMore = true; result.nextContinuation = `catalog_${key}.${index}`;
        break;
      }
    }
    if (index === snapshot.refs.length) { result.hasMore = false; result.nextContinuation = null; }
    if (JSON.stringify(result).length > maxChars || (!result.entries.length && result.hasMore))
      return { ok: false, errorCode: "response_budget_too_small", recommendedMaxChars: 4096 };
    if (result.hasMore) {
      if (!this.catalogSnapshots.has(key) && this.catalogSnapshots.size >= 16)
        this.catalogSnapshots.delete(this.catalogSnapshots.keys().next().value);
      this.catalogSnapshots.set(key, snapshot);
    }
    return result;
  }

  put(body, metadata = {}, lifecycle = {}) {
    if (this.directory && !this.lockDepth) {
      try { return this.withStorageLock(() => this.put(body, metadata, lifecycle)); }
      catch { return { ok: false, errorCode: "archive_lock_unavailable" }; }
    }
    if (typeof body !== "string" || Buffer.byteLength(body) > this.options.maxBytes / 2) return { ok: false, errorCode: "quota" };
    try {
      const sanitized = redact(body);
      const safeMetadata = redact(metadata);
      const id = `ev_${hash([this.scope, this.lineage, body, safeMetadata.callKey])}`;
      const existing = this.load(id);
      if (existing.ok) { this.setPins(new Set([...this.pins, ...(lifecycle.liveRefs || []), id])); return existing; }
      const record = { schemaVersion: 1, scope: this.scope, evidenceId: id,
        lineage: this.lineage,
        createdAt: this.now(), sourceHash: hash(body), archivedBodyHash: hash(sanitized),
        redacted: sanitized !== body, sourceChars: body.length,
        archivedRanges: [[0, sanitized.length]], rangeUnit: "utf16_code_units",
        archiveState: this.directory ? "durable" : "session_only", metadata: safeMetadata, body: sanitized };
      record.recordHash = hash(record);
      const bytes = Buffer.byteLength(JSON.stringify(record));
      const liveRefs = lifecycle.liveRefs instanceof Set ? lifecycle.liveRefs : new Set(lifecycle.liveRefs || []);
      for (const id of this.windowRefs()) liveRefs.add(id);
      for (const id of this.leaseFacts().refs) liveRefs.add(id);
      let entries = this.entries();
      const expiredDead = entries.filter(entry => !liveRefs.has(entry.key)
        && Number.isFinite(entry.record?.createdAt)
        && this.now() - entry.record.createdAt >= this.options.ttlMs);
      for (const entry of expiredDead) this.removeEntry(entry.key);
      const removed = new Set(expiredDead.map(entry => entry.key));
      entries = entries.filter(entry => !removed.has(entry.key));
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
      this.setPins(new Set([...this.pins, ...(lifecycle.liveRefs || []), id]));
      const verified = this.load(id);
      if (verified.ok) this.stats.captured++;
      return verified;
    } catch { return { ok: false, errorCode: "archive_write_failed" }; }
  }

  read(id, version, start = 0, maxChars = 4096, view = "envelope") {
    this.stats.reads++;
    if (!Number.isSafeInteger(start) || start < 0 || !Number.isSafeInteger(maxChars) || maxChars < 1 || maxChars > ARCHIVE_READ_CONTRACT.maximumEnvelopeChars)
      return { ok: false, errorCode: "invalid_range" };
    const loaded = this.load(id);
    if (!loaded.ok) { this.stats.unavailable++; return loaded; }
    const r = loaded.record;
    if (version !== r.archivedBodyHash) return { ok: false, errorCode: "version_mismatch" };
    if (!["envelope", "body"].includes(view)) return { ok: false, errorCode: "invalid_view" };
    let selected = null, bodyFacts = null;
    if (view === "body") {
      const decoded = require("./compaction-tool-memory.js").decodeToolResultRecord(r.body);
      selected = evidenceBodyView(decoded.value);
      bodyFacts = evidenceSemanticFacts(decoded.value);
      if (!selected) return { ok: false, errorCode: "body_view_unavailable", retryView: "envelope", evidenceId: id, version };
    }
    const body = selected ? selected.body : r.body;
    if (start > body.length) return { ok: false, errorCode: "invalid_range" };
    const identity = r.metadata?.sourceIdentity || {};
    const sourceFacts = bodyFacts || r.metadata?.semanticFacts || {};
    const base = { ok: true, kind: "historical_evidence_range", evidenceId: id, version,
      originalCallId: r.metadata?.providerRequestId,
      sourceHash: r.sourceHash, sourceVersion: r.metadata?.sourceVersion,
      sourceIdentity: identity, sourceIdentityDigest: r.metadata?.sourceIdentityDigest || "unknown-source",
      toolName: r.metadata?.toolName, resultStatus: r.metadata?.resultStatus,
      errorCode: r.metadata?.errorCode ?? null,
      sourceRange: r.metadata?.originRanges || (Number.isInteger(sourceFacts.offsetBytes)
        ? [sourceFacts.offsetBytes, sourceFacts.nextOffsetBytes] : undefined),
      sourceRangeUnit: r.metadata?.originRangeUnit || (Number.isInteger(sourceFacts.offsetBytes) ? "utf8_byte" : undefined),
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
      rangeUnit: r.rangeUnit, representation: selected ? "sanitized_body" : "sanitized_archived_tool_envelope",
      ...(selected ? { viewVersion: 1, bodyField: selected.field, readHandle: this.readHandleFor(r), sourceFacts } : {}),
      totalChars: body.length, availableRange: [0, body.length], responseBudgetChars: maxChars };
    const totalBudget = maxChars;
    const overhead = JSON.stringify({ ...base, returnedRange: [start, start], hasMore: true,
      reachedEnd: false, fullRawProvided: false, coverageState: "partial", content: "" }).length;
    const contentBudget = Math.max(0, totalBudget - overhead);
    let end = Math.min(body.length, start + contentBudget);
    const response = () => {
      const reachedEnd = end === body.length;
      const fullRawProvided = !selected && start === 0 && reachedEnd && r.redacted === false;
      return { ...base, returnedRange: [start, end], returnedContentChars: end - start,
        nextOffset: reachedEnd ? null : end,
        archiveHasMore: !reachedEnd, archiveReachedEnd: reachedEnd,
        hasMore: !reachedEnd, reachedEnd,
        fullRawProvided, coverageState: fullRawProvided ? "complete" : "partial",
        ...(selected ? { bodyComplete: start === 0 && reachedEnd } : {}),
        content: body.slice(start, end) };
    };
    let result = response(), serialized = JSON.stringify(result);
    while (serialized.length > totalBudget && end > start) {
      end = Math.max(start, end - Math.max(16, serialized.length - totalBudget + 16));
      result = response();
      serialized = JSON.stringify(result);
    }
    if (serialized.length > totalBudget || end - start < Math.min(ARCHIVE_READ_CONTRACT.minimumBodyChars, body.length - start)) {
      return { ok: false, errorCode: "response_budget_too_small", evidenceId: id,
        minimumBodyChars: Math.min(ARCHIVE_READ_CONTRACT.minimumBodyChars, body.length - start),
        responseBudgetChars: maxChars,
        recommendedMaxChars: Math.min(ARCHIVE_READ_CONTRACT.maximumEnvelopeChars,
          Math.max(ARCHIVE_READ_CONTRACT.defaultEnvelopeChars, serialized.length + ARCHIVE_READ_CONTRACT.minimumBodyChars)),
        currentFile: false, grantsMutation: false };
    }
    this.stats.returnedChars += end - start;
    return result;
  }
}

module.exports = { EvidenceArchive, evidenceBodyView, evidenceSemanticFacts, hash, redact, safeDirectory, readFile, atomicWrite };
