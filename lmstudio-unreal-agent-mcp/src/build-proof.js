const EXECUTOR_SETUP_PATTERNS = [
  /Executing up to \d+ processes, one per physical core/i,
  /Building \d+ actions? with \d+ processes?/i,
];

const COMPILE_ACTION_PATTERN = /\[(\d+)\/(\d+)\]\s+Compile\b/i;
const LINK_ACTION_PATTERN = /\[(\d+)\/(\d+)\]\s+Link\b/i;
const RUN_ACTIONS_PATTERN = /run\s+(\d+)\s+action\(s\)/i;
const BUILDING_ACTIONS_PATTERN = /Building\s+(\d+)\s+action\(s\)/i;
const UP_TO_DATE_PATTERN = /Target is up to date|run\s+0\s+action\(s\)/i;

function maxActionTotal(pattern, text) {
  let total = 0;
  for (const match of text.matchAll(new RegExp(pattern, "gi"))) {
    const value = Number(match[2] || 0);
    if (Number.isFinite(value)) {
      total = Math.max(total, value);
    }
  }
  return total;
}

function parseBuildProof(ok, output, { logPath = "" } = {}) {
  const text = String(output || "");
  const compileActionCount = maxActionTotal(COMPILE_ACTION_PATTERN, text);
  const linkActionCount = maxActionTotal(LINK_ACTION_PATTERN, text);
  const runActionsMatch = text.match(RUN_ACTIONS_PATTERN);
  const buildingActionsMatch = text.match(BUILDING_ACTIONS_PATTERN);
  const summaryActionCount = Math.max(
    runActionsMatch ? Number(runActionsMatch[1] || 0) : 0,
    buildingActionsMatch ? Number(buildingActionsMatch[1] || 0) : 0,
  );
  const actionCount = Math.max(compileActionCount + linkActionCount, summaryActionCount);
  const targetUpToDate = Boolean(ok && UP_TO_DATE_PATTERN.test(text));
  const executorOnly = EXECUTOR_SETUP_PATTERNS.some((pattern) => pattern.test(text));

  let proofLevel;
  if (!ok) {
    proofLevel = "Failed";
  } else if (actionCount > 0 && !executorOnly) {
    proofLevel = "Built";
  } else if (targetUpToDate) {
    proofLevel = "BuiltStale";
  } else {
    proofLevel = "BuiltUnverified";
  }

  return {
    ok: Boolean(ok),
    targetUpToDate,
    actionCount,
    compileActionCount,
    linkActionCount,
    proofLevel,
    logPath,
  };
}

function proofLevelFromBuildOutput(ok, output) {
  return parseBuildProof(ok, output).proofLevel;
}

module.exports = {
  parseBuildProof,
  proofLevelFromBuildOutput,
};
