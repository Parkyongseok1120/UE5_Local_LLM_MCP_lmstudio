"use strict";

const crypto = require("node:crypto");
const path = require("node:path");

const { renderModelJson } = require("./direct-response");
const { parseBuildProof } = require("./build-proof.js");

function clamp(value, fallback, min, max) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(min, Math.min(max, Math.trunc(parsed))) : fallback;
}

function digest(value) {
  return crypto.createHash("sha256").update(String(value || ""), "utf8").digest("hex");
}

// How diagnosticContext() appends the compiler's echoed source line; it is last.
const SOURCE_ECHO = " | src: ";

function compactDiagnostic(line, maxChars = 500) {
  let value = String(line || "").replace(/\s+/g, " ").trim();
  if (!value) return "";
  // The echoed source line is code, not a path: its backslashes are content.
  // Conversation 1794590083146 was shown "src: /tif (...)" for a line that
  // began with a literal backslash-t, the very cause of the C2017 it reported.
  const echoAt = value.indexOf(SOURCE_ECHO);
  const echo = echoAt >= 0 ? value.slice(echoAt) : "";
  if (echo) value = value.slice(0, echoAt);
  const source = value.match(/(?:^|[\\/])((?:Source|Plugins)[\\/].+?\.(?:cpp|c|cc|cxx|h|hpp)(?:\(\d+(?:,\d+)?\)|:\d+(?::\d+)?))(?=:\s*(?:fatal\s+)?(?:error|warning)\b)/i);
  if (source && Number.isInteger(source.index)) {
    value = value.slice(source.index + source[0].length - source[1].length).replace(/\\/g, "/");
  }
  return (value + echo).slice(0, Math.max(80, maxChars));
}

const MSVC_DIAGNOSTIC = /\(\d+(?:,\d+)?\)\s*:\s*(?:fatal\s+)?(?:error|warning)\s+C\d+/i;
const NOTE_LINE = /^(.*?)\((\d+)(?:,\d+)?\)\s*:\s*note\s*:\s*(.*)$/i;
const BOUNDARY = /\b(?:error|warning)\s+(?:C|LNK|MSB)\d+|^\s*\[\d+\/\d+\]|\bResult:|\bTotal execution time\b/i;
const MAX_NOTES = 2, NOTE_CHARS = 160, SOURCE_CHARS = 120;

/** The notes and the echoed source line MSVC prints right after an error. They
 * go into the same entry: the plugin keeps diagnostics as strings, so a side
 * field would be lost when its context is compacted. Lines are taken from the
 * build output itself, never re-read from a file that may have changed since. */
function diagnosticContext(lines, index) {
  const notes = [];
  let source = "";
  const errorPath = /^(.*?)\(\d+/.exec(String(lines[index] || "").trim())?.[1] || "";
  for (let next = index + 1; next < Math.min(lines.length, index + 10); next += 1) {
    const line = String(lines[next] || "");
    if (!line.trim()) continue;
    if (BOUNDARY.test(line)) break;
    const note = NOTE_LINE.exec(line.trim());
    if (note) {
      if (notes.length < MAX_NOTES) {
        const where = note[1] && note[1] !== errorPath ? `${path.basename(note[1].replace(/\\/g, "/"))}:${note[2]} ` : "";
        notes.push(`note: ${where}${note[3]}`.replace(/\uFFFD+/g, " ").replace(/\s+/g, " ").trim().slice(0, NOTE_CHARS));
      }
      continue;
    }
    if (/^\s*\^[~\s^]*$/.test(line)) continue;
    // The source echo precedes its caret line.
    if (!source && /^\s*\^/.test(String(lines[next + 1] || ""))) source = line.replace(/\s+/g, " ").trim().slice(0, SOURCE_CHARS);
  }
  return [...notes, ...(source ? [`${SOURCE_ECHO.slice(3)}${source}`] : [])];
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
      const context = MSVC_DIAGNOSTIC.test(line) ? diagnosticContext(lines, index) : [];
      const compact = compactDiagnostic(context.length ? [line, ...context].join(" | ") : line);
      if (compact && !diagnostics.includes(compact)) diagnostics.push(compact);
    }
    if (captureUndefined && /^\s*(?:ld:|clang\+\+:|Result:|Total time)/i.test(line)) captureUndefined = false;
    if (diagnostics.length >= limit) { truncated = index < lines.length - 1; break; }
  }
  return { diagnostics, coverage: { scope: "filtered_log_lines", truncated, maxEntries: limit,
    returnedEntries: diagnostics.length, perEntryMaxChars: 500, completeBuildLog: false } };
}

// A repeated location/code with different messages is ambiguous and must never
// select a replacement. Strip only the absolute prefix the console also drops.
function diagnosticKey(line) {
  return /^(.+?\(\d+(?:,\d+)?\))\s*:\s*(?:fatal\s+)?error\s+(C\d+)\b/i.exec(line)
    ?.slice(1).join("|").replace(/\\/g, "/") || "";
}

function extractBuildDiagnostics(stdout, stderr, maxLines = 40) {
  return collectBuildDiagnostics(stdout, stderr, maxLines).diagnostics;
}

