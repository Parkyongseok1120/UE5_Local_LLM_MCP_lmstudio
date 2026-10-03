"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawn } = require("node:child_process");
const { fail } = require("../../shared-tool-core/files");
// One explicit analysis at a time. No build, analyzer, generator or model execution.
class Symbols {
  constructor(policy, bridge, env, dependencies = {}) {
    Object.assign(this, { policy, bridge, env, index: null, job: null, worker: null, closed: false });
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
        Object.assign(job, { status: "indexed", indexVersion: index.indexVersion, completeWithinScope: index.completeWithinScope });
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
    if (args.action === "status") return { status: "observed", job: this.job, indexVersion: this.index?.indexVersion, configured: Boolean(this.env.UNITY_SYMBOL_WORKER && this.env.UNITY_DOTNET) };
    const index = this.index;
    if (!index || args.indexVersion !== index.indexVersion) fail("index_unavailable", "Use the exact retained indexVersion");
    if (["usages", "relations"].includes(args.action) && !args.symbolId) fail("invalid_arguments", "symbolId is required for a directional semantic query");
    let rows;
    if (args.action === "find") rows = index.symbols.filter(s => (!args.name || s.name === args.name) && (!args.symbolId || s.containingSymbol === args.symbolId));
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
      items: rows.slice(offset, offset + limit), total: rows.length, nextOffset: offset + limit < rows.length ? offset + limit : null, unusedConclusion: "not_inferred" };
  }
}
module.exports = { Symbols };
