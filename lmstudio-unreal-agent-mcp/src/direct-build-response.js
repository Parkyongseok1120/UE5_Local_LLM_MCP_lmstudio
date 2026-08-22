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

function extractBuildDiagnostics(stdout, stderr, maxLines = 40) {
  const combined = `${stdout || ""}\n${stderr || ""}`;
  const warningsAreErrors = /UnrealHeaderTool[^\r\n]*-WarningsAsErrors/i.test(combined)
    || /Running Internal UnrealHeaderTool[^\r\n]*-WarningsAsErrors/i.test(combined);
  const lines = combined.split(/\r?\n/);
  const diagnostics = [];
  let captureUndefined = false;
  for (const raw of lines) {
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
    if (diagnostics.length >= clamp(maxLines, 40, 1, 120)) break;
  }
  return diagnostics;
}

function buildDirectResponse({ result, build, planResult, projectPath, command, logPath, verbose = false, executionMode = "direct" }) {
  const stdout = String(result?.stdout || "");
  const stderr = String(result?.stderr || "");
  const rawOutput = `${stdout}\n${stderr}`;
  const diagnostics = extractBuildDiagnostics(stdout, stderr);
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
  const outputTail = rawOutput.split(/\r?\n/).filter(Boolean).slice(-30).map((line) => compactDiagnostic(line, 1000)).filter(Boolean);
  const payload = {
    ok,
    executionMode,
    summary,
    message: ok ? summary : String(result?.error || firstError || "Unreal build failed."),
    errorCode,
    exitCode: result?.exitCode ?? null,
    timedOut: result?.timedOut === true,
    diagnostics,
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
    payload.stdout = stdout.slice(-100_000);
    payload.stderr = stderr.slice(-100_000);
  }
  return payload;
}

module.exports = {
  buildDirectResponse,
  compactDiagnostic,
  extractBuildDiagnostics,
};
