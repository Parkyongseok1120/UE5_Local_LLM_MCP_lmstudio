"use strict";
const fs = require("node:fs");
const net = require("node:net");
const crypto = require("node:crypto");
const { fail } = require("../../shared-tool-core/files");
const { identity } = require("./project");
class BridgeClient {
  constructor(policy) { this.policy = policy; this.lastSession = null; }
  discover() {
    const s = fs.lstatSync(this.policy.discovery);
    if (s.isSymbolicLink() || s.size > 8192) fail("invalid_discovery", "Invalid discovery file");
    if (process.platform !== "win32" && (s.mode & 0o077)) fail("insecure_discovery", "Discovery must be owner-readable only");
    const d = JSON.parse(fs.readFileSync(this.policy.discovery, "utf8"));
    if (d.protocolVersion !== 1 || identity(d.canonicalProjectRoot) !== identity(this.policy.root) || d.projectIdentity !== this.policy.projectIdentity || !Number.isInteger(d.port) || d.port < 1 || d.port > 65535 || !/^[a-f0-9]{64}$/.test(d.token) || !Number.isInteger(d.processId) || d.processId <= 0) fail("binding_mismatch", "Project/protocol/process discovery mismatch");
    try { process.kill(d.processId, 0); } catch { fail("editor_disconnected", "Discovered Editor process is not available locally"); }
    return d;
  }
  async send(d, method, args) {
    const requestId = crypto.randomUUID();
    const envelope = { protocolVersion: 1, serverVersion: "1.4.0-beta.4", requestId, token: d.token,
      projectIdentity: d.projectIdentity, canonicalProjectRoot: d.canonicalProjectRoot, editorSessionId: d.editorSessionId,
      domainGeneration: d.domainGeneration, method, args };
    const bytes = Buffer.from(JSON.stringify(envelope) + "\n");
    if (bytes.length > 131072) fail("request_budget_exceeded", "RPC request exceeds 128 KiB");
    return new Promise((resolve, reject) => {
      const socket = net.createConnection({ host: "127.0.0.1", port: d.port });
      let buffer = Buffer.alloc(0), settled = false;
      const finish = (error, result) => { if (settled) return; settled = true; socket.destroy(); error ? reject(error) : resolve(result); };
      socket.setTimeout(15000, () => finish(Object.assign(new Error("RPC timed out; query operationId before deciding what to do"), { code: "rpc_timeout", status: args.operationId ? "outcome_unknown" : "not_applied" })));
      socket.on("connect", () => socket.write(bytes));
      socket.on("error", () => finish(Object.assign(new Error("Local Editor connection unavailable"), { code: "editor_disconnected", status: args.operationId ? "outcome_unknown" : "not_applied" })));
      socket.on("close", () => finish(Object.assign(new Error("Editor connection closed before the result"), { code: "editor_disconnected", status: args.operationId ? "outcome_unknown" : "not_applied" })));
      socket.on("data", chunk => {
        buffer = Buffer.concat([buffer, chunk]);
        if (buffer.length > 65536) return finish(Object.assign(new Error("Bridge response exceeds 64 KiB"), { code: "response_budget_exceeded", status: args.operationId ? "outcome_unknown" : "not_applied" }));
        const end = buffer.indexOf(10);
        if (end < 0) return;
        try {
          const result = JSON.parse(buffer.subarray(0, end).toString("utf8"));
          if (result.requestId !== requestId || result.editorSessionId !== d.editorSessionId) throw new Error("RPC identity mismatch");
          finish(null, result);
        } catch { finish(Object.assign(new Error("Invalid Bridge response"), { code: "protocol_error", status: args.operationId ? "outcome_unknown" : "not_applied" })); }
      });
    });
  }
  async call(method, args) {
    const d = this.discover();
    const handshake = await this.send(d, "unity_status", {});
    if (handshake.status === "error" || handshake.projectIdentity !== this.policy.projectIdentity || handshake.domainGeneration !== d.domainGeneration) fail("binding_mismatch", "Editor handshake changed; request was not submitted");
    this.lastSession = { editorSessionId: d.editorSessionId, domainGeneration: d.domainGeneration };
    return method === "unity_status" ? handshake : this.send(d, method, args);
  }
}
module.exports = { BridgeClient };
