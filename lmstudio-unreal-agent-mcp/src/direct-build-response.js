"use strict";

const crypto = require("node:crypto");
const path = require("node:path");

const { parseBuildProof } = require("./build-proof.js");

function clamp(value, fallback, min, max) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(min, Math.min(max, Math.trunc(parsed))) : fallback;
}

function digest(value) {
  return crypto.createHash("sha256").update(String(value || ""), "utf8").digest("hex");
}

function compactDiagnostic(line, maxChars = 500) {
  let value = String(line || "").replace(/\s+/g, " ").trim();
  if (!value) return "";
  const source = value.match(/(?:^|[\\/])((?:Source|Plugins)[\\/].+?\.(?:cpp|c|cc|cxx|h|hpp)(?:\(\d+(?:,\d+)?\)|:\d+(?::\d+)?))(?=:\s*(?:fatal\s+)?(?:error|warning)\b)/i);
  if (source && Number.isInteger(source.index)) {
    value = value.slice(source.index + source[0].length - source[1].length).replace(/\\/g, "/");
  }
  return value.replace(/\ufffd+/g, " ").replace(/\s+/g, " ").trim().slice(0, Math.max(80, maxChars));
}

function collectBuildDiagnostics(stdout, stderr, maxLines = 40) {
  const combined = `${stdout || ""}\n${stderr || ""}`;
  const warningsAreErrors = /UnrealHeaderTool[^\r\n]*-WarningsAsErrors/i.test(combined)
    || /Running Internal UnrealHeaderTool[^\r\n]*-WarningsAsErrors/i.test(combined);
  const lines = combined.split(/\r?\n/);
  const diagnostics = [];
  const limit = clamp(maxLines, 40, 1, 120);
  let truncated = false;
  let captureUndefined = false;
  for (const [index, raw] of lines.entries()) {
    const line = String(raw || "");
    if (/^Undefined symbols for architecture\b/i.test(line.trim())) captureUndefined = true;
    const interesting = (
      /\b(?:fatal\s+)?error\s+(?:C\d+|LNK\d+|MSB\d+|UHT\d*)\b/i.test(line)
      || /\b(?:fatal\s+)?error:/i.test(line)
      || /\b(?:UnrealHeaderTool|UBT)\s+(?:failed|error)\b/i.test(line)
      || /\bBuild failed\b|\bOtherCompilationError\b/i.test(line)
      || /\bUnhandled\s+\d+\s+aggregate exceptions?\b/i.test(line)
      || (warningsAreErrors && /\([^\r\n]*\):\s*Warning:/i.test(line))
      || (captureUndefined && /^\s*".+",\s+referenced from:\s*$/i.test(line))
    );
    if (interesting) {
      const compact = compactDiagnostic(line);
      if (compact && !diagnostics.includes(compact)) diagnostics.push(compact);
    }
    if (captureUndefined && /^\s*(?:ld:|clang\+\+:|Result:|Total time)/i.test(line)) captureUndefined = false;
    if (diagnostics.length >= limit) { truncated = index < lines.length - 1; break; }
  }
  return { diagnostics, coverage: { scope: "filtered_log_lines", truncated, maxEntries: limit,
    returnedEntries: diagnostics.length, perEntryMaxChars: 500, completeBuildLog: false } };
}

function extractBuildDiagnostics(stdout, stderr, maxLines = 40) {
  return collectBuildDiagnostics(stdout, stderr, maxLines).diagnostics;
}

function buildDirectResponse({ result, build, planResult, projectPath, command, logPath, verbose = false, executionMode = "direct" }) {
  const stdout = String(result?.stdout || "");
  const stderr = String(result?.stderr || "");
  const rawOutput = `${stdout}\n${stderr}`;
  const { diagnostics, coverage } = collectBuildDiagnostics(stdout, stderr);
  const proof = parseBuildProof(result?.ok === true, rawOutput, { logPath });
  const actionsExecuted = proof.highestObservedActionIndex || proof.actionCount;
  const upToDate = proof.targetUpToDate === true;
  const platform = String(build?.platform || (process.platform === "win32" ? "Win64" : process.platform === "darwin" ? "Mac" : "Linux"));
  const ok = result?.ok === true;
  const firstError = diagnostics[0] || "";
  const errorCode = ok ? "" : String(result?.errorCode || (result?.timedOut ? "BUILD_TIMEOUT" : "BUILD_FAILED"));
  const summary = ok
    ? upToDate && Number(actionsExecuted || 0) === 0
      ? `BUILD SUCCEEDED (up to date; 0 actions) — ${build.target} ${platform} ${build.configuration || "Development"}`
      : `BUILD SUCCEEDED — ${actionsExecuted ?? "unknown"} action(s) — ${build.target} ${platform} ${build.configuration || "Development"}`
    : `BUILD FAILED${firstError ? ` — ${firstError}` : ""}`;
  const outputTail = outputTailLines(rawOutput);
  const payload = {
    ok,
    executionMode,
    summary,
    message: ok ? summary : String(result?.error || firstError || "Unreal build failed."),
    errorCode,
    exitCode: result?.exitCode ?? null,
    timedOut: result?.timedOut === true,
    cancelled: result?.cancelled === true,
    processStarted: result?.processStarted,
    processExited: result?.processExited,
    terminationStatus: result?.terminationStatus,
    diagnostics,
    diagnosticCoverage: coverage,
    firstError: firstError || null,
    outputTail: ok ? [] : outputTail,
    fullLogPath: logPath,
    proof: {
      level: proof.proofLevel,
      upToDate,
      declaredTotalActions: proof.declaredTotalActions,
      observedCompileLines: proof.compileLineCount,
      observedLinkLines: proof.linkLineCount,
      highestObservedActionIndex: proof.highestObservedActionIndex,
      actionsExecuted,
    },
    project: {
      projectPath,
      projectFile: path.basename(projectPath),
      target: build.target,
      platform,
      configuration: build.configuration || "Development",
      engineRoot: build.engineRoot,
      engineAssociation: build.requestedEngineAssociation || build.engineAssociation || null,
      selectionReason: planResult?.selectionReason || null,
    },
    fingerprints: {
      commandSha256: digest(command),
      diagnosticsSha256: digest(diagnostics.length ? diagnostics.join("\n") : outputTail.join("\n")),
      outputSha256: digest(rawOutput),
    },
    retry: { allowed: false, mode: "none" },
  };
  if (verbose) {
    payload.command = command;
    payload.stdout = tailText(stdout, VERBOSE_STREAM_CHARS);
    payload.stderr = tailText(stderr, VERBOSE_STREAM_CHARS);
  }
  return payload;
}

