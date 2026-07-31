"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const { resolveAgentStateRoot, ensureStateRootLayout } = require("./state-root");
const {
  tryAcquireCrossProcessLock,
  releaseCrossProcessLock,
} = require("./write-locks");

const ID_RE = /^[A-Za-z0-9_.:-]{8,128}$/;

function validId(value) {
  return Boolean(value && ID_RE.test(String(value)));
}

function sleepMs(ms) {
  const sab = new SharedArrayBuffer(4);
  const view = new Int32Array(sab);
  Atomics.wait(view, 0, 0, Math.max(1, ms));
}

function pidAlive(pid) {
  if (!Number.isFinite(pid) || pid <= 0) return "dead";
  try {
    process.kill(pid, 0);
    return "alive";
  } catch (error) {
    if (error && (error.code === "ESRCH" || error.errno === -3)) return "dead";
    return "unknown";
  }
}

function parentPid(pid) {
  if (!Number.isFinite(pid) || pid <= 0) return 0;
  if (process.platform !== "win32") {
    try {
      const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8").split(/\s+/);
      return Number(stat[3] || 0) || 0;
    } catch {
      return 0;
    }
  }
  const result = spawnSync(
    "powershell",
    [
      "-NoProfile",
      "-Command",
      `$p = Get-CimInstance Win32_Process -Filter "ProcessId=${pid}"; if ($null -eq $p) { exit 1 }; Write-Output $p.ParentProcessId`,
    ],
    { encoding: "utf8", windowsHide: true, timeout: 5000 }
  );
  if (result.status !== 0) return 0;
  return Number(String(result.stdout || "").trim() || 0) || 0;
}

function processName(pid) {
  if (!Number.isFinite(pid) || pid <= 0) return "";
  if (process.platform !== "win32") {
    try {
      return String(fs.readFileSync(`/proc/${pid}/comm`, "utf8") || "").trim();
    } catch {
      return "";
    }
  }
  const result = spawnSync(
    "powershell",
    [
      "-NoProfile",
      "-Command",
      `$p = Get-CimInstance Win32_Process -Filter "ProcessId=${pid}"; if ($null -eq $p) { exit 1 }; Write-Output $p.Name`,
    ],
    { encoding: "utf8", windowsHide: true, timeout: 5000 }
  );
  if (result.status !== 0) return "";
  return String(result.stdout || "").trim();
}

function resolveMcpHostPid() {
  const raw = String(process.env.MCP_HOST_PID || "").trim();
  if (/^\d+$/.test(raw)) return Number(raw);
  const markers = ["lm studio", "lmstudio", "cline", "cursor", "code"];
  let pid = process.pid;
  const seen = new Set();
  for (let i = 0; i < 8; i += 1) {
    if (seen.has(pid) || pid <= 0) break;
    seen.add(pid);
    const parent = parentPid(pid);
    if (parent <= 0 || seen.has(parent)) break;
    const name = processName(parent).toLowerCase();
    if (markers.some((marker) => name.includes(marker))) return parent;
    pid = parent;
  }
  return process.ppid > 0 ? process.ppid : process.pid;
}

function atomicWriteJson(filePath, payload) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temp = `${filePath}.tmp-${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`;
  fs.writeFileSync(temp, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  fs.renameSync(temp, filePath);
}

function processStartedAt(pid) {
  if (!Number.isFinite(pid) || pid <= 0) return "";
  if (process.platform === "win32") {
    const result = spawnSync(
      "powershell",
      [
        "-NoProfile",
        "-Command",
        `$p = Get-CimInstance Win32_Process -Filter "ProcessId=${pid}"; if ($null -eq $p) { exit 1 }; Write-Output ($p.CreationDate.ToUniversalTime().ToString('o'))`,
      ],
      { encoding: "utf8", windowsHide: true, timeout: 5000 }
    );
    if (result.status !== 0) return "";
    return String(result.stdout || "").trim();
  }
  try {
    if (fs.existsSync(`/proc/${pid}/stat`)) {
      const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
      const close = stat.lastIndexOf(")");
      const fields = (close >= 0 ? stat.slice(close + 1) : stat).trim().split(/\s+/);
      const startTicks = Number(fields[19] || 0);
      const uptimeSec = Number(String(fs.readFileSync("/proc/uptime", "utf8")).split(/\s+/)[0] || 0);
      const hz = 100;
      const boot = Date.now() / 1000 - uptimeSec;
      return new Date((boot + startTicks / hz) * 1000).toISOString();
    }
  } catch {
    // fall through
  }
  const result = spawnSync("ps", ["-o", "lstart=", "-p", String(pid)], {
    encoding: "utf8",
    timeout: 5000,
  });
  if (result.status !== 0) return "";
  return String(result.stdout || "").trim();
}

