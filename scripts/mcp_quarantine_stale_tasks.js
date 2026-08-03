"use strict";
/**
 * Quarantine stale "running" tasks in shared ~/.lmstudio/state/unreal-agent.
 * Does not touch O-Mock game code. Backs up each state.json before marking cancelled.
 */
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const STATE_ROOT = path.join(os.homedir(), ".lmstudio", "state", "unreal-agent");
const TASKS = path.join(STATE_ROOT, "tasks");
const QUARANTINE = path.join(STATE_ROOT, "quarantine");
const DEBUG = path.join(__dirname, "..", "debug-821b0f.log");
const REPORT = path.join(__dirname, "mcp_stale_task_quarantine_report.json");

function log(message, data, hypothesisId = "H1") {
  fs.appendFileSync(
    DEBUG,
    JSON.stringify({
      sessionId: "821b0f",
      runId: "mcp-quarantine",
      hypothesisId,
      location: "mcp_quarantine_stale_tasks.js",
      message,
      data,
      timestamp: Date.now(),
    }) + "\n",
  );
  console.log(message, JSON.stringify(data));
}

function main() {
  if (!fs.existsSync(TASKS)) {
    throw new Error(`tasks dir missing: ${TASKS}`);
  }
  fs.mkdirSync(QUARANTINE, { recursive: true });

  const ids = fs.readdirSync(TASKS);
  const cancelled = [];
  const skipped = [];
  const errors = [];

  for (const id of ids) {
    const statePath = path.join(TASKS, id, "state.json");
    if (!fs.existsSync(statePath)) continue;
    let state;
    try {
      state = JSON.parse(fs.readFileSync(statePath, "utf8"));
    } catch (e) {
      errors.push({ id, error: String(e.message || e) });
      continue;
    }
    if (String(state.status || "") !== "running") {
      skipped.push({ id, status: state.status });
      continue;
    }

    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const backupDir = path.join(QUARANTINE, `${id}_${stamp}`);
    fs.mkdirSync(backupDir, { recursive: true });
    fs.copyFileSync(statePath, path.join(backupDir, "state.json"));
    try {
      const route = path.join(TASKS, id, "route-scope.json");
      if (fs.existsSync(route)) fs.copyFileSync(route, path.join(backupDir, "route-scope.json"));
    } catch {
      /* ignore */
    }

    state.status = "cancelled";
    state.cancelledAt = new Date().toISOString();
    state.cancelReason = "midpoint_audit_stale_running_quarantine_821b0f";
    state.forceCancelled = true;
    fs.writeFileSync(statePath, JSON.stringify(state, null, 2));
    cancelled.push({
      id,
      updated: state.updatedAt || null,
      project: state.projectFile || "",
      requestPreview: String(state.request || "").slice(0, 100),
    });
  }

  const report = {
    stateRoot: STATE_ROOT,
    cancelledCount: cancelled.length,
    skippedCount: skipped.length,
    errorCount: errors.length,
    cancelled,
    errors,
  };
  fs.writeFileSync(REPORT, JSON.stringify(report, null, 2));
  // #region agent log
  log(
    "quarantine_done",
    {
      cancelledCount: cancelled.length,
      sampleIds: cancelled.slice(0, 5).map((c) => c.id),
      hadStage2Blocker: cancelled.some((c) => c.id === "43baa300ded2456a"),
    },
    "H1",
  );
  // #endregion
  console.log(REPORT);
}

main();