// Long-running tool results are summaries: the complete output stays in the
// log file named by fullLogPath, and only bounded tails reach the model.
// Failures must also fit under the 4,096-character error transport cap.
const LONG_RUNNING_SUCCESS_CHARS = 16_000;
const LONG_RUNNING_FAILURE_CHARS = 3_584;
const VERBOSE_STREAM_CHARS = 6_000;
const OUTPUT_TAIL_LINES = 30;
// [field, keep]: "tail" keeps the newest output, "head" the first findings.
const TRIM_ORDER = [
  ["stdout", "tail"], ["stderr", "tail"], ["outputTail", "tail"],
  ["findings", "head"], ["expectedTests", "head"], ["diagnostics", "head"],
];

function tailText(value, maxChars) {
  const text = String(value || "");
  if (text.length <= maxChars) return text;
  const marker = `...[${text.length - maxChars} chars omitted; see fullLogPath]\n`;
  return marker + text.slice(-Math.max(0, maxChars - marker.length));
}

function outputTailLines(text, maxLines = OUTPUT_TAIL_LINES) {
  return String(text || "").split(/\r?\n/).filter(Boolean).slice(-maxLines)
    .map((line) => compactDiagnostic(line, 1000)).filter(Boolean);
}

function trimField(payload, field, keep) {
  const value = payload[field];
  if (typeof value === "string") {
    if (!value) return false;
    payload[field] = value.length <= 64 ? "" : tailText(value, Math.floor(value.length / 2));
    return true;
  }
  if (!Array.isArray(value) || !value.length) return false;
  const count = Math.floor(value.length / 2);
  payload[field] = keep === "tail" ? value.slice(value.length - count) : value.slice(0, count);
  return true;
}

/**
 * Halve the bulkiest output fields until the compact model JSON fits.
 * Summary, exit status, counts and fullLogPath are never trimmed.
 */
function boundLongRunningPayload(payload, options = {}) {
  const maxChars = Number(options.maxChars) || (payload?.ok === false ? LONG_RUNNING_FAILURE_CHARS : LONG_RUNNING_SUCCESS_CHARS);
  const bounded = { ...payload };
  const original = {};
  for (const [field] of TRIM_ORDER) {
    const value = bounded[field];
    if (typeof value === "string" || Array.isArray(value)) original[field] = value.length;
  }
  let guard = 0;
  while (JSON.stringify(bounded).length > maxChars && guard < 256) {
    guard += 1;
    if (!TRIM_ORDER.some(([field, keep]) => trimField(bounded, field, keep))) break;
  }
  const trimmed = Object.fromEntries(Object.entries(original)
    .filter(([field, length]) => bounded[field]?.length !== length)
    .map(([field, length]) => [field, { original: length, returned: bounded[field]?.length || 0 }]));
  if (trimmed.diagnostics && bounded.diagnosticCoverage) {
    bounded.diagnosticCoverage = { ...bounded.diagnosticCoverage, truncated: true, returnedEntries: bounded.diagnostics.length };
  }
  if (Object.keys(trimmed).length) bounded.responseTrimmed = trimmed;
  return bounded;
}

module.exports = {
  LONG_RUNNING_FAILURE_CHARS,
  LONG_RUNNING_SUCCESS_CHARS,
  VERBOSE_STREAM_CHARS,
  boundLongRunningPayload,
  buildDirectResponse,
  compactDiagnostic,
  extractBuildDiagnostics,
  outputTailLines,
  tailText,
};
