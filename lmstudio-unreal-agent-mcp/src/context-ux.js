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
  if (!validation) return null;
  const rawFindings = validation.skipped
    ? (validation.advisoryFindings || validation.findings || [])
    : (validation.findings || []);
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
  const severityRank = { error: 0, warning: 1, info: 2 };
  const prioritized = grouped
    .map((item, index) => ({ item, index }))
    .sort((a, b) => (
      (severityRank[String(a.item.severity || "").toLowerCase()] ?? 3)
      - (severityRank[String(b.item.severity || "").toLowerCase()] ?? 3)
      || a.index - b.index
    ))
    .map(({ item }) => item);
  const findings = prioritized.slice(0, maxFindings);
  const omittedFindingCount = Math.max(0, grouped.length - findings.length);
  const rawErrorCount = grouped.filter((item) => item.severity === "error").length;
  const blockingErrorCount = validation.skipped ? 0 : rawErrorCount;
  const advisoryErrorCount = validation.skipped ? rawErrorCount : 0;
  const warningCount = grouped.filter((item) => item.severity === "warning").length;
  const infoCount = grouped.filter((item) => item.severity === "info").length;
  const shownBlockingErrorCount = findings.filter((item) => item.severity === "error").length;
  const omittedBlockingErrorCount = validation.skipped
    ? 0
    : Math.max(0, blockingErrorCount - shownBlockingErrorCount);
  const groups = [...new Set(grouped.map((item) => item.group))];
  return {
    ok: validation.ok !== false,
    findingCount: validation.findingCount || grouped.length,
    findings,
    omittedFindingCount,
    omittedBlockingErrorCount,
    blockingErrorCount,
    advisoryErrorCount,
    warningCount,
    infoCount,
    advisoryOnly: blockingErrorCount === 0,
    groups,
    deferredCount: validation.deferredCount || 0,
    preExistingCount: validation.preExistingCount || 0,
    skipped: Boolean(validation.skipped),
    infrastructureError: Boolean(validation.infrastructureError),
    timedOut: Boolean(validation.timedOut),
    note: validation.note || (omittedFindingCount
      ? `${omittedFindingCount} more finding(s) omitted${omittedBlockingErrorCount ? `, including ${omittedBlockingErrorCount} blocking error(s)` : ""}; run static_validate_project with narrower scope for the full list.`
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
    validationPassed: validation ? validation.skipped !== true && validation.ok !== false : null,
    workflowComplete: validation ? validation.skipped !== true && validation.ok !== false : null,
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
      advisoryErrorCount: compact.advisoryErrorCount,
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
      skipped: compact.skipped,
      infrastructureError: compact.infrastructureError,
      note: compact.note
    };
  }
  if (validation && validation.skipped) {
    payload.validationSummary = payload.validationSummary || { ok: true };
    payload.validationPassed = false;
    payload.workflowComplete = false;
    payload.validationSummary.note = validation.note
      || "validation skipped; run static_validate_project before build";
  }
  return payload;
}

const { parseBuildProof } = require("./build-proof");

