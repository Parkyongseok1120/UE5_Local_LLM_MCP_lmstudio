"use strict";

const fs = require("fs");
const path = require("path");
const { atomicWriteText } = require("./atomic-io");

const DEFAULT_AGENT_RESULT_MAX_CHARS = 32_000;
const DEFAULT_BUILD_ERROR_LINES = 20;
const DEFAULT_LOG_RESULT_MAX_CHARS = 24_000;
const DEFAULT_VALIDATION_FINDING_CAP = 12;

const VALIDATION_CATEGORY_HINTS = {
  UPROPERTY: { group: "GC/Ownership", hint: "Add UPROPERTY() or TObjectPtr with UPROPERTY on retained UObject members.", doc: "RAG_Project_Guidelines/Unreal_Programming/27_Generation_Guardrails_To_Validator_Map.md" },
  TOBJECTPTR: { group: "GC/Ownership", hint: "TObjectPtr members need UPROPERTY() for GC tracking.", doc: "RAG_Project_Guidelines/Unreal_Programming/27_Generation_Guardrails_To_Validator_Map.md" },
  RAW_UOBJECT: { group: "GC/Ownership", hint: "Use UPROPERTY(TObjectPtr<...>) instead of raw UObject pointers.", doc: "RAG_Project_Guidelines/Unreal_Programming/27_Generation_Guardrails_To_Validator_Map.md" },
  DELEGATE: { group: "GC/Lifecycle", hint: "RemoveDynamic/RemoveAll/Unbind in EndPlay or Deinitialize.", doc: "RAG_Project_Guidelines/Unreal_Programming/28_Delegate_Lifecycle_Codegen_Recipe.md" },
  TIMER: { group: "GC/Lifecycle", hint: "ClearTimer or ClearAllTimersForObject in teardown.", doc: "RAG_Project_Guidelines/Unreal_Programming/33_Teardown_Symmetry_And_Lifecycle.md" },
  INTERRUPT: { group: "GC/Lifecycle", hint: "Handle bInterrupted/bWasCancelled in montage/callback end handlers.", doc: "RAG_Project_Guidelines/Unreal_Programming/33_Teardown_Symmetry_And_Lifecycle.md" },
  CAST: { group: "Safety", hint: "Check Cast<> result with if (IsValid(...)) before dereferencing.", doc: "RAG_Project_Guidelines/Unreal_Programming/27_Generation_Guardrails_To_Validator_Map.md" },
  REPLICAT: { group: "Networking", hint: "Add GetLifetimeReplicatedProps and DOREPLIFETIME in .cpp.", doc: "RAG_Project_Guidelines/Unreal_Programming/29_Replication_RPC_Codegen_Recipe.md" },
  NEW_DELETE: { group: "GC/Ownership", hint: "Use NewObject<> with outer; never new/delete on UObject types.", doc: "RAG_Project_Guidelines/06_Unreal_AntiPatterns.md" },
  LOAD: { group: "Performance", hint: "Prefer TSoftObjectPtr/FStreamableManager over sync LoadObject in hot paths.", doc: "RAG_Project_Guidelines/Unreal_Programming/30_Async_Asset_Load_Codegen_Recipe.md" },
  ASSET_PATH: { group: "Assets", hint: "Prefer TSoftObjectPtr or ConstructorHelpers in ctor over hardcoded /Game/ paths.", doc: "RAG_Project_Guidelines/Unreal_Programming/30_Async_Asset_Load_Codegen_Recipe.md" },
  DEFAULT: { group: "Advisory", hint: "Review finding and fix before claiming runtime correctness.", doc: "RAG_Project_Guidelines/Unreal_Programming/27_Generation_Guardrails_To_Validator_Map.md" }
};

function clampInt(value, fallback, min, max) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(Math.trunc(parsed), max));
}

function resolveAgentResultMaxChars(env = process.env) {
  return clampInt(
    env.MCP_AGENT_RESULT_MAX_CHARS,
    DEFAULT_AGENT_RESULT_MAX_CHARS,
    4_000,
    80_000
  );
}

