"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawn } = require("node:child_process");
const { fail } = require("../../shared-tool-core/files");
// One explicit analysis at a time. No build, analyzer, generator or model execution.
class Symbols {
  constructor(policy, bridge, env) { Object.assign(this, { policy, bridge, env, index: null, job: null }); }
  async call(args) {
    if (args.action === "assemblies") return this.bridge.call("unity_compilation_manifest", { action: "list" });
    if (args.action === "index") {
      if (!this.env.UNITY_SYMBOL_WORKER || !this.env.UNITY_DOTNET) fail("worker_not_configured", "Set UNITY_SYMBOL_WORKER (.dll) and UNITY_DOTNET (executable); no runtime is installed automatically");
      if (["preparing", "running"].includes(this.job?.status)) fail("analysis_busy", "One analysis job is permitted");
      // Reserve before the first await: concurrent MCP calls must not start two workers.
      const job = this.job = { jobId: crypto.randomUUID(), status: "preparing" };
      try {
      const exported = await this.bridge.call("unity_compilation_manifest", args);
      if (exported.errorCode) { Object.assign(job, { status: "failed", error: exported.errorCode }); return exported; }
      const file = path.join(this.policy.stateRoot, "analysis-manifest.json");
      if (fs.lstatSync(file).isSymbolicLink() || fs.statSync(file).size > 16 * 1024 * 1024) fail("manifest_invalid", "Unsafe manifest");
      const manifest = JSON.parse(fs.readFileSync(file, "utf8"));
      if (manifest.manifestId !== exported.manifestId || manifest.projectIdentity !== this.policy.projectIdentity) fail("manifest_changed", "Manifest binding changed");
      Object.assign(job, { status: "running", manifestId: manifest.manifestId });
      const child = spawn(this.env.UNITY_DOTNET, [this.env.UNITY_SYMBOL_WORKER], { stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, DOTNET_GCHeapHardLimit: "0x40000000" } });
      let chunks = [], bytes = 0, errors = "", exceeded = false;
      const timer = setTimeout(() => { exceeded = true; child.kill("SIGKILL"); }, 90000);
      child.stdout.on("data", b => { bytes += b.length; if (bytes > 64 * 1024 * 1024) { exceeded = true; child.kill("SIGKILL"); } else chunks.push(b); });
      child.stderr.on("data", b => { errors = (errors + b).slice(0, 2000); });
      child.stdin.on("error", () => {});
      child.on("error", e => { clearTimeout(timer); Object.assign(job, { status: "failed", error: e.message }); });
      child.on("close", code => {
        clearTimeout(timer);
        try {
          if (code !== 0 || exceeded) throw Error(exceeded ? "analysis_resource_limit" : errors || `worker_exit_${code}`);
          const index = JSON.parse(Buffer.concat(chunks));
          if (index.projectIdentity !== this.policy.projectIdentity || index.manifestId !== job.manifestId) throw Error("index_binding_mismatch");
          this.index = index;
          Object.assign(job, { status: "indexed", indexVersion: index.indexVersion, completeWithinScope: index.completeWithinScope });
        } catch (e) { Object.assign(job, { status: "failed", error: e.message.slice(0, 2000) }); }
        chunks = [];
      });
      child.stdin.end(JSON.stringify(manifest));
      return { ...job, status: "accepted", retention: "one index in adapter memory; explicit reindex replaces it" };
      } catch (e) { Object.assign(job, { status: "failed", error: e.message }); throw e; }
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
