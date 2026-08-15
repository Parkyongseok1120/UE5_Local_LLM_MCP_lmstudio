#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const { resolveAgentStateRoot } = require("./state-root.js");
const { resolveRecoveryRequiredJournal } = require("./transaction-journal.js");

async function main() {
  const workspaceRoot = path.resolve(String(process.argv[2] || process.cwd()));
  const raw = fs.readFileSync(0, "utf8");
  const payload = raw.trim() ? JSON.parse(raw) : {};
  const result = await resolveRecoveryRequiredJournal(
    {
      transactionId: String(payload.transactionId || ""),
      taskSessionId: String(payload.taskSessionId || ""),
      resolution: payload.resolution && typeof payload.resolution === "object"
        ? payload.resolution
        : {},
    },
    resolveAgentStateRoot(workspaceRoot)
  );
  process.stdout.write(`${JSON.stringify(result)}\n`);
  if (result.ok !== true) process.exitCode = 1;
}

main().catch((error) => {
  process.stderr.write(`${String(error.stack || error)}\n`);
  process.exitCode = 1;
});