function truncateUtf8(text, maxBytes) {
  const value = String(text || "");
  const raw = Buffer.from(value, "utf8");
  if (raw.length <= maxBytes) return value;
  const suffix = `\n[TRUNCATED: result exceeded ${maxBytes} bytes]`;
  const suffixBytes = Buffer.byteLength(suffix, "utf8");
  return raw.subarray(0, Math.max(0, maxBytes - suffixBytes)).toString("utf8") + suffix;
}

function truncateToCharLimit(text, maxChars) {
  const value = String(text || "");
  if (value.length <= maxChars) return value;
  const suffix = `\n[TRUNCATED: tool result exceeded ${maxChars} characters; use narrower arguments]`;
  if (maxChars <= suffix.length) {
    return value.slice(0, maxChars);
  }
  return value.slice(0, maxChars - suffix.length) + suffix;
}

function shrinkEmergencyPayload(emergency, maxChars) {
  const working = { ...emergency };
  const trimString = (field, limit) => {
    if (typeof working[field] !== "string") return;
    if (working[field].length > limit) {
      working[field] = working[field].slice(0, Math.max(0, limit));
    }
  };
  const trimArray = (field, maxItems, itemLimit) => {
    if (!Array.isArray(working[field])) return;
    working[field] = working[field]
      .slice(0, maxItems)
      .map((item) => {
        if (typeof item === "string") {
          return item.slice(0, itemLimit);
        }
        if (item && typeof item === "object") {
          return {
            ...item,
            args: item.args && typeof item.args === "object"
              ? Object.fromEntries(
                Object.entries(item.args).map(([key, value]) => [
                  key,
                  typeof value === "string" ? value.slice(0, itemLimit) : value
                ])
              )
              : item.args
          };
        }
        return item;
      });
  };

  let serialized = JSON.stringify(working, null, 2);
  if (serialized.length <= maxChars) return serialized;

  trimString("error", Math.max(120, Math.floor(maxChars / 8)));
  trimArray("nextSteps", 3, 180);
  trimArray("suggestedToolCalls", 2, 120);
  trimString("preview", Math.max(64, Math.floor(maxChars / 6)));
  serialized = JSON.stringify(working, null, 2);
  if (serialized.length <= maxChars) return serialized;

  delete working.preview;
  trimArray("nextSteps", 1, 120);
  trimArray("suggestedToolCalls", 1, 80);
  trimString("error", 80);
  serialized = JSON.stringify(working, null, 2);
  if (serialized.length <= maxChars) return serialized;

  return truncateToCharLimit(serialized, maxChars);
}

function compactMcpContent(content, maxChars = resolveAgentResultMaxChars()) {
  const value = String(content ?? "");
  if (value.length <= maxChars) return value;

  try {
    const parsed = JSON.parse(value);
    const summary = parsed && typeof parsed === "object" && parsed.summary
      ? String(parsed.summary).slice(0, Math.max(80, Math.floor(maxChars / 6)))
      : "Tool result truncated — rerun with narrower arguments.";
    const emergency = {
      summary,
      ok: parsed && typeof parsed === "object" ? parsed.ok ?? null : null,
      error: parsed && typeof parsed === "object" ? parsed.error ?? null : null,
      truncated: true,
      originalChars: value.length,
      nextSteps: parsed && typeof parsed === "object"
        ? parsed.nextSteps || ["Rerun the tool with narrower arguments."]
        : ["Rerun the tool with narrower arguments."],
      suggestedToolCalls: parsed && typeof parsed === "object"
        ? parsed.suggestedToolCalls || []
        : [],
      preview: value.slice(0, Math.max(256, maxChars - 1_000))
    };
    return shrinkEmergencyPayload(emergency, maxChars);
  } catch {
    return truncateToCharLimit(value, maxChars);
  }
}

