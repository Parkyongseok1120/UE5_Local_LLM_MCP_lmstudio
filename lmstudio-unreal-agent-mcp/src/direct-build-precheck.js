"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { discoverAutomationTests } = require("./automation-source-discovery");

// Static findings UBT/UHT reject on every engine version: a missing include
// file, or a broken .generated.h contract. Heuristic or version-dependent
// rules stay advisory and never skip a build.
const DEFINITE_CODES = new Set(["INCLUDE_PATH_NOT_FOUND", "UHT_MACRO_IN_CONDITIONAL_BLOCK"]);
const DEFINITE_PREFIXES = ["GENERATED_H_"];
const MAX_PRECHECK_TARGETS = 64;
const MAX_PRECHECK_FINDINGS = 8;
const MAX_RELATED_TESTS = 10;

function isDefiniteStaticError(finding) {
  const code = String(finding?.code || "");
  return finding?.severity === "error" && finding.blocking !== false
    && (DEFINITE_CODES.has(code) || DEFINITE_PREFIXES.some((prefix) => code.startsWith(prefix)));
}

function compactFinding(finding) {
  return {
    severity: finding.severity,
    code: finding.code,
    path: finding.path,
    line: finding.line,
    message: String(finding.message || "").slice(0, 240),
    definite: isDefiniteStaticError(finding),
  };
}

// Changed Source files that still exist; deleted ones have nothing to scan.
function existingTargets(projectRoot, paths) {
  return paths.filter((relative) => {
    try { return fs.statSync(path.join(projectRoot, relative)).isFile(); } catch { return false; }
  });
}

/**
 * Static validation of the Source files changed since the last successful
 * build. Returns the response block, the definite errors, and every finding
 * (for the log when the response is trimmed). An unavailable validator never
 * skips a build.
 */
async function runBuildStaticPrecheck({ projectRoot, pending, runValidation, validationOptions }) {
  const targets = existingTargets(projectRoot, pending.paths);
  const scope = {
    scopeTargets: targets.slice(0, MAX_PRECHECK_TARGETS),
    scopeComplete: !pending.overflowed && targets.length <= MAX_PRECHECK_TARGETS,
  };
  if (!targets.length) {
    return { staticPrecheck: { status: "skipped", reason: "no_recorded_source_changes", ...scope,
      note: "Only Source edits committed by this server since its last successful build are prechecked." },
    definite: [], findings: [] };
  }
  const result = await runValidation(projectRoot, { ...validationOptions, scopeTargets: scope.scopeTargets });
  const findings = Array.isArray(result.findings) ? result.findings : [];
  const parsed = !result.reason;
  const definite = parsed ? findings.filter(isDefiniteStaticError) : [];
  const ordered = [...definite, ...findings.filter((finding) => !definite.includes(finding))];
  const status = !parsed ? "unavailable" : definite.length ? "definite_errors"
    : findings.some((finding) => finding.severity === "error") ? "advisory_errors" : findings.length ? "advisory" : "clean";
  return {
    staticPrecheck: {
      status,
      ...scope,
      findingCount: result.findingCount ?? findings.length,
      definiteErrorCount: definite.length,
      elapsedMs: result.elapsedMs || 0,
      analysisCoverage: result.analysis?.analysisCoverage?.status || (parsed ? "unknown" : "unavailable"),
      ...(parsed ? {} : { reason: String(result.reason).slice(0, 300) }),
      findings: ordered.slice(0, MAX_PRECHECK_FINDINGS).map(compactFinding),
    },
    definite,
    findings,
  };
}

function commonDottedPrefix(names) {
  const split = names.map((name) => name.split("."));
  const common = [];
  for (let index = 0; split.every((parts) => index < parts.length); index++) {
    const segment = split[0][index];
    if (!split.every((parts) => parts[index].toLowerCase() === segment.toLowerCase())) break;
    common.push(segment);
  }
  return common.join(".");
}

// After a successful build: the project Automation tests in the changed
// files' modules (and modules depending on them), as one advisory next run.
function automationTestSuggestion(projectRoot, changedPaths, projectSelector, discover = discoverAutomationTests) {
  if (!changedPaths.length) return undefined;
  let discovery;
  try { discovery = discover(projectRoot, { scopeTargets: changedPaths }); } catch { return undefined; }
  if (!discovery?.count || discovery.truncated) return undefined;
  let testFilter = commonDottedPrefix(discovery.names);
  if (!testFilter) {
    // No shared prefix: the root that covers the most related tests.
    const roots = new Map();
    for (const name of discovery.names) roots.set(name.split(".")[0], (roots.get(name.split(".")[0]) || 0) + 1);
    testFilter = [...roots].sort((left, right) => right[1] - left[1])[0][0];
  }
  const covered = discovery.names.filter((name) => name.toLowerCase() === testFilter.toLowerCase()
    || name.toLowerCase().startsWith(`${testFilter.toLowerCase()}.`));
  // The response keeps only a suggestion's tool and args; the evidence for it
  // travels beside it.
  return {
    suggestion: { tool: "run_unreal_automation_tests",
      args: { testFilter, scopeTargets: changedPaths.slice(0, MAX_PRECHECK_TARGETS),
        ...(projectSelector ? { project: projectSelector } : {}) } },
    relatedAutomationTests: { advisory: true, count: discovery.count, matchedByFilter: covered.length,
      names: discovery.names.slice(0, MAX_RELATED_TESTS) },
  };
}

module.exports = {
  automationTestSuggestion,
  isDefiniteStaticError,
  runBuildStaticPrecheck,
};