function buildDirectResponse({ result, build, planResult, projectPath, command, logPath, verbose = false, executionMode = "direct" }) {
  const stdout = String(result?.stdout || "");
  const stderr = String(result?.stderr || "");
  const rawOutput = `${stdout}\n${stderr}`;
  const { diagnostics, coverage } = collectBuildDiagnostics(stdout, stderr);
  if (result?.ubtDiagnostics) {
    for (const [index, line] of diagnostics.entries()) {
      diagnostics[index] = result.ubtDiagnostics.get(diagnosticKey(line)) || line;
    }
  }
  const proof = parseBuildProof(result?.ok === true, rawOutput, { logPath });
  const actionsExecuted = proof.highestObservedActionIndex || proof.actionCount;
  const upToDate = proof.targetUpToDate === true;
  const platform = String(build?.platform || (process.platform === "win32" ? "Win64" : process.platform === "darwin" ? "Mac" : "Linux"));
  const ok = result?.ok === true;
  const firstError = diagnostics[0]?.split(" | ")[0] || "";
  const errorCode = ok ? "" : String(result?.errorCode || (result?.timedOut ? "BUILD_TIMEOUT" : "BUILD_FAILED"));
  const summary = ok
    ? upToDate && Number(actionsExecuted || 0) === 0
      ? `BUILD SUCCEEDED (up to date; 0 actions) — ${build.target} ${platform} ${build.configuration || "Development"}`
      : `BUILD SUCCEEDED — ${actionsExecuted ?? "unknown"} action(s) — ${build.target} ${platform} ${build.configuration || "Development"}`
    : "BUILD FAILED";
  const outputTail = outputTailLines(rawOutput);
  const payload = {
    ok,
    executionMode,
    summary,
    message: ok ? summary : String(result?.error || "Unreal build failed. See diagnostics and the run log."),
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
    ...(result?.ubtLog ? { ubtLog: result.ubtLog } : {}),
    ...(!ok && logPath ? { suggestion: { tool: "read_unreal_logs", args: {
      fileName: result?.ubtLog?.readableFileName || path.basename(logPath), mode: "range", cursorByte: 0,
    } } } : {}),
    ...(result?.outputRecovery ? { outputRecovery: result.outputRecovery } : {}),
    ...(result?.environmentNotice ? { environmentNotice: result.environmentNotice } : {}),
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

function fitBuildFailure(payload, maxChars) {
  // Start with the facts needed for recovery. Preferring complete diagnostics
  // over tails and precheck details avoids losing all errors to the transport.
  const { project, diagnostics, diagnosticCoverage, fullLogPath } = payload;
  const out = {
    ok: false, errorCode: payload.errorCode, summary: "BUILD FAILED",
    message: payload.message.slice(0, 220), retry: payload.retry, executionMode: payload.executionMode,
    exitCode: payload.exitCode, timedOut: payload.timedOut, cancelled: payload.cancelled,
    processStarted: payload.processStarted, processExited: payload.processExited, terminationStatus: payload.terminationStatus,
    project: { projectPath: project.projectPath, projectFile: project.projectFile, target: project.target, configuration: project.configuration, platform: project.platform },
    fullLogPath, ubtLog: payload.ubtLog, outputRecovery: payload.outputRecovery,
    firstError: diagnostics[0]?.split(" | ")[0].slice(0, 180) || null,
    diagnostics: [], diagnosticCoverage: { ...diagnosticCoverage, returnedEntries: 0, truncated: true,
      collectedEntries: diagnostics.length }, responseTrimmed: { diagnostics: { original: diagnostics.length, returned: 0 } },
  };
  for (const field of ["stdout", "stderr", "outputTail"]) {
    if (payload[field]?.length) out.responseTrimmed[field] = { original: payload[field].length, returned: 0 };
  }
  // The exact basename is sufficient for read_unreal_logs even with a very
  // long workspace prefix. Keep the absolute path whenever it fits.
  const fits = () => renderModelJson(out).length <= maxChars;
  if (renderModelJson(out).length > maxChars - 512) delete out.project.projectPath;
  if (renderModelJson(out).length > maxChars - 512) out.fullLogPath = path.basename(fullLogPath || "");
  if (!fits()) {
    // A plan/launcher refusal may echo unbounded user input (e.g. a target).
    for (const key of ["target", "configuration", "platform", "projectFile"])
      out.project[key] = String(out.project[key] || "").slice(0, 120);
  }
  function optional(key, value) {
    if (value === undefined) return;
    out[key] = value;
    if (!fits()) delete out[key];
  }
  optional("suggestion", payload.suggestion);
  for (const diagnostic of diagnostics) {
    const count = out.diagnostics.push(diagnostic);
    out.diagnosticCoverage.returnedEntries = out.responseTrimmed.diagnostics.returned = count;
    if (!fits()) {
      out.diagnostics.pop();
      out.diagnosticCoverage.returnedEntries = out.responseTrimmed.diagnostics.returned = count - 1;
      break;
    }
  }
  out.diagnosticCoverage.truncated = diagnosticCoverage.truncated || out.diagnostics.length < diagnostics.length;
  if (payload.staticPrecheck) optional("staticPrecheck", {
    status: payload.staticPrecheck.status, contractRevision: payload.staticPrecheck.contractRevision,
  });
  optional("proof", payload.proof);
  optional("environmentNotice", payload.environmentNotice);
  optional("fingerprints", payload.fingerprints);
  if (!diagnostics.length && out.responseTrimmed.outputTail) {
    optional("outputTail", payload.outputTail.slice(-2).map(line => line.slice(0, 200)));
    out.responseTrimmed.outputTail.returned = out.outputTail?.length || 0;
  }
  return out;
}

function boundLongRunningPayload(payload, options = {}) {
  const maxChars = Number(options.maxChars) || (payload?.ok === false ? LONG_RUNNING_FAILURE_CHARS : LONG_RUNNING_SUCCESS_CHARS);
  if (payload?.ok === false && payload.diagnosticCoverage && payload.project && renderModelJson(payload).length > maxChars)
    return fitBuildFailure(payload, maxChars);
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
  diagnosticKey,
  outputTailLines,
  tailText,
};