function errorPayload(message, options = {}) {
  const error = String(message || "Unknown error");
  const firstLine = error.split(/\r?\n/, 1)[0];
  const payload = {
    summary: `ERROR — ${firstLine}`,
    ok: false,
    error,
    phase: "failed",
    userMessage: options.userMessage || firstLine,
    nextSteps: Array.isArray(options.nextSteps) ? options.nextSteps : [],
    suggestedToolCalls: Array.isArray(options.suggestedToolCalls)
      ? options.suggestedToolCalls
      : []
  };
  const reserved = new Set([
    "userMessage", "nextSteps", "suggestedToolCalls", "writeToolPolicy",
    "requiredNextTool", "errorCode", "retryable", "doNotRetry", "agentInstruction",
    "writeApplied", "bookkeepingFailed", "mutationGenerationNotRecorded", "operation", "path",
  ]);
  for (const [key, value] of Object.entries(options)) {
    if (reserved.has(key) || value === undefined) continue;
    payload[key] = value;
  }
  if (options.writeToolPolicy) payload.writeToolPolicy = options.writeToolPolicy;
  if (options.requiredNextTool) payload.requiredNextTool = options.requiredNextTool;
  if (options.errorCode) payload.errorCode = options.errorCode;
  if (options.retryable !== undefined) payload.retryable = options.retryable;
  if (options.doNotRetry) payload.doNotRetry = options.doNotRetry;
  if (options.writeApplied !== undefined) payload.writeApplied = options.writeApplied;
  if (options.bookkeepingFailed !== undefined) payload.bookkeepingFailed = options.bookkeepingFailed;
  if (options.mutationGenerationNotRecorded !== undefined) {
    payload.mutationGenerationNotRecorded = options.mutationGenerationNotRecorded;
  }
  if (options.operation) payload.operation = options.operation;
  if (options.path) payload.path = options.path;
  if (options.agentInstruction) payload.agentInstruction = options.agentInstruction;
  else if (Array.isArray(options.nextSteps) && options.nextSteps.length) {
    payload.agentInstruction = options.nextSteps.join(" ");
  }
  return payload;
}

function writeDisciplineOptions(existingPath = true) {
  if (!existingPath) return {};
  return {
    errorCode: "FILE_ALREADY_EXISTS",
    writeToolPolicy: "create_only",
    requiredNextTool: "replace_in_file",
    doNotRetry: "write_file",
    doNotCall: ["unreal_agent_plan"],
    authorizationRefreshRequired: false,
    nextSteps: ["Read the existing file, then patch it with replace_in_file. Do not retry write_file on this path."],
    suggestedToolCalls: [
      { tool: "read_file", args: { path: "<path>", detailLevel: "compact" } },
      { tool: "replace_in_file", args: { path: "<path>", oldText: "<exact text from read_file>", newText: "<replacement>", expectedOccurrences: 1 } }
    ]
  };
}

function parseBuildExecutionSummary(stdout, stderr) {
  const combined = `${stdout || ""}\n${stderr || ""}`;
  const upToDate = /Target is up to date/i.test(combined);
  let actionsExecuted = null;
  const executedPatterns = [
    /(?:^|\n)\s*(?:run|building)\s+(\d+)\s+action\(s\)/i,
    /------\s*Building\s+(\d+)\s+action\(s\)/i,
    /Building\s+(\d+)\s+action\(s\)\s+with\s+\d+\s+process/i
  ];
  for (const pattern of executedPatterns) {
    const match = combined.match(pattern);
    if (match) {
      actionsExecuted = Number.parseInt(match[1], 10);
      break;
    }
  }
  return { upToDate, actionsExecuted };
}

