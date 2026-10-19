"use strict";
const fs = require("node:fs");
const net = require("node:net");
const crypto = require("node:crypto");
const { fail } = require("../../shared-tool-core/files");
const { identity } = require("./project");
const path = require("node:path");
const BRIDGE_PACKAGE = "com.evidencefirst.unity-bridge";
// Each connection state names what the user can do about it. Only a reload
// is worth retrying unchanged; the others need the Editor or the installer.
const CONNECTION_STEPS = {
  bridge_not_installed: "Run the installer for this project to bind the Bridge package, then open the project in Unity.",
  editor_not_running: "Open this project in the Unity Editor and wait for script compilation to finish. File and Git tools work without the Editor.",
  editor_reloading: "The Editor is reloading scripts. Retry after a few seconds, or call unity_status.",
  editor_disconnected: "Call unity_status: the Editor may be reloading or may have closed.",
};
function connectionFailure(code, message, extra = {}) {
  try { fail(code, message); } catch (error) { throw Object.assign(error, { nextStep: CONNECTION_STEPS[code], ...extra }); }
}
function processAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try { process.kill(pid, 0); return true; } catch (error) { return error.code === "EPERM"; }
}
class BridgeClient {
  constructor(policy) { this.policy = policy; this.lastSession = null; }
  bridgeInstalled() {
    try {
      const manifest = JSON.parse(fs.readFileSync(path.join(this.policy.root, "Packages", "manifest.json"), "utf8").replace(/^\uFEFF/, ""));
      return Boolean(manifest?.dependencies && Object.prototype.hasOwnProperty.call(manifest.dependencies, BRIDGE_PACKAGE));
    } catch { return false; }
  }
  discover() {
    let s;
    try { s = fs.lstatSync(this.policy.discovery); } catch (error) {
      if (error.code !== "ENOENT") throw error;
      if (!this.bridgeInstalled()) connectionFailure("bridge_not_installed", `The Bridge package ${BRIDGE_PACKAGE} is not in Packages/manifest.json`);
      connectionFailure("editor_not_running", "No running Unity Editor has published a Bridge for this project");
    }
    if (s.isSymbolicLink() || s.size > 8192) fail("invalid_discovery", "Invalid discovery file");
    if (process.platform !== "win32" && (s.mode & 0o077)) fail("insecure_discovery", "Discovery must be owner-readable only");
    const d = JSON.parse(fs.readFileSync(this.policy.discovery, "utf8"));
    // A Bridge marks its discovery during a domain reload instead of deleting
    // it; the reload is only real while that Editor process is alive.
    if (d.state === "reloading") {
      if (identity(d.canonicalProjectRoot) !== identity(this.policy.root) || d.projectIdentity !== this.policy.projectIdentity) fail("binding_mismatch", "Project/protocol/process discovery mismatch");
      if (!processAlive(d.processId)) connectionFailure("editor_not_running", "The Editor that was reloading is no longer running");
      connectionFailure("editor_reloading", "The Unity Editor is reloading scripts (domain reload)",
        { status: "not_applied", retryAfterSeconds: 5, reloadStartedAt: typeof d.reloadStartedAt === "string" ? d.reloadStartedAt.slice(0, 40) : undefined });
    }
    if (![1, 2].includes(d.protocolVersion) || identity(d.canonicalProjectRoot) !== identity(this.policy.root) || d.projectIdentity !== this.policy.projectIdentity || !Number.isInteger(d.port) || d.port < 1 || d.port > 65535 || !/^[a-f0-9]{64}$/.test(d.token) || !Number.isInteger(d.processId) || d.processId <= 0) fail("binding_mismatch", "Project/protocol/process discovery mismatch");
    if (!processAlive(d.processId)) connectionFailure("editor_not_running", "The Editor that published this Bridge is no longer running");
    return d;
  }
  async send(d, method, args, { signal } = {}) {
    if (signal?.aborted) fail("request_cancelled", "Request ended before Editor dispatch");
    const requestId = crypto.randomUUID();
    const envelope = { protocolVersion: d.protocolVersion ?? 1, serverVersion: "1.4.0", requestId, token: d.token,
      projectIdentity: d.projectIdentity, canonicalProjectRoot: d.canonicalProjectRoot, editorSessionId: d.editorSessionId,
      domainGeneration: d.domainGeneration, method, args };
    const bytes = Buffer.from(JSON.stringify(envelope) + "\n");
    if (bytes.length > 131072) fail("request_budget_exceeded", "RPC request exceeds 128 KiB");
    return new Promise((resolve, reject) => {
      const socket = net.createConnection({ host: "127.0.0.1", port: d.port });
      let buffer = Buffer.alloc(0), settled = false, submitted = false;
      const finish = (error, result) => {
        if (settled) return;
        settled = true;
        signal?.removeEventListener("abort", cancel);
        socket.destroy();
        error ? reject(error) : resolve(result);
      };
      const cancel = () => finish(Object.assign(new Error(submitted
        ? "Response wait cancelled; the Editor operation may have executed. Query its operationId."
        : "Request ended before Editor dispatch"), {
        code: "request_cancelled", status: submitted ? "outcome_unknown" : "not_applied",
      }));
      signal?.addEventListener("abort", cancel, { once: true });
      if (signal?.aborted) { cancel(); return; }
      const deliveryFailure = (code, message) => Object.assign(new Error(message), {
        code, status: submitted ? "outcome_unknown" : "not_applied",
        ...(CONNECTION_STEPS[code] ? { nextStep: CONNECTION_STEPS[code] } : {}),
      });
      socket.setTimeout(15000, () => finish(deliveryFailure("rpc_timeout", "RPC timed out; verify the operation before retrying")));
      socket.on("connect", () => { if (!settled) { submitted = true; socket.write(bytes); } });
      socket.on("error", () => finish(deliveryFailure("editor_disconnected", "Local Editor connection unavailable")));
      socket.on("close", () => finish(deliveryFailure("editor_disconnected", "Editor connection closed before the result")));
      socket.on("data", chunk => {
        buffer = Buffer.concat([buffer, chunk]);
        if (buffer.length > 65536) return finish(deliveryFailure("response_budget_exceeded", "Bridge response exceeds 64 KiB"));
        const end = buffer.indexOf(10);
        if (end < 0) return;
        try {
          const result = JSON.parse(buffer.subarray(0, end).toString("utf8"));
          // New Bridges preserve the evidence's original identity at the root.
          // Old Bridges still bind the root session to this transport request.
          const delivery = result.delivery || result;
          if (d.protocolVersion === 2 && !result.delivery) throw new Error("Missing RPC delivery identity");
          if (result.requestId !== requestId || delivery.editorSessionId !== d.editorSessionId
            || delivery.domainGeneration !== d.domainGeneration) throw new Error("RPC identity mismatch");
          finish(null, result);
        } catch { finish(deliveryFailure("protocol_error", "Invalid Bridge response")); }
      });
    });
  }
  async call(method, args, context = {}) {
    if (context.signal?.aborted) fail("request_cancelled", "Request ended before Editor dispatch");
    const d = this.discover();
    let handshake;
    try { handshake = await this.send(d, "unity_status", {}, context); }
    catch (error) {
      // Only the read-only handshake was submitted; the requested operation was not.
      error.status = "not_applied";
      throw error;
    }
    if (handshake.status === "error" || handshake.projectIdentity !== this.policy.projectIdentity || handshake.domainGeneration !== d.domainGeneration) fail("binding_mismatch", "Editor handshake changed; request was not submitted");
    this.lastSession = { editorSessionId: d.editorSessionId, domainGeneration: d.domainGeneration };
    return method === "unity_status" ? handshake : this.send(d, method, args, context);
  }
}
module.exports = { BRIDGE_PACKAGE, BridgeClient, CONNECTION_STEPS };
