"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawn } = require("node:child_process");
const { fail } = require("../../shared-tool-core/files");
const { atomicWriteText } = require("../../shared-tool-core/atomic-io");
const MAX_PERSISTED_INDEX_BYTES = 64 * 1024 * 1024;
// A qualified display without its parameter list: "Game.Player.TakeHit".
const displayBase = value => String(value || "").replace(/\(.*$/s, "");
function nameMatches(symbol, query, mode) {
  const candidates = [String(symbol.name || ""), displayBase(symbol.display)];
  if (mode === "prefix") return candidates.some(value => value.startsWith(query));
  if (mode === "contains") return candidates.some(value => value.includes(query));
  return candidates.some(value => value === query);
}
// One explicit analysis at a time. No build, analyzer, generator or model execution.
class Symbols {
  constructor(policy, bridge, env, dependencies = {}) {
    Object.assign(this, { policy, bridge, env, index: null, indexSource: null, job: null, worker: null, closed: false });
    this.spawn = dependencies.spawn || spawn;
    this.shutdownMs = dependencies.shutdownMs ?? 5000;
  }
  close(reason = "connection_closed") {
    this.closed = true;
    this.worker?.cancel(reason);
    if (this.job?.status === "preparing") Object.assign(this.job, { status: "cancelled", reason });
  }
  assertActive(signal) {
    if (this.closed || signal?.aborted) fail("request_cancelled", "Symbol request ended before dispatch");
  }
  startWorker(manifest, job, signal) {
    this.assertActive(signal);
    const child = this.spawn(this.env.UNITY_DOTNET, [this.env.UNITY_SYMBOL_WORKER], {
      stdio: ["pipe", "pipe", "pipe"], windowsHide: true,
      env: { ...process.env, DOTNET_GCHeapHardLimit: "0x40000000" },
    });
    let chunks = [], bytes = 0, errors = "", stopped = false, cancelReason = "", shutdown;
    const worker = { child, cancel: reason => {
      if (stopped || cancelReason) return;
      cancelReason = reason;
      clearTimeout(timer);
      chunks = [];
      Object.assign(job, { status: "cancel_requested", reason, terminationConfirmed: false });
      // Install the deadline before requesting termination. A missing close must
      // keep the reservation, and cannot publish a late successful index.
      shutdown = setTimeout(() => {
        if (!stopped) {
          Object.assign(job, { status: "outcome_unknown", terminationConfirmed: false });
          // Retain the reservation while alive, but do not let a failed kill
          // keep an otherwise closed adapter process running indefinitely.
          child.unref?.();
          for (const stream of [child.stdin, child.stdout, child.stderr]) stream?.unref?.();
        }
      }, this.shutdownMs);
      shutdown.unref?.();
      try { child.kill("SIGKILL"); } catch (e) { job.terminationError = String(e.message).slice(0, 1000); }
    } };
    this.worker = worker;
    const abort = () => worker.cancel("request_cancelled");
    const timer = setTimeout(() => worker.cancel("analysis_resource_limit"), 90000);
    const finish = (code, spawnError) => {
      if (stopped) return;
      stopped = true;
      clearTimeout(timer); clearTimeout(shutdown);
      signal?.removeEventListener("abort", abort);
      if (this.worker === worker) this.worker = null;
      try {
        if (cancelReason) {
          Object.assign(job, { status: cancelReason === "analysis_resource_limit" ? "failed" : "cancelled",
            terminationConfirmed: true, error: cancelReason });
          return;
        }
        if (spawnError || code !== 0) throw Error(spawnError || errors || `worker_exit_${code}`);
        const index = JSON.parse(Buffer.concat(chunks));
        if (index.projectIdentity !== this.policy.projectIdentity || index.manifestId !== job.manifestId)
          throw Error("index_binding_mismatch");
        this.index = index;
        this.indexSource = "worker";
        Object.assign(job, { status: "indexed", indexVersion: index.indexVersion, completeWithinScope: index.completeWithinScope,
          persistence: this.persist(index) });
      } catch (e) { Object.assign(job, { status: "failed", error: e.message.slice(0, 2000) }); }
      finally { chunks = []; }
    };
    child.stdout.on("data", b => {
      if (stopped || cancelReason) return;
      bytes += b.length;
      if (bytes > 64 * 1024 * 1024) worker.cancel("analysis_resource_limit"); else chunks.push(b);
    });
    child.stderr.on("data", b => { errors = (errors + b).slice(0, 2000); });
    child.stdin.on("error", () => {});
    child.on("error", e => {
      if (!child.pid) finish(null, e.message);
      else { job.processError = String(e.message).slice(0, 1000); worker.cancel("worker_error"); }
    });
    child.on("close", code => finish(code));
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted || this.closed) abort();
    else child.stdin.end(JSON.stringify(manifest));
  }
  indexFile() { return this.policy.stateRoot ? path.join(this.policy.stateRoot, "symbol-index", "latest.json") : null; }
  // The last completed index is kept beside the Bridge state so queries keep
  // working without an Editor. Persistence is best effort and never blocks.
  persist(index) {
    const file = this.indexFile();
    if (!file) return "unavailable";
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      if (fs.lstatSync(path.dirname(file)).isSymbolicLink()) return "unavailable";
      atomicWriteText(file, JSON.stringify(index));
      return "persisted";
    } catch { return "unavailable"; }
  }
  loadPersisted() {
    const file = this.indexFile();
    if (!file) return null;
    try {
      const stat = fs.lstatSync(file);
      if (stat.isSymbolicLink() || stat.size > MAX_PERSISTED_INDEX_BYTES) return null;
      const index = JSON.parse(fs.readFileSync(file, "utf8"));
      if (index?.projectIdentity !== this.policy.projectIdentity || typeof index.indexVersion !== "string") return null;
      this.index = index;
      this.indexSource = "persisted";
      return index;
    } catch { return null; }
  }
  // A declaration's readable location: Assets paths and embedded packages are
  // project paths; other package sources are package:// URIs. The physical
  // path the worker saw is never returned.
  declarationUri(declaration) {
    const at = String(declaration?.path || "");
    let uri;
    if (at.startsWith("Assets/")) uri = at;
    if (at.startsWith("Packages/")) {
      const [, name, ...rest] = at.split("/");
      if (!name || !rest.length) return { uri: null, uriReason: "package path without a file" };
      uri = this.policy.root && fs.existsSync(path.join(this.policy.root, "Packages", name)) ? at : `package://${name}/${rest.join("/")}`;
    }
    if (uri) {
      try { this.policy.resolve(uri, false); return { uri }; }
      catch (error) { return { uri: null, uriReason: error.code || "source_unavailable" }; }
    }
    return { uri: null, uriReason: "not a project or package source path" };
  }
  // Whether the file still has the bytes the index was built from.
  currentHash(uri, cache) {
    if (cache.has(uri)) return cache.get(uri);
    let digest = null;
    try { digest = crypto.createHash("sha256").update(fs.readFileSync(this.policy.resolve(uri, false))).digest("hex"); } catch { digest = null; }
    cache.set(uri, digest);
    return digest;
  }
  locatedDeclarations(symbol, cache) {
    return (symbol.declarations || []).map(declaration => {
      const located = { ...declaration, ...this.declarationUri(declaration) };
      if (located.uri && declaration.sourceHash && this.policy.resolve) {
        const current = this.currentHash(located.uri, cache);
        located.stale = current === null ? "unknown" : current !== declaration.sourceHash;
      }
      delete located.path;
      return located;
    });
  }
  // The declaration to read for one symbol, or why there is none.
  locate(args) {
    const index = this.index || this.loadPersisted();
    if (!index || args.indexVersion !== index.indexVersion) fail("index_unavailable", "Use the exact retained indexVersion");
    if (!args.symbolId) fail("invalid_arguments", "symbolId is required for source");
    const symbol = index.symbols.find(item => item.id === args.symbolId);
    if (!symbol) fail("symbol_not_in_index", "The symbolId is not in this index; find it first");
    const declaration = this.locatedDeclarations(symbol, new Map()).find(item => item.uri);
    if (!declaration) fail("source_unavailable", "This symbol has no readable source declaration (compiled assembly, built-in package or generated code)");
    return { symbol: { id: symbol.id, name: symbol.name, display: symbol.display, kind: symbol.kind },
      uri: declaration.uri, line: declaration.line, sourceHash: declaration.sourceHash, stale: declaration.stale,
      indexVersion: index.indexVersion, indexSource: this.indexSource };
  }
  async call(args, { signal } = {}) {
    this.assertActive(signal);
    if (args.action === "assemblies") return this.bridge.call("unity_compilation_manifest", { action: "list" }, { signal });
    if (args.action === "index") {
      if (!this.env.UNITY_SYMBOL_WORKER || !this.env.UNITY_DOTNET) fail("worker_not_configured", "Set UNITY_SYMBOL_WORKER (.dll) and UNITY_DOTNET (executable); no runtime is installed automatically");
      if (this.worker || this.job?.status === "preparing") fail("analysis_busy", "One analysis job is permitted");
      // Reserve before the first await: concurrent MCP calls must not start two workers.
      const job = this.job = { jobId: crypto.randomUUID(), status: "preparing" };
      try {
        const exported = await this.bridge.call("unity_compilation_manifest", args, { signal });
        this.assertActive(signal);
        if (exported.errorCode) { Object.assign(job, { status: "failed", error: exported.errorCode }); return exported; }
        const file = path.join(this.policy.stateRoot, "analysis-manifest.json");
        if (fs.lstatSync(file).isSymbolicLink() || fs.statSync(file).size > 16 * 1024 * 1024) fail("manifest_invalid", "Unsafe manifest");
        const manifest = JSON.parse(fs.readFileSync(file, "utf8"));
        if (manifest.manifestId !== exported.manifestId || manifest.projectIdentity !== this.policy.projectIdentity) fail("manifest_changed", "Manifest binding changed");
        Object.assign(job, { status: "running", manifestId: manifest.manifestId });
        this.startWorker(manifest, job, signal);
        return { ...job, status: "accepted", retention: "one index in adapter memory; explicit reindex replaces it" };
      } catch (e) { Object.assign(job, { status: e.code === "request_cancelled" ? "cancelled" : "failed", error: e.message }); throw e; }
    }
    if (args.action === "status") {
      const index = this.index || this.loadPersisted();
      return { status: "observed", job: this.job, indexVersion: index?.indexVersion, indexSource: this.indexSource,
        configured: Boolean(this.env.UNITY_SYMBOL_WORKER && this.env.UNITY_DOTNET),
        ...(index ? {} : { nextStep: "Index needs a running Editor: call unity_symbols index with assemblies from the assemblies action." }) };
    }
    const index = this.index || this.loadPersisted();
    if (!index || args.indexVersion !== index.indexVersion) fail("index_unavailable", "Use the exact retained indexVersion");
    if (["usages", "relations"].includes(args.action) && !args.symbolId) fail("invalid_arguments", "symbolId is required for a directional semantic query");
    let rows;
    if (args.action === "find") rows = index.symbols.filter(s => (!args.name || nameMatches(s, args.name, args.match || "exact")) && (!args.symbolId || s.containingSymbol === args.symbolId));
    else if (args.action === "usages") rows = index.uses.filter(s => s[args.direction === "forward" ? "from" : "to"] === args.symbolId);
    else if (args.action === "relations") rows = index.relations.filter(s => s[args.direction === "reverse" ? "to" : "from"] === args.symbolId);
    else if (args.action === "diagnostics") rows = index.diagnostics;
    else if (args.action === "omissions") rows = index.omissions;
    else if (args.action === "versions") rows = index.versions;
    else fail("invalid_action", "Unknown symbol action");
    const offset = args.offset ?? 0, limit = args.limit ?? 50;
    return { status: "observed", indexVersion: index.indexVersion, manifestId: index.manifestId, compilationId: index.compilationId,
      editorSessionId: index.editorSessionId, domainGeneration: index.domainGeneration, observedAt: index.observedAt, scope: index.scope,
      completeWithinScope: index.completeWithinScope, freshness: "immutable_index_current_sources_not_rechecked", compilerVersion: index.compilerVersion,
      omissionCount: index.omissions.length, diagnosticCount: index.diagnostics.length,
      referenceCoverage: index.referenceCoverage,
      // An empty or partial answer describes only this index; it never proves a symbol is absent.
      absenceVerified: false, searchedAssemblies: Array.isArray(index.scope) ? index.scope : [],
      ...(args.action === "find" ? { limitations: [
        "Only the assemblies selected for this index are searched; unselected assemblies and engine/package DLL symbols are not find targets.",
        "A name not found here is not proven absent from the project.",
        `Matched with ${args.match || "exact"} against the simple name and the qualified name.`,
      ] } : {}),
      indexSource: this.indexSource,
      ...(this.indexSource === "persisted" ? { persistedIndexNote: "Loaded from the last completed index; declarations marked stale changed since." } : {}),
      items: this.present(args, rows.slice(offset, offset + limit)), total: rows.length,
      nextOffset: offset + limit < rows.length ? offset + limit : null, unusedConclusion: "not_inferred" };
  }
  present(args, rows) {
    const cache = new Map();
    return rows.map(row => ({ ...row,
      ...(row.declarations ? { declarations: this.locatedDeclarations(row, cache) } : {}),
      ...(row.location ? { location: this.locatedDeclarations({ declarations: [row.location] }, cache)[0] } : {}),
      ...(row.path ? this.declarationUri(row) : {}) }));
  }
}
module.exports = { Symbols };