function validationFindingMeta(code) {
  const value = String(code || "");
  if (value.includes("REPLICAT")) {
    return VALIDATION_CATEGORY_HINTS.REPLICAT;
  }
  if (value.includes("DELEGATE") || value.includes("MONTAGE")) {
    return VALIDATION_CATEGORY_HINTS.DELEGATE;
  }
  if (value.includes("TIMER")) {
    return VALIDATION_CATEGORY_HINTS.TIMER;
  }
  if (value.includes("INTERRUPT")) {
    return VALIDATION_CATEGORY_HINTS.INTERRUPT;
  }
  if (value.includes("CAST")) {
    return VALIDATION_CATEGORY_HINTS.CAST;
  }
  if (value.includes("NEW_DELETE")) {
    return VALIDATION_CATEGORY_HINTS.NEW_DELETE;
  }
  if (value.includes("SYNC_LOAD") || (value.includes("LOAD") && !value.includes("UPROPERTY"))) {
    return VALIDATION_CATEGORY_HINTS.LOAD;
  }
  if (value.includes("ASSET_PATH")) {
    return VALIDATION_CATEGORY_HINTS.ASSET_PATH;
  }
  if (value.includes("UPROPERTY") || value.includes("UOBJECT") || value.includes("TOBJECTPTR")) {
    return VALIDATION_CATEGORY_HINTS.UPROPERTY;
  }
  return VALIDATION_CATEGORY_HINTS.DEFAULT;
}