function extractLikelyCompileErrors(stdout, stderr, maxLines = DEFAULT_BUILD_ERROR_LINES) {
  const combined = `${stdout || ""}\n${stderr || ""}`;
  const uhtWarningsAreErrors = /UnrealHeaderTool[^\r\n]*-WarningsAsErrors/i.test(combined)
    || /Running Internal UnrealHeaderTool[^\r\n]*-WarningsAsErrors/i.test(combined);
  const lines = combined.split(/\r?\n/);
  // Apple/Clang prints the useful linker diagnostics as a block whose symbol
  // rows do not contain the word "error". Preserve those rows as compact,
  // deterministic diagnostics before the generic final clang++ failure.
  const undefinedSymbols = [];
  let inUndefinedSymbolBlock = false;
  for (const line of lines) {
    if (/^Undefined symbols for architecture\b/i.test(String(line).trim())) {
      inUndefinedSymbolBlock = true;
      continue;
    }
    if (!inUndefinedSymbolBlock) continue;
    const match = String(line).match(/^\s*"(.+)",\s+referenced from:\s*$/);
    if (match) {
      undefinedSymbols.push(`Undefined symbol: ${match[1]}`);
      continue;
    }
    if (/^\s*(?:ld:|clang\+\+:|Result:|Total time)/i.test(String(line))) {
      inUndefinedSymbolBlock = false;
    }
  }
  const interesting = lines.filter((line) => (
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
  return [...undefinedSymbols, ...interesting]
    .slice(0, clampInt(maxLines, DEFAULT_BUILD_ERROR_LINES, 1, 120));
}

function firstUsefulLine(lines) {
  return (lines || []).find((line) => String(line).trim()) || "";
}

function compactCompilerDiagnostic(line, maxChars = 360) {
  let value = String(line || "").replace(/\s+/g, " ").trim();
  if (!value) return "";

  // Keep a portable basename/line coordinate instead of leaking a long,
  // machine-specific absolute path into the next model prompt.
  const portableSource = value.match(
    /(?:^|[\\/])((?:Source|Plugins)[\\/].+?\.(?:cpp|c|cc|cxx|h|hpp)(?:\(\d+(?:,\d+)?\)|:\d+(?::\d+)?))(?=:\s*(?:fatal\s+)?error\b)/i
  );
  const source = portableSource || value.match(
    /(?:^|[\\/])([^\\/]+\.(?:cpp|c|cc|cxx|h|hpp)(?:\(\d+(?:,\d+)?\)|:\d+(?::\d+)?))(?=:\s*(?:fatal\s+)?error\b)/i
  );
  if (source && Number.isInteger(source.index)) {
    value = value.slice(source.index + source[0].length - source[1].length);
    value = value.replace(/\\/g, "/");
  }

  // A compact query needs the stable ASCII error code and C++ symbols. Localized
  // prose remains available in fullLogPath (and verbose output) after decoding.
  const firstNonAscii = value.search(/[^\x09\x20-\x7e]/);
  if (firstNonAscii >= 0) value = value.slice(0, firstNonAscii).trim();
  value = value.replace(/\ufffd+/g, " ").replace(/\s+/g, " ").replace(/\?+$/, "").trim();
  return value.slice(0, Math.max(80, Number(maxChars) || 360));
}

function compilerDiagnosticDetails(line) {
  const compact = compactCompilerDiagnostic(line);
  const undefinedSymbolMatch = compact.match(/^Undefined symbol:\s*(.+)$/i);
  const codeMatch = compact.match(/\b(?:fatal\s+)?error\s+([A-Z]+\d+)\b/i);
  const locationMatch = compact.match(
    /^(.+\.(?:cpp|c|cc|cxx|h|hpp))(?:(?:\((\d+)(?:,(\d+))?\))|(?::(\d+)(?::(\d+))?))/i
  );
  const quoted = [];
  const quotedPattern = /'([^']+)'|"([^"]+)"/g;
  let match;
  while ((match = quotedPattern.exec(compact)) !== null) {
    quoted.push(String(match[1] || match[2] || "").trim());
  }
  return {
    compact,
    diagnosticCode: codeMatch ? codeMatch[1].toUpperCase() : "",
    targetFile: locationMatch ? locationMatch[1] : "",
    targetLine: locationMatch ? Number(locationMatch[2] || locationMatch[4]) : null,
    targetColumn: locationMatch && (locationMatch[3] || locationMatch[5])
      ? Number(locationMatch[3] || locationMatch[5])
      : null,
    quoted: quoted.filter(Boolean),
    linkerSymbol: undefinedSymbolMatch ? undefinedSymbolMatch[1].trim() : "",
  };
}

function symbolLeaf(value) {
  const normalized = String(value || "").trim().replace(/\(\s*\)$/, "");
  const leaf = normalized.split("::").filter(Boolean).pop() || normalized;
  return leaf.replace(/^[*&\s]+|[&*\s]+$/g, "");
}