function hostIdentityMatches(payload, hostPid) {
  if (Number(payload.hostPid || 0) !== hostPid) return false;
  const currentStarted = processStartedAt(hostPid);
  const payloadStarted = String(payload.hostStartedAt || "").trim();
  if (currentStarted && payloadStarted && currentStarted !== payloadStarted) return false;
  const currentExe = processName(hostPid);
  const payloadExe = String(payload.hostExecutable || "").trim();
  if (currentExe && payloadExe && currentExe.toLowerCase() !== payloadExe.toLowerCase()) {
    return false;
  }
  return true;
}

function resolveOrCreateBootInstanceId() {
  const explicit = String(process.env.MCP_CLIENT_INSTANCE_ID || "").trim();
  if (validId(explicit)) return explicit;
  let root = "";
  try {
    root = ensureStateRootLayout(resolveAgentStateRoot());
  } catch {
    return `mcp-boot-local-${process.pid}-${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
  }
  const hostPid = resolveMcpHostPid();
  const hostPidExplicit = /^\d+$/.test(String(process.env.MCP_HOST_PID || "").trim());
  const filePath = path.join(root, "runtime", `boot-${hostPid}.json`);
  const currentStarted = processStartedAt(hostPid);
  const currentExe = processName(hostPid);
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const acquired = tryAcquireCrossProcessLock(filePath, "mcp_boot_instance", root);
    if (!acquired.ok) {
      sleepMs(50);
      continue;
    }
    try {
      if (fs.existsSync(filePath)) {
        try {
          const payload = JSON.parse(fs.readFileSync(filePath, "utf8"));
          const existing = String(payload.clientInstanceId || "").trim();
          const alive = pidAlive(hostPid);
          const reusable = (
            validId(existing)
            && hostIdentityMatches(payload, hostPid)
            && (hostPidExplicit || alive === "alive")
          );
          if (reusable) {
            payload.renewedAt = new Date().toISOString();
            payload.expiresAt = new Date(Date.now() + 12 * 3600 * 1000).toISOString();
            if (currentStarted && !String(payload.hostStartedAt || "").trim()) {
              payload.hostStartedAt = currentStarted;
            }
            if (currentExe && !String(payload.hostExecutable || "").trim()) {
              payload.hostExecutable = currentExe;
            }
            atomicWriteJson(filePath, payload);
            return existing;
          }
        } catch {
          // recreate below
        }
      }
      const value = `mcp-boot-${hostPid}-${crypto.randomUUID().replace(/-/g, "").slice(0, 16)}`;
      const payload = {
        clientInstanceId: value,
        hostPid,
        hostStartedAt: currentStarted,
        hostExecutable: currentExe,
        createdAt: new Date().toISOString(),
        renewedAt: new Date().toISOString(),
        expiresAt: new Date(Date.now() + 12 * 3600 * 1000).toISOString(),
      };
      atomicWriteJson(filePath, payload);
      const final = JSON.parse(fs.readFileSync(filePath, "utf8"));
      const resolved = String(final.clientInstanceId || "").trim();
      return validId(resolved) ? resolved : value;
    } finally {
      releaseCrossProcessLock(filePath);
    }
  }
  return `mcp-boot-local-${process.pid}-${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
}

module.exports = {
  resolveMcpHostPid,
  resolveOrCreateBootInstanceId,
  validId,
};