function compactValidationPayload(validation, maxFindings = DEFAULT_VALIDATION_FINDING_CAP) {
  if (!validation || validation.skipped) return null;
  const rawFindings = validation.findings || [];
  const seen = new Set();
  const grouped = [];
  for (const finding of rawFindings) {
    const key = `${finding.code}:${finding.path}:${finding.line}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const meta = validationFindingMeta(finding.code);
    grouped.push({
      severity: finding.severity,
      code: finding.code,
      path: finding.path,
      line: finding.line,
      message: finding.message,
      group: meta.group,
      fixHint: meta.hint,
      doc: meta.doc
    });
  }
  const findings = grouped.slice(0, maxFindings);
  const omittedFindingCount = Math.max(0, grouped.length - findings.length);
  const blockingErrorCount = grouped.filter((item) => item.severity === "error").length;
  const warningCount = grouped.filter((item) => item.severity === "warning").length;
  const infoCount = grouped.filter((item) => item.severity === "info").length;
  const groups = [...new Set(grouped.map((item) => item.group))];
  return {
    ok: validation.ok !== false,
    findingCount: validation.findingCount || grouped.length,
    findings,
    omittedFindingCount,
    blockingErrorCount,
    warningCount,
    infoCount,
    advisoryOnly: blockingErrorCount === 0,
    groups,
    deferredCount: validation.deferredCount || 0,
    preExistingCount: validation.preExistingCount || 0,
    timedOut: Boolean(validation.timedOut),
    note: validation.note || (omittedFindingCount
      ? `${omittedFindingCount} more advisory finding(s) omitted; run static_validate_project for full list.`
      : "")
  };
}

function slimWriteSuccessPayload(summary, validation, options = {}) {
  const payload = {
    summary,
    ok: true,
    phase: "complete",
    userMessage: summary,
    path: options.path || null,
    operation: options.operation || null,
    bytesWritten: options.bytesWritten ?? null,
    validationSummary: null,
    nextSteps: options.nextSteps || []
  };
  if (options.replacements != null) {
    payload.replacements = options.replacements;
  }
  const compact = compactValidationPayload(validation);
  if (compact) {
    payload.validationSummary = {
      ok: compact.ok,
      findingCount: compact.findingCount,
      blockingErrorCount: compact.blockingErrorCount,
      warningCount: compact.warningCount,
      infoCount: compact.infoCount,
      groups: compact.groups,
      scanMode: validation.scanMode || null,
      elapsedMs: validation.elapsedMs ?? null,
      topFindings: compact.findings.slice(0, 5).map((item) => ({
        code: item.code,
        path: item.path,
        fixHint: item.fixHint
      })),
      omittedFindingCount: compact.omittedFindingCount,
      deferredCount: compact.deferredCount,
      preExistingCount: compact.preExistingCount,
      note: compact.note
    };
  }
  if (validation && validation.timedOut) {
    payload.validationSummary = payload.validationSummary || { ok: true };
    payload.validationSummary.note = "validation skipped (time budget); run static_validate_project before build";
  }
  return payload;
}

const { parseBuildProof } = require("./build-proof");

function extractLikelyCompileErrors(stdout, stderr, maxLines = DEFAULT_BUILD_ERROR_LINES) {
  const combined = `${stdout || ""}\n${stderr || ""}`;
  const uhtWarningsAreErrors = /UnrealHeaderTool[^\r\n]*-WarningsAsErrors/i.test(combined)
    || /Running Internal UnrealHeaderTool[^\r\n]*-WarningsAsErrors/i.test(combined);
  const interesting = combined.split(/\r?\n/).filter((line) => (
    /\berror\s+(C\d+|LNK\d+|MSB\d+|UHT\d*)\b/i.test(line)
    || /\bfatal error\b/i.test(line)
    || /\bUnrealHeaderTool failed\b/i.test(line)
    || /\bUBT ERROR\b/i.test(line)
    || /\bBuild failed\b/i.test(line)
    || /\berror:/i.test(line)
    || (uhtWarningsAreErrors && /\([^\r\n]*\):\s*Warning:/i.test(line))
    || /\bOtherCompilationError\b/i.test(line)
    || /\bUnhandled\s+\d+\s+aggregate exceptions?\b/i.test(line)
  ));
  return interesting.slice(0, clampInt(maxLines, DEFAULT_BUILD_ERROR_LINES, 1, 120));
}

function firstUsefulLine(lines) {
  return (lines || []).find((line) => String(line).trim()) || "";
}

function compactCompilerDiagnostic(line, maxChars = 360) {
  let value = String(line || "").replace(/\s+/g, " ").trim();
  if (!value) return "";

  // Keep a portable basename/line coordinate instead of leaking a long,
  // machine-specific absolute path into the next model prompt.
  const source = value.match(/(?:^|[\\/])([^\\/]+\.(?:cpp|c|cc|cxx|h|hpp)\(\d+(?:,\d+)?\))/i);
  if (source && Number.isInteger(source.index)) {
    value = value.slice(source.index + source[0].length - source[1].length);
  }

  // A compact query needs the stable ASCII error code and C++ symbols. Localized
  // prose remains available in fullLogPath (and verbose output) after decoding.
  const firstNonAscii = value.search(/[^\x09\x20-\x7e]/);
  if (firstNonAscii >= 0) value = value.slice(0, firstNonAscii).trim();
  value = value.replace(/\ufffd+/g, " ").replace(/\s+/g, " ").replace(/\?+$/, "").trim();
  return value.slice(0, Math.max(80, Number(maxChars) || 360));
}

function buildResponsePayload({ result, build, planResult, projectPath, command, logPath, verbose = false }) {
  const errorLines = extractLikelyCompileErrors(result.stdout, result.stderr);
  const compactErrorLines = errorLines.map((line) => compactCompilerDiagnostic(line)).filter(Boolean);
  const responseErrorLines = verbose ? errorLines : Array.from(new Set(compactErrorLines));
  const firstError = firstUsefulLine(compactErrorLines);
  const execSummary = parseBuildExecutionSummary(result.stdout, result.stderr);
  const proof = parseBuildProof(result.ok, `${result.stdout || ""}\n${result.stderr || ""}`, { logPath });
  const upToDate = proof.targetUpToDate;
  const actionsExecuted = proof.highestObservedActionIndex || proof.actionCount;
  const proofLevel = proof.proofLevel;
  const hasCompileEvidence = Number(proof.compileLineCount || 0) > 0 || Number(proof.linkLineCount || 0) > 0;

  let summary;
  if (!result.ok) {
    summary = `BUILD FAILED — ${errorLines.length} likely error line(s)${firstError ? `; first: ${firstError}` : ""}`;
  } else if (actionsExecuted != null && actionsExecuted > 0) {
    summary = `BUILD SUCCEEDED — ${actionsExecuted} action(s) — ${build.target} ${build.platform || "Win64"} ${build.configuration || "Development"}`;
  } else if (upToDate && actionsExecuted === 0) {
    summary = `BUILD SUCCEEDED (up to date — 0 files recompiled) — ${build.target} ${build.platform || "Win64"} ${build.configuration || "Development"}`;
  } else if (actionsExecuted === 0) {
    summary = `BUILD SUCCEEDED (compile proof unverified — action count not detected) — ${build.target} ${build.platform || "Win64"} ${build.configuration || "Development"}`;
  } else {
    summary = `BUILD SUCCEEDED — ${actionsExecuted} action(s) — ${build.target} ${build.platform || "Win64"} ${build.configuration || "Development"}`;
  }

  const payload = {
    summary,
    ok: Boolean(result.ok),
    exitCode: result.exitCode,
    upToDate,
    actionsExecuted,
    declaredTotalActions: proof.declaredTotalActions,
    observedCompileLines: proof.compileLineCount,
    observedLinkLines: proof.linkLineCount,
    highestObservedActionIndex: proof.highestObservedActionIndex,
    proofLevel,
    responseMode: verbose ? "verbose" : "compact",
    likelyErrors: responseErrorLines,
    fullLogPath: logPath,
    error: result.error || "",
    nextSteps: [],
    suggestedToolCalls: [],
    phase: result.ok ? "complete" : "failed",
    userMessage: result.ok
      ? (upToDate && actionsExecuted === 0
        ? "Build finished (up to date — no files recompiled)"
        : `Build succeeded (${actionsExecuted ?? "?"} action(s))`)
      : `Build failed${firstError ? `: ${firstError}` : ""}`,
    userMessageKo: result.ok
      ? (upToDate && actionsExecuted === 0
        ? "빌드 완료 (최신 상태 — 재컴파일 없음)"
        : `빌드 성공 (${actionsExecuted ?? "?"} action(s))`)
      : `빌드 실패${firstError ? `: ${firstError}` : ""}`,
    cancellable: false
  };

  if (!result.ok) {
    payload.nextSteps = [
      "Fix only the first actionable compiler or linker error.",
      "If API evidence is still needed, run unreal_rag_search with mode=compile_fix at most once using the compact first error.",
      "Rebuild after the smallest patch."
    ];
    if (firstError) {
      payload.suggestedToolCalls = [{
        tool: "unreal_rag_search",
        args: { query: firstError.slice(0, 360), mode: "compile_fix", hybrid: false, top_k: 4 }
      }];
    }
  } else if (upToDate && actionsExecuted === 0) {
    payload.nextSteps = [
      "upToDate=true means UBT did not recompile any files — this is not proof your recent edit was built.",
      "If you just edited C++, confirm the file was saved, then rebuild and check fullLogPath for action count > 0.",
      `Report proofLevel=${proofLevel} with fullLogPath as evidence.`
    ];
  } else if (hasCompileEvidence) {
    payload.nextSteps = [
      `Compile/link evidence detected (${proof.compileLineCount || 0} compile, ${proof.linkLineCount || 0} link lines).`,
      "Inspect fullLogPath if runtime verification is still required.",
      `Report proofLevel=${proofLevel} with fullLogPath as evidence.`
    ];
  } else {
    payload.nextSteps = [
      "Compile action count was not detected in the build summary.",
      "Inspect fullLogPath manually; if you find compile/link lines, you may report proofLevel=Built.",
      "Otherwise stay at proofLevel=BuiltUnverified until compile proof is visible.",
      `Report proofLevel=${proofLevel} with fullLogPath as evidence.`
    ];
  }

  if (verbose) {
    payload.command = command;
    payload.autoDetected = {
      selectionReason: planResult.selectionReason,
      engineRoot: build.engineRoot,
      engineSource: build.engineSource,
      engineWarning: build.engineWarning || null,
      requestedEngineAssociation: build.requestedEngineAssociation || null,
      projectPath,
      projectFile: path.basename(projectPath),
      target: build.target,
      platform: build.platform || "Win64",
      configuration: build.configuration || "Development",
      allTargets: build.allTargets
    };
    payload.stdout = result.stdout || "";
    payload.stderr = result.stderr || "";
  } else {
    payload.autoDetected = {
      projectFile: path.basename(projectPath),
      target: build.target,
      platform: build.platform || "Win64",
      configuration: build.configuration || "Development"
    };
  }

  return payload;
}

function isInterestingLogLine(line) {
  return (
    /\berror\s+(C\d+|LNK\d+|MSB\d+|UHT\d*)\b/i.test(line)
    || /\bfatal error\b/i.test(line)
    || /\bassert(?:ion)? failed\b/i.test(line)
    || /\bensure condition failed\b/i.test(line)
    || /\bUnhandled Exception\b/i.test(line)
    || /\bUnhandled\s+\d+\s+aggregate exceptions?\b/i.test(line)
    || /\bOtherCompilationError\b/i.test(line)
    || /\bLog\w+:\s*Error:/i.test(line)
    || /\berror:/i.test(line)
  );
}

function firstErrorCluster(lines, radius = 4, maxLines = 30) {
  const source = Array.isArray(lines) ? lines : [];
  const index = source.findIndex(isInterestingLogLine);
  if (index < 0) return source.slice(-Math.min(maxLines, source.length));
  const start = Math.max(0, index - radius);
  return source.slice(start, Math.min(source.length, index + radius + 1, start + maxLines));
}

function compactLogPayload(payload, maxChars = DEFAULT_LOG_RESULT_MAX_CHARS) {
  let serialized = JSON.stringify(payload, null, 2);
  if (serialized.length <= maxChars) return payload;

  const compact = {
    ...payload,
    truncated: true,
    originalChars: serialized.length,
    logs: (payload.logs || []).map((log) => ({
      file: log.file,
      lineCount: log.lineCount,
      lines: firstErrorCluster(log.lines || [], 3, 24)
    }))
  };
  serialized = JSON.stringify(compact, null, 2);
  if (serialized.length <= maxChars) return compact;

  compact.logs = compact.logs.slice(0, 1).map((log) => ({
    ...log,
    lines: log.lines.slice(0, 12)
  }));
  return compact;
}

async function writeTextArtifact(workspaceRoot, relativePath, text) {
  const target = path.join(workspaceRoot, relativePath);
  atomicWriteText(target, String(text || ""));
  return path.relative(workspaceRoot, target).replace(/\\/g, "/");
}

function sanitizeHandoffList(values, maxItems) {
  if (!Array.isArray(values)) return [];
  return values
    .slice(0, maxItems)
    .map((value) => String(value || "").replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

function formatSessionHandoff(args = {}) {
  const summary = String(args.summary || "").replace(/\s+/g, " ").trim().slice(0, 500);
  if (!summary) throw new Error("summary is required");
  const changedFiles = sanitizeHandoffList(args.changedFiles, 12);
  const openErrors = sanitizeHandoffList(args.openErrors, 5);
  const nextSteps = sanitizeHandoffList(args.nextSteps, 3);
  const avoidRepeating = sanitizeHandoffList(args.avoidRepeating, 3);
  const lines = [
    "# LM Studio Session Handoff",
    `Summary: ${summary}`,
    `Changed: ${changedFiles.length ? changedFiles.join(", ") : "none"}`,
    `Open errors: ${openErrors.length ? openErrors.join(" | ") : "none"}`,
    `Next: ${nextSteps.length ? nextSteps.join(" -> ") : "review this handoff and choose the smallest next step"}`,
    `Do not repeat: ${avoidRepeating.length ? avoidRepeating.join(" | ") : "none recorded"}`,
    "Resume: paste prompts/lmstudio_session_bootstrap.md, then ask the model to read .agent/handoff/latest.md."
  ];
  return lines.join("\n") + "\n";
}

module.exports = {
  DEFAULT_AGENT_RESULT_MAX_CHARS,
  DEFAULT_BUILD_ERROR_LINES,
  DEFAULT_LOG_RESULT_MAX_CHARS,
  DEFAULT_VALIDATION_FINDING_CAP,
  buildResponsePayload,
  clampInt,
  compactLogPayload,
  compactMcpContent,
  compactValidationPayload,
  errorPayload,
  extractLikelyCompileErrors,
  compactCompilerDiagnostic,
  firstErrorCluster,
  formatSessionHandoff,
  parseBuildExecutionSummary,
  resolveAgentResultMaxChars,
  slimWriteSuccessPayload,
  truncateUtf8,
  validationFindingMeta,
  writeDisciplineOptions,
  writeTextArtifact
};