function qualifiedCppSymbol(value) {
  const normalized = String(value || "")
    .replace(/\b(?:class|struct|enum)\s+/g, "")
    .replace(/\s+/g, " ")
    .trim();
  const matches = [...normalized.matchAll(/\b([A-Za-z_]\w*)::([~A-Za-z_]\w*)\s*\(/g)];
  if (!matches.length) return { ownerSymbol: "", missingSymbol: "" };
  const match = matches[0];
  return {
    ownerSymbol: String(match[1] || ""),
    missingSymbol: String(match[2] || "").replace(/^~/, ""),
  };
}

function buildFailureRecovery(firstError) {
  const diagnostic = compilerDiagnosticDetails(firstError);
  const code = diagnostic.diagnosticCode;
  const firstQuoted = diagnostic.quoted[0] || "";
  const secondQuoted = diagnostic.quoted[1] || "";
  let category = "compile_error";
  let symbolQuery = "";
  let linkerIdentity = { ownerSymbol: "", missingSymbol: "" };

  if (diagnostic.linkerSymbol) {
    category = "linker_missing_definition";
    symbolQuery = symbolLeaf(diagnostic.linkerSymbol.replace(/\([^)]*\).*$/, ""));
    linkerIdentity = qualifiedCppSymbol(diagnostic.linkerSymbol);
  } else if (code === "C2039") {
    category = "missing_member";
    symbolQuery = symbolLeaf(firstQuoted);
  } else if (code === "C3861" || code === "C2065" || code === "C2061") {
    category = "unknown_symbol";
    symbolQuery = symbolLeaf(firstQuoted);
  } else if ([
    "C2660", "C2661", "C2664", "C2672", "C2780", "C2784", "C2893",
  ].includes(code)) {
    category = "api_signature";
    symbolQuery = symbolLeaf(firstQuoted);
  } else if (/^LNK\d+$/i.test(code)) {
    // MSVC reports unresolved externals as a quoted, decorated C++ signature
    // instead of the clang-style "Undefined symbol" block handled above.
    // Route the stable leaf symbol through the already-active symbol lookup;
    // falling back to compile-fix RAG here can contradict the executor route.
    const msvcSymbol = firstQuoted.replace(/\([^)]*\).*$/, "");
    symbolQuery = symbolLeaf(msvcSymbol);
    category = symbolQuery ? "linker_missing_definition" : "linker";
    linkerIdentity = qualifiedCppSymbol(firstQuoted);
  } else if (code === "C1083") {
    category = "include_or_module";
  } else if (/\b(?:UHT|UnrealHeaderTool|generated\.h)\b/i.test(diagnostic.compact)) {
    category = "uht_or_reflection";
  } else if (diagnostic.targetFile) {
    category = "source_compile_error";
  }

  const common = {
    protocolVersion: 1,
    state: "evidence_required",
    category,
    diagnosticCode: code || null,
    targetFile: diagnostic.targetFile || null,
    targetLine: diagnostic.targetLine,
    targetColumn: diagnostic.targetColumn,
    firstError: diagnostic.compact,
    stopIfNoNewEvidence: true,
  };

  if (symbolQuery) {
    const args = { query: symbolQuery, top_k: 8, detailLevel: "compact" };
    const linkerMissingDefinition = category === "linker_missing_definition";
    return {
      ...common,
      member: code === "C2039" ? symbolQuery : null,
      owner: code === "C2039" ? secondQuoted || null : null,
      ownerSymbol: linkerMissingDefinition ? linkerIdentity.ownerSymbol || null : null,
      missingSymbol: linkerMissingDefinition
        ? linkerIdentity.missingSymbol || symbolQuery || null
        : null,
      semanticEvidenceRequired: linkerMissingDefinition,
      mutationPermittedWithoutSemanticEvidence: !linkerMissingDefinition,
      semanticEvidenceSources: linkerMissingDefinition
        ? ["exact declaration", "project call sites or collaborating state", "tests or requirements"]
        : [],
      requiredNextTool: "unreal_symbol_lookup",
      requiredNextToolArgs: args,
      requiredSequence: [
        "unreal_symbol_lookup",
        ...(linkerMissingDefinition ? ["unreal_agent_plan"] : []),
        "read_file_range",
        ...(linkerMissingDefinition ? ["unreal_code_sketch_claim_validate"] : []),
        "replace_in_file",
        "static_validate_project",
        "build_unreal_project",
      ],
      forbiddenUntilMutation: linkerMissingDefinition
        ? ["unreal_rag_search"]
        : ["unreal_rag_search", "unreal_agent_plan"],
      maxEvidenceCallsBeforeMutation: 2,
    };
  }

  if (diagnostic.targetFile && diagnostic.targetLine) {
    const args = {
      path: diagnostic.targetFile,
      startLine: Math.max(1, diagnostic.targetLine - 15),
      endLine: diagnostic.targetLine + 15,
      detailLevel: "compact",
    };
    return {
      ...common,
      requiredNextTool: "read_file_range",
      requiredNextToolArgs: args,
      requiredSequence: [
        "read_file_range",
        "unreal_code_sketch_claim_validate",
        "replace_in_file",
        "static_validate_project",
        "build_unreal_project",
      ],
      forbiddenUntilMutation: ["unreal_agent_plan"],
      maxEvidenceCallsBeforeMutation: 1,
      rebindTargetBeforeMutation: true,
    };
  }

  const args = {
    query: diagnostic.compact.slice(0, 360),
    mode: "compile_fix",
    hybrid: false,
    top_k: 4,
    detailLevel: "compact",
  };
  return {
    ...common,
    requiredNextTool: "unreal_rag_search",
    requiredNextToolArgs: args,
    requiredSequence: [
      "unreal_rag_search",
      "read_file_range",
      "replace_in_file",
      "static_validate_project",
      "build_unreal_project",
    ],
    forbiddenUntilMutation: ["unreal_agent_plan"],
    maxEvidenceCallsBeforeMutation: 2,
  };
}

