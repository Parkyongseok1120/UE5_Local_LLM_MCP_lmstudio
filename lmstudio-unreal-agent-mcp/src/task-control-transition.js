"use strict";

// Production semantic task transitions are owned by the durable Python task
// server.  This module intentionally contains no transition table.  It is a
// synchronous execution adapter because Agent MCP route authorization and
// result commits already run inside a synchronous state-file transaction.

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => (
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`
    )).join(",")}}`;
  }
  const rendered = JSON.stringify(value);
  return rendered === undefined ? "null" : rendered;
}

function canonicalHash(value) {
  return crypto.createHash("sha256").update(canonicalJson(value)).digest("hex");
}

function scriptsDirectory() {
  const candidates = [
    process.env.UNREAL_MCP_SCRIPTS_DIR,
    path.resolve(__dirname, "../../scripts"),
    process.env.UNREAL_MCP_REPO_ROOT
      ? path.resolve(process.env.UNREAL_MCP_REPO_ROOT, "scripts")
      : "",
  ].filter(Boolean);
  const selected = candidates.find((candidate) => (
    fs.existsSync(path.join(candidate, "control_transition_bridge.py"))
  ));
  if (!selected) {
    const error = new Error("Canonical Python control bridge is unavailable.");
    error.code = "TASK_PYTHON_BRIDGE_FAILED";
    throw error;
  }
  return selected;
}

function invokeCanonicalControl(operation, state = {}, args = {}) {
  const scriptsDir = scriptsDirectory();
  const fallbackPython = process.platform === "win32" ? "python" : "python3";
  const python = String(process.env.PYTHON_EXE || process.env.PYTHON || fallbackPython).trim()
    || fallbackPython;
  const result = spawnSync(
    python,
    [path.join(scriptsDir, "control_transition_bridge.py")],
    {
      cwd: path.resolve(scriptsDir, ".."),
      encoding: "utf8",
      env: {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1",
      },
      windowsHide: true,
      timeout: 120000,
      killSignal: "SIGKILL",
      input: JSON.stringify({ operation, state, arguments: args }),
      maxBuffer: 16 * 1024 * 1024,
    },
  );
  let payload = null;
  try {
    payload = JSON.parse(String(result.stdout || "").trim());
  } catch {
    payload = null;
  }
  if (result.error || result.status !== 0 || payload?.ok !== true) {
    const message = String(
      payload?.error
      || result.error?.message
      || result.stderr
      || "Canonical Python control bridge failed.",
    ).trim().slice(0, 800);
    const error = new Error(message);
    error.code = String(payload?.errorCode || "TASK_PYTHON_BRIDGE_FAILED");
    throw error;
  }
  return payload;
}

function replaceObject(target, source) {
  for (const key of Object.keys(target)) delete target[key];
  Object.assign(target, source);
  return target;
}

function commitControlTransition(state) {
  const payload = invokeCanonicalControl("commit_control_transition", state);
  return replaceObject(state, payload.state || {});
}

function deriveNextObligation(state) {
  return invokeCanonicalControl("derive_next_obligation", state).control || {};
}

function reduceCommittedEvent(state, event = {}) {
  const payload = invokeCanonicalControl("reduce_committed_event", state, { event });
  return replaceObject(state, payload.state || {});
}

function failedGateAttemptForCurrentScope(state, gate) {
  return invokeCanonicalControl(
    "failed_gate_attempt_for_current_scope",
    state,
    { gate },
  ).attempt || {};
}

function mutationToolForState(state, route = {}, hostPlatform = process.platform) {
  return String(invokeCanonicalControl(
    "mutation_tool_for_state",
    state,
    { route, hostPlatform },
  ).tool || "");
}

function preGateSourceReadPath(state, pendingGates = [], hostPlatform = process.platform) {
  return String(invokeCanonicalControl(
    "pre_gate_source_read_path",
    state,
    { pendingGates, hostPlatform },
  ).path || "");
}

function transitionPathIdentity(value, hostPlatform = process.platform) {
  return String(invokeCanonicalControl(
    "transition_path_identity",
    {},
    { value, hostPlatform },
  ).identity || "");
}

function validationFindingRecovery(finding = {}) {
  const value = invokeCanonicalControl(
    "validation_finding_recovery",
    {},
    { finding },
  ).recovery || {};
  return [
    String(value.status || ""),
    String(value.scopeDisposition || ""),
    value.requiredTool && typeof value.requiredTool === "object" ? value.requiredTool : {},
    Array.isArray(value.targetFiles) ? value.targetFiles : [],
  ];
}

function authoritativeProjectFile(state) {
  return String(invokeCanonicalControl("authoritative_project_file", state).path || "");
}

function authoritativeProjectRoot(state) {
  return String(invokeCanonicalControl("authoritative_project_root", state).path || "");
}

function isSourceEvidenceTask(state) {
  return invokeCanonicalControl("is_source_evidence_task", state).value === true;
}

module.exports = {
  canonicalHash,
  canonicalJson,
  commitControlTransition,
  deriveNextObligation,
  reduceCommittedEvent,
  failedGateAttemptForCurrentScope,
  mutationToolForState,
  preGateSourceReadPath,
  transitionPathIdentity,
  validationFindingRecovery,
  authoritativeProjectFile,
  authoritativeProjectRoot,
  isSourceEvidenceTask,
  invokeCanonicalControl,
};
