"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createListDirectoryBudget } = require("../src/list-directory-budget.js");

test("list_directory budget blocks duplicates and window overuse, not path depth", () => {
  const budget = createListDirectoryBudget({
    windowMs: 60_000,
    maxCallsPerWindow: 3,
    maxCallsPerPath: 1,
  });
  const deep = "Source/Project_MJS/Public/System/Combat";
  assert.equal(budget.check("proj", deep).ok, true);
  budget.commit("proj", deep);
  const dup = budget.check("proj", deep);
  assert.equal(dup.ok, false);
  assert.equal(dup.errorCode, "LIST_DIRECTORY_DUPLICATE");

  assert.equal(budget.check("proj", "Source").ok, true);
  budget.commit("proj", "Source");
  assert.equal(budget.check("proj", "Plugins").ok, true);
  budget.commit("proj", "Plugins");
  const over = budget.check("proj", "Config");
  assert.equal(over.ok, false);
  assert.equal(over.errorCode, "LIST_DIRECTORY_BUDGET_EXCEEDED");

  // #region agent log
  try {
    const debugLog = process.env.LMS_CONTEXT_COMPACTOR_DEBUG_LOG
      || path.join(__dirname, "..", "..", "debug-49b048.log");
    fs.appendFileSync(
      debugLog,
      `${JSON.stringify({
        sessionId: "49b048",
        runId: "release-harden",
        hypothesisId: "H-LIST",
        location: "list-directory-budget.test",
        message: "budget blocks duplicates not depth",
        data: {
          deepAllowedOnce: true,
          duplicateBlocked: dup.errorCode,
          windowBlocked: over.errorCode,
        },
        timestamp: Date.now(),
      })}\n`,
    );
  } catch {
    /* ignore */
  }
  // #endregion
});