function buildToolDisposition(payload = {}) {
  if (payload.ok) {
    return {
      buildOutcome: "succeeded",
      toolExecutionSucceeded: true,
      recoverable: false,
      mcpIsError: false,
    };
  }
  const likelyErrors = Array.isArray(payload.likelyErrors) ? payload.likelyErrors : [];
  const compileFailed = (
    payload.phase !== "stale"
    && !payload.timedOut
    && !String(payload.errorCode || "").trim()
    && !String(payload.error || "").trim()
    && likelyErrors.length > 0
  );
  return {
    buildOutcome: compileFailed ? "compile_failed" : "tool_failed",
    toolExecutionSucceeded: compileFailed,
    recoverable: compileFailed,
    mcpIsError: !compileFailed,
  };
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
    timedOut: Boolean(result.timedOut),
    errorCode: result.errorCode || "",
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
    const diagnostic = compilerDiagnosticDetails(firstError);
    const actionable = firstError && (
      Boolean(diagnostic.targetFile && diagnostic.targetLine)
      || Boolean(diagnostic.linkerSymbol)
      || /\b(?:fatal\s+)?error\s+[A-Z]+\d+\b/i.test(firstError)
      || /\b(?:UHT|UnrealHeaderTool|generated\.h)\b/i.test(firstError)
      || /\.[ch](?:pp)?\(\d+(?:,\d+)?\).*\bWarning:/i.test(firstError)
    );
    if (actionable) {
      const recovery = buildFailureRecovery(firstError);
      // A same-file incomplete-type diagnostic means the repair commonly
      // needs both the include preamble and the failing definition. For small
      // source files, return that evidence in the single permitted recovery
      // read instead of forcing the model to guess an include it was forbidden
      // to inspect.
      const relatedDiagnostics = compactErrorLines
        .map((line) => ({ line, details: compilerDiagnosticDetails(line) }))
        .filter(({ details }) => (
          recovery.targetFile
          && details.targetFile === recovery.targetFile
          && Number(details.targetLine || 0) > 0
        ));
      const highestRelatedLine = relatedDiagnostics.reduce(
        (highest, item) => Math.max(highest, Number(item.details.targetLine || 0)),
        Number(recovery.targetLine || 0),
      );
      const needsIncludePreamble = relatedDiagnostics.some(({ line }) => (
        /\b(?:incomplete type|unknown type name|does not name a type)\b/i.test(line)
      ));
      if (
        needsIncludePreamble
        && recovery.requiredNextTool === "read_file_range"
        && highestRelatedLine > 0
        && highestRelatedLine + 15 <= 150
      ) {
        recovery.requiredNextToolArgs = {
          ...recovery.requiredNextToolArgs,
          startLine: 1,
          endLine: highestRelatedLine + 15,
        };
        recovery.includesSourcePreamble = true;
      }
      payload.recovery = recovery;
      payload.requiredNextTool = recovery.requiredNextTool;
      payload.requiredNextToolArgs = recovery.requiredNextToolArgs;
      payload.nextSteps = recovery.category === "linker_missing_definition"
        ? [
          "Look up the first undefined symbol exactly once with requiredNextToolArgs.",
          "Replan one owning implementation file, then read the exact declaration and existing project collaborators that define its behavior.",
          "Validate a bounded sketch before mutation. A missing definition proves only that code is absent; do not invent persistent state, thresholds, defaults, or gameplay policy.",
          "Rebuild only after a mutation; a new linker symbol starts a new recovery state."
        ]
        : [
          "Call " + recovery.requiredNextTool + " exactly once with requiredNextToolArgs; do not substitute another evidence tool.",
          "After that read, validate a bounded code sketch for recovery.targetFile so the server can rebind the active slice, then apply the smallest mutation and run static_validate_project.",
          "Rebuild only after a mutation; a new compiler error starts a new recovery state."
        ];
      payload.suggestedToolCalls = [{
        tool: recovery.requiredNextTool,
        args: recovery.requiredNextToolArgs,
      }];
    } else {
      payload.nextSteps = [
        "No actionable compiler diagnostic was extracted.",
        "Inspect fullLogPath once; do not guess an API or alternate evidence tools.",
      ];
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

  const disposition = buildToolDisposition(payload);
  payload.buildOutcome = disposition.buildOutcome;
  payload.toolExecutionSucceeded = disposition.toolExecutionSucceeded;
  payload.recoverable = disposition.recoverable;
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

  if (payload?.responseMode === "range") {
    // Never compact away range lines while retaining a cursor beyond them.
    // Reset continuation to the requested cursor so a smaller retry remains
    // lossless and deterministic.
    return {
      ...payload,
      summary: "RANGE RESPONSE TOO LARGE — retry with maxFiles=1 and a smaller maxBytes value.",
      truncated: true,
      originalChars: serialized.length,
      exactTraversalPreserved: true,
      rangeRetryRequired: true,
      suggestedRangeArgs: { maxFiles: 1, maxBytes: 4096 },
      logs: (payload.logs || []).map((log) => ({
        file: log.file,
        lineCount: 0,
        lines: [],
        sourceBytes: log.sourceBytes,
        bytesRead: 0,
        bytesReturned: 0,
        sourceTruncated: Number(log.cursorByte || 0) > 0
          || Number(log.cursorByte || 0) < Number(log.sourceBytes || 0),
        mode: "range",
        cursorByte: log.cursorByte,
        contentStartByte: log.cursorByte,
        contentEndByte: log.cursorByte,
        nextCursorByte: log.cursorByte,
        hasMore: Number(log.cursorByte || 0) < Number(log.sourceBytes || 0),
        lineLimited: false,
        rangeRetryRequired: true,
      })),
    };
  }

  const compact = {
    ...payload,
    truncated: true,
    originalChars: serialized.length,
    logs: (payload.logs || []).map((log) => ({
      file: log.file,
      lineCount: log.lineCount,
      sourceBytes: log.sourceBytes,
      bytesRead: log.bytesRead,
      sourceTruncated: Boolean(log.sourceTruncated),
      mode: log.mode,
      cursorByte: log.cursorByte,
      nextCursorByte: log.nextCursorByte,
      hasMore: Boolean(log.hasMore),
      firstErrorFound: log.firstErrorFound,
      scanTruncated: Boolean(log.scanTruncated),
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
  buildFailureRecovery,
  buildResponsePayload,
  buildToolDisposition,
  clampInt,
  compilerDiagnosticDetails,
  compactLogPayload,
  compactMcpContent,
  compactValidationPayload,
  errorPayload,
  extractLikelyCompileErrors,
  compactCompilerDiagnostic,
  firstErrorCluster,
  isInterestingLogLine,
  formatSessionHandoff,
  parseBuildExecutionSummary,
  resolveAgentResultMaxChars,
  slimWriteSuccessPayload,
  truncateUtf8,
  validationFindingMeta,
  writeDisciplineOptions,
  writeTextArtifact
};
