"use strict";

const crypto = require("node:crypto");

const COMPACTION_SCHEMA_VERSION = 2;
const DEFAULT_COMPACTION_CONFIG = Object.freeze({
  enabled: true,
  observeOnly: false,
  softRemainingTokens: 14000,
  hardRemainingTokens: 8000,
  maxOutputReserve: 4096,
  safetyMarginTokens: 1024,
  normalToolResultReserve: 3000,
  buildToolResultReserve: 8000,
  recentCompleteTurns: 1,
  minimumTurnsBetweenCompactions: 0,
  targetRemainingTokensAfterCompaction: 24000,
  maxCheckpointFacts: 32,
});

function stableStringify(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
}

function sha256(value) {
  return crypto.createHash("sha256").update(String(value || ""), "utf8").digest("hex");
}

function textOf(message) {
  if (!message) return "";
  if (typeof message === "string") return message;
  if (typeof message.text === "string") return message.text;
  if (typeof message.content === "string") return message.content;
  if (typeof message.getText === "function") {
    try { return String(message.getText() || ""); } catch { return ""; }
  }
  return "";
}

function roleOf(message) {
  if (!message) return "unknown";
  if (typeof message.role === "string") return message.role;
  if (typeof message.getRole === "function") {
    try { return String(message.getRole() || "unknown"); } catch { return "unknown"; }
  }
  return "unknown";
}

function toolRequestsOf(message) {
  if (Array.isArray(message?.toolCalls)) return message.toolCalls;
  if (typeof message?.getToolCallRequests === "function") {
    try { return message.getToolCallRequests() || []; } catch { return []; }
  }
  return [];
}

function toolResultsOf(message) {
  if (Array.isArray(message?.toolResults)) return message.toolResults;
  if (typeof message?.getToolCallResults === "function") {
    try { return message.getToolCallResults() || []; } catch { return []; }
  }
  return [];
}

function toolResultContent(result) {
  // The MCP control envelope lives in structuredContent. Prefer it over the
  // intentionally concise text projection so compaction never has to infer
  // protocol state from prose.
  const raw = (
    result?.structuredContent && typeof result.structuredContent === "object"
      ? result.structuredContent
      : result?.content ?? result?.result ?? ""
  );
  if (typeof raw === "string") {
    const source = raw.trim();
    if ((source.startsWith("[") || source.startsWith("{")) && source.length > 1) {
      try {
        const parsed = JSON.parse(source);
        const isTransportBlock = Array.isArray(parsed)
          ? parsed.some((block) => block && typeof block === "object" && ["text", "resource"].includes(block.type))
          : parsed && typeof parsed === "object" && ["text", "resource"].includes(parsed.type);
        if (isTransportBlock) return toolResultContent({ content: parsed });
      } catch { /* ordinary source/text result */ }
    }
    return raw;
  }
  if (Array.isArray(raw)) {
    return raw.map((block) => {
      if (typeof block === "string") return block;
      if (typeof block?.text === "string") return block.text;
      if (typeof block?.content === "string") return block.content;
      try { return JSON.stringify(block); } catch { return ""; }
    }).filter(Boolean).join("\n");
  }
  if (raw && typeof raw === "object") {
    if (typeof raw.text === "string") return raw.text;
    try { return JSON.stringify(raw); } catch { return ""; }
  }
  return String(raw || "");
}

function messageSnapshot(message) {
  return {
    role: roleOf(message),
    text: textOf(message),
    toolCalls: toolRequestsOf(message).map((call) => ({
      id: call.id || null,
      name: call.name || "",
      arguments: call.arguments || {},
    })),
    toolResults: toolResultsOf(message).map((result) => ({
      toolCallId: result.toolCallId || null,
      name: result.name || "",
      content: toolResultContent(result),
      isError: result.isError === true,
    })),
  };
}

function snapshotMessages(messages) {
  return (messages || []).map(messageSnapshot);
}

function parseJsonObjects(text) {
  const values = [];
  const parseNested = (candidate, depth = 0) => {
    if (depth > 4 || candidate == null) return;
    if (Array.isArray(candidate)) {
      for (const item of candidate) parseNested(item, depth + 1);
      return;
    }
    if (candidate && typeof candidate === "object") {
      // LM Studio persists MCP text blocks as a JSON-encoded array inside the
      // tool result's content string. The block is transport, not evidence;
      // recursively parse its text to recover the actual MCP payload.
      if (candidate.type === "text" && typeof candidate.text === "string") {
        parseNested(candidate.text, depth + 1);
        return;
      }
      values.push(candidate);
      return;
    }
    const source = String(candidate || "").trim();
    if (!source) return;
    try {
      parseNested(JSON.parse(source), depth + 1);
    } catch {
      const matches = source.match(/\{[\s\S]*\}/g) || [];
      for (const match of matches.slice(-4)) {
        try {
          parseNested(JSON.parse(match), depth + 1);
        } catch { /* text is not JSON; keep the raw message */ }
      }
    }
  };
  parseNested(text);
  return values;
}

function toolResultSucceeded(result) {
  if (result?.isError === true) return false;
  const payloads = parseJsonObjects(result?.content);
  for (const payload of payloads) {
    if (
      payload.isError === true
      || payload.ok === false
      || payload.toolExecutionSucceeded === false
      || payload.phase === "failed"
      || payload.validationProofPassed === false
      || payload.validationPassed === false
      || payload.buildAllowedForValidatedGeneration === false
    ) {
      return false;
    }
    const validationSummary = payload.validationSummary;
    if (validationSummary && (validationSummary.ok === false || validationSummary.skipped === true)) {
      return false;
    }
    if (typeof payload.buildOutcome === "string" && /fail|error/i.test(payload.buildOutcome)) {
      return false;
    }
  }
  // Plain-text tool results are successful unless the transport or structured
  // payload explicitly marks them as failed. This preserves compatibility with
  // MCP tools that return human-readable output instead of JSON.
  return true;
}

function isNonToolNextAction(_value) {
  // Kept as a compatibility export for the generator. New checkpoints only
  // persist actions whose server-owned control.nextActionIsTool is true, so a
  // duplicated sentinel allowlist is neither needed nor authoritative.
  return false;
}

const ARCHITECTURE_CONTROL_STATES = new Set([
  "Discovery",
  "InitialProposal",
  "FullReplan",
  "EvidenceRefill",
  "ExactRepair",
  "Revalidation",
  "Validated",
  "FailedClosed",
]);

function compactProtocolControl(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (Number(value.version || 0) < 1) return null;
  return {
    version: Number(value.version),
    taskId: String(value.taskId || "").slice(0, 160),
    phase: String(value.phase || "").slice(0, 160),
    status: String(value.status || "").slice(0, 160),
    nextAction: String(value.nextAction || "").slice(0, 160),
    nextActionIsTool: value.nextActionIsTool === true,
    retryPolicy: String(value.retryPolicy || "none").slice(0, 40),
    blockerFingerprint: String(value.blockerFingerprint || "").slice(0, 160),
    continuationToken: String(value.continuationToken || "").slice(0, 160),
  };
}

function compactTaskRouteOwnership(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const taskSessionId = String(value.taskSessionId || "").trim();
  const ownerCapability = String(value.ownerCapability || "").trim();
  if (!taskSessionId || !ownerCapability) return null;
  return { taskSessionId, ownerCapability };
}

function normalizeToolNames(value, sourceTool = "") {
  const candidates = Array.isArray(value)
    ? value
    : (value === true ? [sourceTool] : (typeof value === "string" ? [value] : []));
  return [...new Set(candidates
    .map((item) => String(item || "").trim())
    .filter((item) => /^[a-z][a-z0-9_-]{1,160}$/i.test(item)))];
}

function collectSemanticBlockerFields(value, state, sourceTool = "") {
  if (!value || typeof value !== "object" || Array.isArray(value)) return;
  const control = compactProtocolControl(value.control);
  const retryTargets = normalizeToolNames(value.doNotRetry, sourceTool);
  const explicitForbiddenTools = normalizeToolNames(value.doNotRetryTools, sourceTool);
  const errorCode = String(value.errorCode || "");
  const stopCurrentWorkflow = value.stopCurrentWorkflow === true;
  const evidencePhaseBoundary = (
    (value.stopCurrentPhase === true && String(value.phaseBoundary || "").toLowerCase() === "evidence")
    || /^EVIDENCE_STAGNATION(?:_REPEAT)?$/i.test(errorCode)
  );
  const forbiddenTools = [...new Set([
    ...explicitForbiddenTools,
    ...((evidencePhaseBoundary || stopCurrentWorkflow) ? retryTargets : []),
  ])];
  const requiredNextTool = String(value.requiredNextTool || (
    value.nextActionIsTool === true ? value.nextAction : ""
  ) || "").trim();
  const handoffBoundary = Boolean(requiredNextTool && explicitForbiddenTools.length > 0);

  // retryPolicy=forbidden is often derived from retryable=false and does not
  // mean an entire tool family is forbidden. READ_REPEAT_DETECTED and corrected
  // write retries must remain possible with different arguments.
  if (!evidencePhaseBoundary && !handoffBoundary && !(stopCurrentWorkflow && forbiddenTools.length > 0)) return;

  const scope = evidencePhaseBoundary
    ? "evidence_phase"
    : (handoffBoundary ? "until_required_tool_success" : "workflow");

  const prior = state.semanticBlocker && typeof state.semanticBlocker === "object"
    ? state.semanticBlocker
    : {};
  const preserveEvidencePhase = (
    prior.active === true
    && prior.scope === "evidence_phase"
    && evidencePhaseBoundary
  );
  state.semanticBlocker = {
    active: true,
    scope: preserveEvidencePhase ? prior.scope : scope,
    errorCode: String(preserveEvidencePhase ? prior.errorCode : (errorCode || prior.errorCode || "")).slice(0, 120),
    blockerFingerprint: String(
      control?.blockerFingerprint || value.blockerFingerprint || prior.blockerFingerprint || "",
    ).slice(0, 160),
    stopCurrentWorkflow: preserveEvidencePhase
      ? prior.stopCurrentWorkflow === true
      : stopCurrentWorkflow,
    stopCurrentPhase: preserveEvidencePhase
      ? prior.stopCurrentPhase === true
      : evidencePhaseBoundary,
    phaseBoundary: preserveEvidencePhase ? prior.phaseBoundary : (evidencePhaseBoundary ? "evidence" : ""),
    forbiddenTools: [...new Set([
      ...(preserveEvidencePhase && Array.isArray(prior.forbiddenTools) ? prior.forbiddenTools : []),
      ...forbiddenTools,
    ])].slice(-32),
    clearOnTool: String(preserveEvidencePhase ? prior.clearOnTool : (requiredNextTool || "")).slice(0, 160),
    clearOnToolArgs: preserveEvidencePhase
      ? (prior.clearOnToolArgs || null)
      : (requiredNextTool && value.requiredNextToolArgs && typeof value.requiredNextToolArgs === "object"
        ? value.requiredNextToolArgs
        : null),
    agentInstruction: String(
      preserveEvidencePhase
        ? prior.agentInstruction
        : (value.agentInstruction || value.userMessage || prior.agentInstruction || ""),
    ).slice(0, 800),
  };
}

function isContinuationUserMessage(text) {
  const source = String(text || "").trim();
  return /^(?:continue|resume|retry|keep\s+going|go\s+on|계속(?:해|해서|\s*진행(?:해|하세요)?|\s*작업(?:해|하세요)?)?|이어(?:서)?(?:\s*진행(?:해|하세요)?)?|재개(?:해|하세요)?|중단한\s*곳부터\s*(?:계속|진행)(?:해|하세요)?|다시\s*시도(?:해|하세요)?)[\s.!?]*$/i.test(source);
}

function mutationToolName(name) {
  const normalized = String(name || "").trim().toLowerCase();
  return ["replace_in_file", "write_file", "apply_edit_bundle"].some(
    (candidate) => normalized === candidate || normalized.endsWith(`_${candidate}`),
  );
}

function toolArgumentsSatisfy(requiredArgs, actualArgs) {
  if (!requiredArgs || typeof requiredArgs !== "object" || Array.isArray(requiredArgs)) return true;
  const actual = actualArgs && typeof actualArgs === "object" && !Array.isArray(actualArgs)
    ? actualArgs
    : {};
  const matches = (expected, received, key = "") => {
    if (key === "sessionId" || key === "session_id") return true;
    if (typeof expected === "string" && /^<[^>]+>$/.test(expected.trim())) return true;
    if (Array.isArray(expected)) {
      return Array.isArray(received)
        && expected.length === received.length
        && expected.every((item, index) => matches(item, received[index]));
    }
    if (expected && typeof expected === "object") {
      if (!received || typeof received !== "object" || Array.isArray(received)) return false;
      return Object.entries(expected).every(([childKey, child]) => (
        matches(child, received[childKey], childKey)
      ));
    }
    return stableStringify(received) === stableStringify(expected);
  };
  return Object.entries(requiredArgs).every(([key, expected]) => matches(expected, actual[key], key));
}

function boundedArchitecturePatchPreview(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const allowed = [
    "networking",
    "stateInventory",
    "lifecycleTransitions",
    "impactedSurfaces",
    "implementationFiles",
    "migrationPlan",
  ];
  const bound = (child, depth = 0) => {
    if (typeof child === "string") return child.slice(0, 300);
    if (typeof child === "number" || typeof child === "boolean" || child == null) return child;
    if (depth >= 4) return "[depth-truncated]";
    if (Array.isArray(child)) return child.slice(0, 10).map((item) => bound(item, depth + 1));
    if (typeof child === "object") {
      return Object.fromEntries(
        Object.entries(child).slice(0, 16).map(([key, item]) => [key, bound(item, depth + 1)]),
      );
    }
    return String(child).slice(0, 300);
  };
  const selected = Object.fromEntries(
    allowed.filter((key) => Object.hasOwn(value, key)).map((key) => [key, bound(value[key])]),
  );
  return Object.keys(selected).length ? selected : null;
}

function collectControlFields(value, state) {
  if (!value || typeof value !== "object") return;
  const protocolControl = compactProtocolControl(value.control);
  if (protocolControl) {
    state.protocolControl = protocolControl;
    if (
      ARCHITECTURE_CONTROL_STATES.has(protocolControl.status)
      || /architecture/i.test(protocolControl.phase)
    ) {
      state.architectureControl = protocolControl;
    }
  }
  if (typeof value.proposalRevision === "string" && value.proposalRevision.trim()) {
    const previous = state.architectureProposal || {};
    const nextErrorCode = String(value.errorCode || previous.lastErrorCode || "").slice(0, 120);
    const validation = value.proposalValidation && typeof value.proposalValidation === "object"
      ? value.proposalValidation
      : null;
    const repairs = validation && Array.isArray(validation.repairRequirements)
      ? validation.repairRequirements.slice(0, 24).map((row) => ({
        jsonPath: String(row?.jsonPath || "proposal").slice(0, 160),
        constraint: String(row?.constraint || "").slice(0, 500),
      }))
      : (previous.repairRequirements || []);
    state.architectureProposal = {
      ...previous,
      revision: value.proposalRevision.trim(),
      validationOk: validation ? validation.ok === true : previous.validationOk,
      proposalPatchApplied: value.proposalPatchApplied === true,
      repairRequirements: repairs,
      lastErrorCode: nextErrorCode,
      unchangedCorePaths: (
        nextErrorCode === "ARCHITECTURE_PROPOSAL_REPLAN_CORE_UNCHANGED"
        && Array.isArray(value.requiredChangedPaths)
      )
        ? value.requiredChangedPaths.slice(0, 24).map((path) => String(path).slice(0, 160))
        : [],
      requiredNextAction: String(
        value.requiredNextAction || previous.requiredNextAction || ""
      ).slice(0, 160),
      repairStrategy: String(
        validation?.repairStrategy || value.repairStrategy || previous.repairStrategy || ""
      ).slice(0, 80),
      stagedContractRequired: typeof validation?.designContract?.stagedImplementation === "boolean"
        ? validation.designContract.stagedImplementation
        : previous.stagedContractRequired === true,
      networkedContractRequired: typeof validation?.designContract?.networkedProposal === "boolean"
        ? validation.designContract.networkedProposal
        : previous.networkedContractRequired === true,
      requiresFullReplan: validation?.designContract?.requiresFullReplan === true
        || value.repairSubmission?.mode === "fullProposal",
      repairMode: String(value.repairSubmission?.mode || previous.repairMode || "").slice(0, 80),
      requiredRepairPaths: Array.isArray(value.repairSubmission?.requiredJsonPaths)
        ? value.repairSubmission.requiredJsonPaths.slice(0, 24).map((path) => String(path).slice(0, 160))
        : (previous.requiredRepairPaths || []),
      sourceSnapshotFingerprint: String(
        value.graphEvidence?.sourceSnapshotFingerprint
        || previous.sourceSnapshotFingerprint
        || ""
      ).slice(0, 96),
    };
  }
  const directActionIsTool = protocolControl
    ? protocolControl.nextActionIsTool === true
    : value.nextActionIsTool === true || Boolean(value.requiredNextTool);
  let directRequiredNextToolSeen = false;
  let directRequiredNextTool = null;
  let directAction = protocolControl?.nextAction || "";
  let directActionField = protocolControl?.nextAction ? "control.nextAction" : "";
  // Only requiredNextToolArgs are server-owned equality constraints. Ordinary
  // nextActionArgs are model-facing templates/defaults and may deliberately
  // contain placeholders or omit values the model must derive.
  let directArgs = value.requiredNextToolArgs && typeof value.requiredNextToolArgs === "object"
    ? value.requiredNextToolArgs
    : null;
  for (const [key, child] of Object.entries(value)) {
    if (key === "control") {
      continue;
    } else if (!protocolControl && key === "requiredNextTool") {
      directRequiredNextToolSeen = true;
      directRequiredNextTool = child;
    } else if (!protocolControl && ["requiredNextAction", "nextAction"].includes(key) && typeof child === "string") {
      const candidate = child.trim();
      if (/^[a-z][a-z0-9_]{2,}(?::[a-z0-9_-]+)?$/.test(candidate)) {
        if (!directAction || key === "nextAction") {
          directAction = candidate;
          directActionField = key;
        }
      }
    } else if (!protocolControl && key === "requiredNextToolArgs" && child && typeof child === "object") {
      directArgs = child;
    } else if (key === "taskRouteTerminal" && child === true) {
      state.taskRouteTerminal = true;
      state.toolRoute = null;
      state.taskRouteOwnership = null;
      state.requiredNextTool = null;
      state.requiredNextToolRef = null;
      state.requiredNextToolArgs = null;
    } else if (["taskAuthorization", "routeAuthorization"].includes(key)) {
      const ownership = compactTaskRouteOwnership(child);
      if (ownership && state.taskRouteTerminal !== true) state.taskRouteOwnership = ownership;
    } else if (key === "constraints" && Array.isArray(child)) {
      state.constraints.push(...child.filter((item) => typeof item === "string"));
    } else if (["diagnosticCode", "errorCode", "errorKey", "errorSubkind", "firstError"].includes(key) && child != null) {
      state.lastDiagnostics.push(`${key}=${String(child)}`.slice(0, 400));
    } else if (key === "signatureContract" && child && typeof child === "object") {
      state.exactSignatureContracts.push(child);
    } else if (["path", "file", "projectRelative", "projectPath"].includes(key) && typeof child === "string") {
      state.touchedPaths.push(child.replaceAll("\\", "/"));
    } else if (["activeProject", "uprojectPath", "projectFile"].includes(key) && typeof child === "string" && /\.uproject$/i.test(child)) {
      state.activeProject = child;
    } else if (key === "projectName" && typeof child === "string") {
      state.activeProjectName = child;
    } else if (key === "mutationGeneration" && Number.isFinite(Number(child))) {
      state.mutationGeneration = Math.max(state.mutationGeneration, Number(child));
    } else if (key === "buildOutcome" || key === "proofLevel" || key === "phase") {
      state.buildState[key] = child;
    } else if (key === "selectedSlice" && child && typeof child === "object") {
      state.selectedSlice = child;
    } else if (key === "sliceProgress" && child && typeof child === "object") {
      state.sliceProgress = child;
    } else if (key === "buildVerification" && child && typeof child === "object") {
      state.buildVerification = child;
    } else if (key === "toolRoute" && child && typeof child === "object") {
      if (state.taskRouteTerminal !== true) {
        state.toolRoute = {
          routeHash: child.routeHash || "",
          phase: child.phase || "",
          activeTools: Array.isArray(child.activeTools) ? child.activeTools.slice(0, 16) : [],
          selectedSlice: child.selectedSlice || null,
        };
      }
    } else if (["invariants", "acceptanceCriteria", "postconditions"].includes(key) && Array.isArray(child)) {
      state.invariants.push(...child.filter((item) => typeof item === "string"));
    } else if (["automationCoverage", "engineHeaderLookup", "coverageStatus", "coverage"].includes(key)) {
      state.coverageEvidence.push({ [key]: child });
    }
    collectControlFields(child, state);
  }
  // Parent control fields describe the action that must happen now. Reapply
  // them after recursion so nextActionArgs.requiredNextAction (the action
  // after a recovery checkpoint) cannot overwrite nextAction itself.
  if (directRequiredNextToolSeen) {
    if (directRequiredNextTool === null || directRequiredNextTool === false || directRequiredNextTool === "") {
      state.requiredNextTool = null;
      state.requiredNextToolRef = null;
      state.requiredNextToolArgs = null;
    } else if (typeof directRequiredNextTool === "string") {
      state.requiredNextTool = directRequiredNextTool;
      state.requiredNextToolRef = null;
    } else if (directRequiredNextTool && typeof directRequiredNextTool === "object") {
      const name = typeof directRequiredNextTool.name === "string"
        ? directRequiredNextTool.name
        : (typeof directRequiredNextTool.tool === "string" ? directRequiredNextTool.tool : "");
      if (name) {
        state.requiredNextTool = name;
        state.requiredNextToolRef = directRequiredNextTool;
      }
    }
  } else if (directAction) {
    if (!directActionIsTool) {
      // This is a server routing sentinel, not an MCP tool name. It means the
      // prior exact-tool gate is no longer applicable and any currently active
      // route tool may be selected.
      state.requiredNextTool = null;
      state.requiredNextToolRef = null;
      state.requiredNextToolArgs = null;
    } else {
      state.requiredNextTool = directAction.split(":", 1)[0];
      state.requiredNextToolRef = { sourceField: directActionField, value: directAction };
    }
  } else if (protocolControl) {
    // An enveloped completed response with no next action is authoritative and
    // clears any older nested/legacy action discovered during recursion.
    state.requiredNextTool = null;
    state.requiredNextToolRef = null;
    state.requiredNextToolArgs = null;
  }
  if (directArgs && state.requiredNextTool) state.requiredNextToolArgs = directArgs;
}

function semanticBlockerClearToolSucceeded(blocker, matchedCallName, matchedCall, payload) {
  if (!blocker || blocker.scope !== "until_required_tool_success") return false;
  if (!blocker.clearOnTool || !toolNamesMatch(blocker.clearOnTool, matchedCallName)) return false;
  const requiredArgs = blocker.clearOnToolArgs && typeof blocker.clearOnToolArgs === "object"
    ? blocker.clearOnToolArgs
    : null;
  if (requiredArgs) {
    const actualArgs = matchedCall?.arguments && typeof matchedCall.arguments === "object"
      ? matchedCall.arguments
      : {};
    if (!toolArgumentsSatisfy(requiredArgs, actualArgs)) return false;
  }
  const normalized = String(matchedCallName || "").toLowerCase();
  if (normalized.endsWith("search_files")) {
    return payload?.searchComplete === true || Array.isArray(payload?.results) || Array.isArray(payload?.fileNameResults);
  }
  return true;
}

function semanticAnchors(content) {
  const lines = String(content || "").replace(/^\[path-metadata:[^\n]*\]\r?\n?/, "")
    .replace(/^\[line-endings:[^\n]*\]\r?\n?/, "")
    .split(/\r?\n/);
  const ranked = [];
  const add = (index, score, line) => {
    const normalized = String(line || "").trim().replace(/\s+/g, " ");
    if (!normalized || normalized.startsWith("//")) return;
    ranked.push({ index, score, text: normalized.slice(0, 220) });
  };
  lines.forEach((line, index) => {
    const value = line.trim().replace(/^\d+\|/, "").trim();
    if (/IMPLEMENT_(?:SIMPLE|COMPLEX)_AUTOMATION_TEST|BEGIN_DEFINE_SPEC|END_DEFINE_SPEC|Describe\s*\(|It\s*\(/.test(value)) add(index, 110, value);
    else if (/^U(?:CLASS|STRUCT|ENUM|INTERFACE|FUNCTION)\b/.test(value)) add(index, 100, value);
    else if (/^(?:class|struct|enum(?:\s+class)?)\s+[A-Za-z_]/.test(value)) add(index, 95, value);
    else if (/^[A-Za-z_][\w:<>,*&\s]*::[~A-Za-z_]\w*\s*\(/.test(value)) add(index, 90, value);
    else if (/DOREPLIFETIME|HasAuthority\s*\(|_Implementation\s*\(|OnRep_|Server[A-Za-z_]*\s*\(/.test(value)) add(index, 85, value);
    else if (/^[A-Za-z_][\w:<>,*&\s]*\([^;{}]*\)\s*(?:const\s*)?;\s*$/.test(value)) add(index, 80, value);
    else if (/^UPROPERTY\b/.test(value)) add(index, 70, value);
    else if (/^(?:case\s+E\w+|return\s+E\w+|switch\s*\()/.test(value)) add(index, 65, value);
    else if (/^[A-Za-z_][\w:<>,*&\s]+\s+[A-Za-z_]\w*\s*(?:=\s*[^;]+)?;\s*$/.test(value)) add(index, 45, value);
  });
  const selected = ranked.sort((a, b) => b.score - a.score || a.index - b.index).slice(0, 12);
  selected.sort((a, b) => a.index - b.index);
  return selected.map((row) => `L${row.index + 1}: ${row.text}`);
}

function compactToolEvidence(call, payload, resultContent = "") {
  const name = String(call?.name || "");
  const args = call?.arguments && typeof call.arguments === "object" ? call.arguments : {};
  const normalized = name.toLowerCase();
  if (normalized.endsWith("get_active_project") || normalized === "get_workspace_info") {
    const activeProject = String(
      payload?.activeProject || payload?.uprojectPath || payload?.projectFile
      || payload?.details?.projectFile || ""
    );
    return activeProject ? {
      tool: name,
      activeProject,
      projectName: String(payload?.projectName || payload?.details?.projectName || ""),
      projectDir: String(payload?.projectDir || payload?.details?.projectDir || ""),
    } : null;
  }
  if (normalized.endsWith("search_files")) {
    const matches = [
      ...(Array.isArray(payload?.results) ? payload.results : []),
      ...(Array.isArray(payload?.fileNameResults) ? payload.fileNameResults : []),
    ];
    return {
      tool: name,
      query: String(args.query || "").slice(0, 160),
      path: String(args.path || payload?.path?.displayPath || payload?.path || "").slice(0, 260),
      resultCount: Array.isArray(payload?.results) ? payload.results.length : 0,
      fileNameResultCount: Array.isArray(payload?.fileNameResults) ? payload.fileNameResults.length : 0,
      searchComplete: payload?.searchComplete === true,
      matchedFiles: [...new Set(matches.map((row) => String(row?.file || "")).filter(Boolean))].slice(0, 12),
      cached: payload?.cached === true,
      repeatDetected: payload?.repeatDetected === true,
    };
  }
  if (normalized.endsWith("list_directory")) {
    const entries = Array.isArray(payload?.entries) ? payload.entries : [];
    return {
      tool: name,
      path: String(args.path || payload?.path?.displayPath || payload?.path || "").slice(0, 260),
      entryCount: entries.length,
      entries: entries.map((row) => String(row?.name || row?.path || row || "")).filter(Boolean).slice(0, 32),
    };
  }
  if (normalized.endsWith("read_file") || normalized.endsWith("read_file_range")) {
    const source = String(payload?.content || resultContent || "");
    const suppliedAnchors = Array.isArray(payload?.semanticAnchors)
      ? payload.semanticAnchors.filter((line) => typeof line === "string").slice(0, 16)
      : [];
    return {
      tool: name,
      path: String(args.path || payload?.path?.displayPath || payload?.path || "").slice(0, 260),
      startLine: Number(args.startLine || args.start || 0),
      endLine: Number(args.endLine || args.end || 0),
      lineCount: Number(payload?.cachedLineCount || (source ? source.split(/\r?\n/).length : 0)),
      evidenceHash: String(payload?.evidenceHash || payload?.contentHash || (source ? sha256(source) : "")).slice(0, 80),
      semanticAnchors: suppliedAnchors.length ? suppliedAnchors : semanticAnchors(source),
      repeatDetected: payload?.repeatDetected === true,
      readAttempts: Number(payload?.readAttempts || 1),
    };
  }
  return null;
}

function isReadOnlyUserGoal(text) {
  const source = String(text || "");
  const lower = source.toLowerCase();
  if (
    /수정은\s*하(?:지\s*)?마/.test(source)
    || /수정하지\s*말/.test(source)
    || /찾기만하고/.test(source)
    || /분석만/.test(source)
    || /보고만/.test(source)
  ) {
    return true;
  }
  return (
    /\b(?:do\s+not|don't|dont)\s+(?:fix|edit|patch|change|modify|write)\b/.test(lower)
    || /\b(?:no|without)\s+(?:fixes|edits|patches)\b/.test(lower)
    || /\bfind\s+bugs?\s+only\b/.test(lower)
    || /\banalysis only\b/.test(lower)
    || /\breport only\b/.test(lower)
  );
}

function isMetaUserMessage(text) {
  const source = String(text || "");
  const lower = source.toLowerCase();
  // LM Studio auto-names chats by injecting a synthetic user prompt mid-turn.
  // Treating it as a real goal wipe causes zero-tail compaction and tool-loop amnesia.
  if (/come up with a .{0,80}title for this conversation/i.test(source)) return true;
  if (/come up with a .{0,80}title\b/i.test(source) && /<title>/i.test(source)) return true;
  if (/put your answer in\s*<title>/i.test(source)) return true;
  if (/just return the title in the specified format/i.test(lower)) return true;
  if (/conversation naming technique/i.test(lower)) return true;
  if (/^\s*<title>[\s\S]*<\/title>\s*$/i.test(source)) return true;
  if (/\b2-5 word title\b/i.test(source) && /<\/title>/i.test(source)) return true;
  return false;
}

function findLatestRealUserIndex(snapshots) {
  for (let i = (snapshots || []).length - 1; i >= 0; i -= 1) {
    const message = snapshots[i];
    if (message.role !== "user") continue;
    const text = String(message.text || "").trim();
    if (!text || isMetaUserMessage(text)) continue;
    return i;
  }
  return -1;
}

function extractControlState(messages, prior = {}, options = {}) {
  const snapshots = snapshotMessages(messages || []);
  const priorCount = Number(prior.sourceMessageCount || 0);
  const priorHasActiveTaskRoute = Boolean(prior.toolRoute?.routeHash);
  const priorHasRouteOwnership = Boolean(compactTaskRouteOwnership(prior.taskRouteOwnership));
  const canResume = priorCount > 0
    && priorCount <= snapshots.length
    && prior.sourceHistoryHash === sha256(stableStringify(snapshots.slice(0, priorCount)))
    // Revision 22 migration: old checkpoints discarded ownerCapability. When
    // an active task route is present, rescan the bounded conversation once so
    // compact route ownership can be recovered from an earlier tool result.
    && (!priorHasActiveTaskRoute || priorHasRouteOwnership)
    && Number(prior.schemaVersion || 0) === COMPACTION_SCHEMA_VERSION;
  const source = canResume ? snapshots.slice(priorCount) : snapshots;
  const state = {
    schemaVersion: COMPACTION_SCHEMA_VERSION,
    objective: canResume ? (prior.objective || "") : "",
    constraints: canResume && Array.isArray(prior.constraints) ? [...prior.constraints] : [],
    activeProject: canResume ? (prior.activeProject || null) : null,
    activeProjectName: canResume ? (prior.activeProjectName || "") : "",
    touchedPaths: canResume && Array.isArray(prior.modifiedFiles) ? [...prior.modifiedFiles] : [],
    lastDiagnostics: canResume && Array.isArray(prior.diagnostics) ? [...prior.diagnostics] : [],
    exactSignatureContracts: canResume && Array.isArray(prior.exactSignatureContracts) ? [...prior.exactSignatureContracts] : [],
    requiredNextTool: canResume ? (prior.requiredNextTool?.name || null) : null,
    requiredNextToolRef: canResume ? (prior.requiredNextTool?.reference || null) : null,
    requiredNextToolArgs: canResume ? (prior.requiredNextTool?.args || null) : null,
    mutationGeneration: canResume ? Number(prior.mutationGeneration || 0) : 0,
    buildState: canResume ? { ...(prior.buildState || {}) } : {},
    selectedSlice: canResume ? (prior.selectedSlice || null) : null,
    sliceProgress: canResume ? (prior.sliceProgress || null) : null,
    buildVerification: canResume ? (prior.buildVerification || null) : null,
    toolRoute: canResume ? (prior.toolRoute || null) : null,
    taskRouteOwnership: canResume ? compactTaskRouteOwnership(prior.taskRouteOwnership) : null,
    invariants: canResume && Array.isArray(prior.invariants) ? [...prior.invariants] : [],
    coverageEvidence: canResume && Array.isArray(prior.coverageEvidence) ? [...prior.coverageEvidence] : [],
    architectureProposal: canResume && prior.architectureProposal
      ? { ...prior.architectureProposal }
      : null,
    protocolControl: canResume && prior.protocolControl
      ? { ...prior.protocolControl }
      : null,
    architectureControl: canResume && prior.architectureControl
      ? { ...prior.architectureControl }
      : null,
    semanticBlocker: canResume && prior.semanticBlocker
      ? { ...prior.semanticBlocker }
      : null,
    failedToolResults: canResume && Array.isArray(prior.failedToolResults) ? [...prior.failedToolResults] : [],
    facts: canResume && Array.isArray(prior.facts) ? [...prior.facts] : [],
    evidenceFacts: canResume && Array.isArray(prior.evidenceFacts) ? [...prior.evidenceFacts] : [],
  };
  const toolCallsById = new Map();
  const anonymousToolCalls = [];

  for (const snapshot of source) {
    if (snapshot.role === "user" && snapshot.text.trim()) {
      if (isMetaUserMessage(snapshot.text)) {
        continue;
      }
      // Latest real user message always wins — pinning the first turn causes goal drift.
      // Synthetic LM Studio title prompts must not replace the active goal.
      const userText = snapshot.text.trim();
      const continuation = Boolean(state.objective) && isContinuationUserMessage(userText);
      if (
        state.semanticBlocker?.active
        && state.objective
        && userText !== state.objective
        && !continuation
      ) {
        state.semanticBlocker = null;
      }
      // A continuation utterance advances the existing task; it is not a new
      // objective. Replacing the objective here loses intent after compaction
      // and can silently turn an implementation task into a generic "continue".
      if (continuation) continue;
      state.objective = userText.slice(0, 1200);
      state.constraints = state.constraints.filter((item) =>
        typeof item === "string" && !item.startsWith("active_goal:") && !item.startsWith("read_only_"));
      state.constraints.push(`active_goal:${userText.slice(0, 400)}`);
      if (isReadOnlyUserGoal(userText)) {
        state.constraints.push(
          "read_only_findings_only: do not edit files; do not invent refactor/implementation plans; "
          + "do not re-emit a prior project-structure overview unless the latest user asked for it",
        );
      }
    }
    for (const payload of parseJsonObjects(snapshot.text)) {
      collectControlFields(payload, state);
    }
    for (const call of snapshot.toolCalls) {
      state.facts.push(`tool:${call.name}`);
      if (call.id) toolCallsById.set(call.id, call);
      else anonymousToolCalls.push(call);
    }
    for (const result of snapshot.toolResults) {
      const matchedCall = result.toolCallId
        ? (toolCallsById.get(result.toolCallId) || { name: result.name, arguments: {} })
        : (anonymousToolCalls.shift() || { name: result.name, arguments: {} });
      const matchedCallName = matchedCall.name || result.name;
      const normalizedCallName = String(matchedCallName || "").toLowerCase();
      if (
        normalizedCallName.endsWith("unreal_architecture_reasoning")
        && (matchedCall.arguments?.proposalPatch || matchedCall.arguments?.proposalRepairs)
      ) {
        const patch = matchedCall.arguments.proposalPatch || matchedCall.arguments.proposalRepairs;
        const patchDigest = sha256(stableStringify(patch));
        const previousDigest = state.architectureProposal?.lastPatchDigest || "";
        const repairPaths = Array.isArray(matchedCall.arguments?.proposalRepairs)
          ? matchedCall.arguments.proposalRepairs.map((row) => String(row?.jsonPath || "")).filter(Boolean)
          : [];
        state.architectureProposal = {
          ...(state.architectureProposal || {}),
          lastPatchDigest: patchDigest,
          lastPatchFields: repairPaths.length ? repairPaths.slice(0, 24) : Object.keys(patch).slice(0, 20),
          lastPatchPreview: repairPaths.length
            ? matchedCall.arguments.proposalRepairs.slice(0, 24).map((row) => ({
              jsonPath: String(row?.jsonPath || "").slice(0, 160),
              value: String(stableStringify(row?.value)).slice(0, 500),
            }))
            : boundedArchitecturePatchPreview(patch),
          unchangedPatchAttempts: previousDigest === patchDigest
            ? Number(state.architectureProposal?.unchangedPatchAttempts || 0) + 1
            : 0,
        };
      }
      const resultPayloads = parseJsonObjects(result.content);
      for (const payload of resultPayloads) {
        collectControlFields(payload, state);
        collectSemanticBlockerFields(payload, state, matchedCallName);
      }
      if (toolResultSucceeded(result)) {
        const evidence = compactToolEvidence(matchedCall, resultPayloads.slice(-1)[0] || {}, result.content);
        if (evidence) state.evidenceFacts.push(evidence);
      }
      if (!toolResultSucceeded(result)) {
        const failurePayload = parseJsonObjects(result.content).slice(-1)[0] || {};
        state.failedToolResults.push({
          tool: String(matchedCallName || result.name || ""),
          errorCode: String(failurePayload.errorCode || ""),
          detail: String(
            failurePayload.error
            || failurePayload.userMessage
            || "tool result marked failed"
          ).slice(0, 400),
        });
      }
      if (toolResultSucceeded(result) && mutationToolName(matchedCallName)) {
        const reportedMutationGeneration = resultPayloads
          .map((payload) => Number(payload?.mutationGeneration))
          .filter((value) => Number.isFinite(value) && value >= 0)
          .at(-1);
        if (reportedMutationGeneration === undefined) {
          state.mutationGeneration += 1;
        } else {
          state.mutationGeneration = Math.max(state.mutationGeneration, reportedMutationGeneration);
        }
        // Evidence-level stop/do-not-retry controls remain authoritative until
        // the user changes the goal or a successful mutation changes the source
        // snapshot that made the evidence stale.
        state.semanticBlocker = null;
      }
      if (
        toolResultSucceeded(result)
        && semanticBlockerClearToolSucceeded(
          state.semanticBlocker,
          matchedCallName,
          matchedCall,
          resultPayloads.slice(-1)[0] || {},
        )
      ) {
        state.semanticBlocker = null;
      }
      // A generated call is only intent. Keep the required tool gate until the
      // paired result is observed and is explicitly non-failing.
      if (
        state.requiredNextTool
        && toolNamesMatch(state.requiredNextTool, matchedCallName)
        && toolResultSucceeded(result)
        && toolArgumentsSatisfy(state.requiredNextToolArgs, matchedCall.arguments)
      ) {
        state.requiredNextTool = null;
        state.requiredNextToolRef = null;
        state.requiredNextToolArgs = null;
      }
    }
  }

  const cap = Number(options.maxCheckpointFacts || DEFAULT_COMPACTION_CONFIG.maxCheckpointFacts);
  state.touchedPaths = [...new Set(state.touchedPaths)].slice(-cap);
  state.lastDiagnostics = [...new Set(state.lastDiagnostics)].slice(-cap);
  state.constraints = [...new Set(state.constraints)].slice(-cap);
  state.exactSignatureContracts = [...new Map(
    state.exactSignatureContracts.map((contract) => [stableStringify(contract), contract]),
  ).values()].slice(-cap);
  state.facts = [...new Set(state.facts)].slice(-cap);
  state.invariants = [...new Set(state.invariants)].slice(-cap);
  state.coverageEvidence = state.coverageEvidence.slice(-cap);
  state.failedToolResults = state.failedToolResults.slice(-cap);
  const evidenceByKey = new Map();
  for (const fact of state.evidenceFacts) {
      const tool = String(fact?.tool || "").toLowerCase();
      let key = stableStringify(fact);
      if (tool.endsWith("read_file") || tool.endsWith("read_file_range")) key = `read:${fact.path}`;
      else if (tool.endsWith("list_directory")) key = `list:${fact.path}`;
      else if (tool.endsWith("search_files")) key = `search:${fact.path}:${fact.query}`;
      else if (tool.endsWith("get_active_project") || tool === "get_workspace_info") key = `project:${tool}`;
      const priorFact = evidenceByKey.get(key);
      if (priorFact && (tool.endsWith("read_file") || tool.endsWith("read_file_range"))) {
        const merged = { ...priorFact, ...fact };
        if (!fact.evidenceHash) merged.evidenceHash = priorFact.evidenceHash;
        if (!fact.lineCount) merged.lineCount = priorFact.lineCount;
        if (!Array.isArray(fact.semanticAnchors) || fact.semanticAnchors.length === 0) {
          merged.semanticAnchors = priorFact.semanticAnchors || [];
        }
        evidenceByKey.set(key, merged);
      } else if (priorFact && tool.endsWith("search_files")) {
        const merged = { ...priorFact, ...fact };
        const repeatedWithoutFreshRows = fact.cached === true || fact.repeatDetected === true;
        if (repeatedWithoutFreshRows) {
          merged.resultCount = Math.max(Number(priorFact.resultCount || 0), Number(fact.resultCount || 0));
          merged.fileNameResultCount = Math.max(
            Number(priorFact.fileNameResultCount || 0),
            Number(fact.fileNameResultCount || 0),
          );
          merged.searchComplete = priorFact.searchComplete === true || fact.searchComplete === true;
          merged.matchedFiles = [...new Set([
            ...(Array.isArray(priorFact.matchedFiles) ? priorFact.matchedFiles : []),
            ...(Array.isArray(fact.matchedFiles) ? fact.matchedFiles : []),
          ])].slice(0, 12);
        }
        evidenceByKey.set(key, merged);
      } else {
        evidenceByKey.set(key, fact);
      }
  }
  state.evidenceFacts = [...evidenceByKey.values()].slice(-cap);
  return state;
}

function buildCheckpoint(messages, prior = {}, options = {}) {
  const control = extractControlState(messages, prior, options);
  const snapshots = snapshotMessages(messages || []);
  const generation = Number(prior.checkpointGeneration || 0) + 1;
  if (
    control.semanticBlocker?.active
    && control.requiredNextTool
    && control.semanticBlocker.forbiddenTools.some((name) => toolNamesMatch(name, control.requiredNextTool))
  ) {
    control.requiredNextTool = null;
    control.requiredNextToolRef = null;
    control.requiredNextToolArgs = null;
  }
  return {
    schemaVersion: COMPACTION_SCHEMA_VERSION,
    checkpointGeneration: generation,
    createdAt: new Date().toISOString(),
    objective: control.objective,
    constraints: control.constraints,
    activeProject: control.activeProject,
    activeProjectName: control.activeProjectName,
    modifiedFiles: control.touchedPaths,
    mutationGeneration: control.mutationGeneration,
    buildState: control.buildState,
    selectedSlice: control.selectedSlice,
    sliceProgress: control.sliceProgress,
    buildVerification: control.buildVerification,
    toolRoute: control.toolRoute,
    taskRouteOwnership: control.taskRouteOwnership,
    invariants: control.invariants,
    coverageEvidence: control.coverageEvidence,
    architectureProposal: control.architectureProposal,
    protocolControl: control.protocolControl,
    architectureControl: control.architectureControl,
    semanticBlocker: control.semanticBlocker,
    failedToolResults: control.failedToolResults,
    requiredNextTool: control.requiredNextTool ? {
      name: control.requiredNextTool,
      reference: control.requiredNextToolRef,
      args: control.requiredNextToolArgs,
    } : null,
    exactSignatureContracts: control.exactSignatureContracts,
    diagnostics: control.lastDiagnostics,
    facts: control.facts,
    evidenceFacts: control.evidenceFacts,
    pendingToolCall: prior.pendingToolCall || null,
    pendingToolCalls: Array.isArray(prior.pendingToolCalls) ? [...prior.pendingToolCalls] : [],
    completedToolCallIds: Array.isArray(prior.completedToolCallIds) ? [...prior.completedToolCallIds].slice(-256) : [],
    // RAG and Agent publish tool catalogs independently. Preserve the one-shot
    // catalog refresh across its tool-result turn so a stale client catalog
    // cannot send the model into an unbounded health/read recovery loop.
    catalogRefresh: prior.catalogRefresh && typeof prior.catalogRefresh === "object"
      && !Array.isArray(prior.catalogRefresh)
      ? { ...prior.catalogRefresh }
      : null,
    compactionGeneration: Number(prior.compactionGeneration || 0),
    sourceMessageCount: snapshots.length,
    sourceHistoryHash: sha256(stableStringify(snapshots)),
    lastCompactionSourceMessageCount: Number(prior.lastCompactionSourceMessageCount || 0),
  };
}

const SESSION_MARKER_RE = /<!--\s*ucc-session:([a-f0-9]{16,64})\s*-->/i;

function extractSessionMarker(messages) {
  for (const snapshot of snapshotMessages(messages || [])) {
    const match = String(snapshot.text || "").match(SESSION_MARKER_RE);
    if (match) return String(match[1] || "").toLowerCase();
  }
  return null;
}

function formatSessionMarker(sessionId) {
  const id = String(sessionId || "").replace(/[^a-f0-9]/gi, "").toLowerCase().slice(0, 32);
  if (id.length < 16) return "";
  return `<!-- ucc-session:${id} -->`;
}

function messageLineageFingerprints(messages) {
  return snapshotMessages(messages || []).map((message) => {
    const toolIds = (message.toolCalls || [])
      .map((call) => String(call.id || ""))
      .filter(Boolean)
      .join(",");
    return sha256(`${message.role}:${String(message.text || "").slice(0, 500)}:${toolIds}`).slice(0, 16);
  });
}

function lineageContinues(previous, current) {
  if (!Array.isArray(previous) || !Array.isArray(current)) return false;
  if (previous.length === 0) return current.length >= 0;
  if (previous.length > current.length) return false;
  return previous.every((hash, index) => hash === current[index]);
}

function baseSessionKey(messages, salt = "") {
  const snapshots = snapshotMessages(messages || []);
  const firstSystem = snapshots.find((message) => message.role === "system");
  const firstUser = snapshots.find(
    (message) => message.role === "user" && String(message.text || "").trim() && !isMetaUserMessage(message.text),
  );
  const seed = [firstSystem, firstUser]
    .filter(Boolean)
    .map((message) => `${message.role}:${message.text}`)
    .join("\n");
  return sha256(`${salt}\n${seed || "empty-session"}`).slice(0, 32);
}

function sessionFingerprint(messages, salt = "", options = {}) {
  const marker = String(
    options.sessionMarker
    || options.explicitSessionId
    || extractSessionMarker(messages)
    || "",
  ).trim().toLowerCase();
  if (marker) {
    // A UCC marker is already a minted session identity. Re-hashing it turns
    // marker A into session B on the next generation and breaks continuity.
    return marker.replace(/[^a-f0-9]/g, "").slice(0, 32);
  }
  return baseSessionKey(messages, salt);
}

function lmStudioConversationSessionFingerprint(workingDirectory, modelIdentifier = "") {
  const raw = String(workingDirectory || "").trim();
  if (!raw) return "";
  const normalized = raw.replace(/\\/g, "/").replace(/\/+$/, "");
  const match = normalized.match(/(?:^|\/)working-directories\/([^/]+)$/i);
  if (!match || !String(match[1] || "").trim()) return "";
  // LM Studio assigns this directory per conversation. It remains stable while
  // assistant/tool messages grow or are cancelled, unlike message lineage.
  // Include the model so switching generator targets cannot inherit an
  // incompatible checkpoint. Normalize Windows drive/path casing only.
  const pathIdentity = /^[A-Za-z]:\//.test(normalized)
    ? normalized.toLowerCase()
    : normalized;
  return sha256(`lmstudio-conversation\n${pathIdentity}\n${String(modelIdentifier || "")}`).slice(0, 32);
}

function isMajorGoalChange(priorObjective, latestObjective) {
  const prior = String(priorObjective || "").trim();
  const latest = String(latestObjective || "").trim();
  if (!prior || !latest || prior === latest) return false;
  if (isMetaUserMessage(latest)) return false;
  const priorReadOnly = isReadOnlyUserGoal(prior);
  const latestReadOnly = isReadOnlyUserGoal(latest);
  if (priorReadOnly !== latestReadOnly) return true;
  const goalBucket = (text) => {
    if (isReadOnlyUserGoal(text)) return "readonly";
    if (/\b(implement|refactor|fix|patch|edit|write|compile|build|구현|수정|리팩터)\b/i.test(text)) {
      return "write";
    }
    if (/\b(analyze|review|find|structure|구조|분석|버그|조사|찾아)\b/i.test(text)) {
      return "inspect";
    }
    return "other";
  };
  const priorBucket = goalBucket(prior);
  const latestBucket = goalBucket(latest);
  return Boolean(
    priorBucket !== "other"
    && latestBucket !== "other"
    && priorBucket !== latestBucket,
  );
}

function toolNamesMatch(expected, actual) {
  // LM Studio SDK revisions have exposed the same MCP tool as either a plain
  // function name or a provider-qualified path. Compare a separator-normalized
  // form so `mcp/unreal-rag/unreal_agent_plan` and `unreal_agent_plan` bind to
  // one contract without loosening ordinary suffix matching.
  const normalize = (value) => String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  const left = normalize(expected);
  const right = normalize(actual);
  if (!left || !right) return false;
  return left === right || left.endsWith(`_${right}`) || right.endsWith(`_${left}`);
}

function expectedToolReserve(toolName, config = {}) {
  const normalized = String(toolName || "").toLowerCase();
  if (normalized.includes("build") || normalized.includes("compile")) {
    return Number(config.buildToolResultReserve || DEFAULT_COMPACTION_CONFIG.buildToolResultReserve);
  }
  return Number(config.normalToolResultReserve || DEFAULT_COMPACTION_CONFIG.normalToolResultReserve);
}

function budgetDecision({ contextLength, inputTokens, nextToolName, config = {}, toolSchemaTokens = 0 }) {
  const merged = { ...DEFAULT_COMPACTION_CONFIG, ...config };
  const reserve = Number(merged.maxOutputReserve)
    + Number(merged.safetyMarginTokens || 0)
    + Number(toolSchemaTokens || 0)
    + expectedToolReserve(nextToolName, merged);
  const remaining = Number(contextLength) - Number(inputTokens) - reserve;
  let action = "normal";
  if (remaining < merged.hardRemainingTokens) action = "hard_compact";
  else if (remaining < merged.softRemainingTokens) action = "soft_compact";
  return {
    action,
    contextLength: Number(contextLength),
    inputTokens: Number(inputTokens),
    reservedTokens: reserve,
    remainingTokens: remaining,
    thresholds: {
      soft: merged.softRemainingTokens,
      hard: merged.hardRemainingTokens,
    },
  };
}

function isCompleteToolPair(messages) {
  const pending = new Set();
  const known = new Set();
  const completed = new Set();
  let anonymousPending = 0;
  for (const message of messages || []) {
    for (const call of messageSnapshot(message).toolCalls) {
      if (call.id) {
        known.add(call.id);
        pending.add(call.id);
      } else anonymousPending += 1;
    }
    for (const result of messageSnapshot(message).toolResults) {
      if (result.toolCallId && !known.has(result.toolCallId)) return false;
      if (result.toolCallId) {
        if (completed.has(result.toolCallId)) return false;
        completed.add(result.toolCallId);
        pending.delete(result.toolCallId);
      }
      else {
        anonymousPending -= 1;
        if (anonymousPending < 0) return false;
      }
    }
    // Tool results are validated in the loop above.
  }
  return pending.size === 0 && anonymousPending === 0;
}

function completeTailStart(snapshots, startIndex) {
  let start = Math.max(0, Number(startIndex || 0));
  while (start > 0) {
    const tail = snapshots.slice(start);
    const callIds = new Set();
    let anonymousBalance = 0;
    let orphanResult = false;
    for (const message of tail) {
      for (const call of message.toolCalls || []) {
        if (call.id) callIds.add(call.id);
        else anonymousBalance += 1;
      }
      for (const result of message.toolResults || []) {
        if (result.toolCallId && !callIds.has(result.toolCallId)) orphanResult = true;
        if (!result.toolCallId) {
          anonymousBalance -= 1;
          if (anonymousBalance < 0) orphanResult = true;
        }
      }
    }
    if (!orphanResult) return start;
    start -= 1;
  }
  return 0;
}
function summarizeOldMessages(messages, checkpoint) {
  const lines = [
    "Conversation checkpoint (control state is authoritative; do not reinterpret it).",
    `checkpointGeneration=${checkpoint.checkpointGeneration}`,
    `objective=${checkpoint.objective || "(not captured)"}`,
  ];
  if (checkpoint.modifiedFiles?.length) lines.push(`modifiedFiles=${checkpoint.modifiedFiles.join(", ")}`);
  if (checkpoint.constraints?.length) lines.push(`constraints=${checkpoint.constraints.join(" | ")}`);
  if (checkpoint.activeProject) lines.push(`activeProject=${checkpoint.activeProject}`);
  if (checkpoint.activeProjectName) lines.push(`activeProjectName=${checkpoint.activeProjectName}`);
  lines.push(`mutationGeneration=${Number(checkpoint.mutationGeneration || 0)}`);
  if (checkpoint.buildState && Object.keys(checkpoint.buildState).length) {
    lines.push(`buildState=${JSON.stringify(checkpoint.buildState)}`);
  }
  if (checkpoint.selectedSlice) lines.push(`selectedSlice=${JSON.stringify(checkpoint.selectedSlice)}`);
  if (checkpoint.sliceProgress) lines.push(`sliceProgress=${JSON.stringify(checkpoint.sliceProgress)}`);
  if (checkpoint.buildVerification) lines.push(`buildVerification=${JSON.stringify(checkpoint.buildVerification)}`);
  if (checkpoint.toolRoute) lines.push(`toolRoute=${JSON.stringify(checkpoint.toolRoute)}`);
  if (checkpoint.taskRouteOwnership) {
    lines.push(`taskAuthorization=${JSON.stringify(checkpoint.taskRouteOwnership)}`);
    lines.push(
      "routeOwnershipInstruction=Use the compact taskAuthorization above for active routed tools. "
      + "Do not recover, cancel, or replace the healthy task merely because authToken is omitted.",
    );
  }
  if (checkpoint.invariants?.length) lines.push(`invariants=${checkpoint.invariants.join(" | ")}`);
  if (checkpoint.coverageEvidence?.length) {
    lines.push(`coverageEvidence=${JSON.stringify(checkpoint.coverageEvidence)}`);
  }
  if (checkpoint.architectureProposal) {
    lines.push(`architectureProposalContinuation=${JSON.stringify(checkpoint.architectureProposal)}`);
    if (
      checkpoint.architectureProposal.requiresFullReplan
      || checkpoint.architectureProposal.repairStrategy === "full_replan"
      || checkpoint.architectureProposal.repairMode === "fullProposal"
    ) {
      lines.push(
        "architectureProposalInstruction=The retained proposal has a core ownership/state/lifecycle contradiction. "
        + "Reuse retained direct-source evidence while sourceSnapshotFingerprint is unchanged. Re-read only when "
        + "source changed, required evidence is missing, or needed lines were not covered. Submit one complete "
        + "independently derived proposal. Do not use proposalPatch/proposalRepairs, do not reuse lastPatchPreview, "
        + "and do not preserve the rejected central owner.",
      );
    } else {
      lines.push(
        "architectureProposalInstruction=Use the exact proposal revision above. Resolve each retained repair "
        + "requirement by changing the corresponding values. Compare against lastPatchPreview and never resubmit "
        + "the same patch digest; when repairMode is proposalRepairs, call unreal_architecture_reasoning with "
        + "baseProposalRevision plus one {jsonPath,value} entry per requiredRepairPaths item. Keep each path exact, "
        + "fill values from your own design, and do not regenerate or resend the prior proposalPatch. For an array "
        + "path, send one complete replacement array rather than repeating that jsonPath per item.",
      );
    }
  }
  if (checkpoint.protocolControl) {
    lines.push(`protocolControl=${JSON.stringify(checkpoint.protocolControl)}`);
  }
  if (checkpoint.architectureControl) {
    lines.push(`architectureControl=${JSON.stringify(checkpoint.architectureControl)}`);
  }
  if (checkpoint.semanticBlocker?.active) {
    lines.push(`semanticBlocker=${JSON.stringify(checkpoint.semanticBlocker)}`);
    lines.push(
      "semanticBlockerInstruction=This server-owned blocker survives compaction. Do not call any forbiddenTools. "
      + "If scope=evidence_phase, only discovery is closed: continue from retained evidence with an allowed "
      + "write/validation/final action. If scope=until_required_tool_success, call clearOnTool once. "
      + "Never retry a forbidden tool merely because older tool results were compacted.",
    );
  }
  if (checkpoint.failedToolResults?.length) {
    lines.push(`failedToolResults=${JSON.stringify(checkpoint.failedToolResults)}`);
  }
  if (checkpoint.diagnostics?.length) lines.push(`diagnostics=${checkpoint.diagnostics.join(" | ")}`);
  if (checkpoint.requiredNextTool?.name) {
    lines.push(`requiredNextTool=${checkpoint.requiredNextTool.name}`);
    lines.push(`requiredNextToolArgs=${JSON.stringify(checkpoint.requiredNextTool.args || {})}`);
  }
  if (checkpoint.exactSignatureContracts?.length) {
    lines.push(`exactSignatureContracts=${JSON.stringify(checkpoint.exactSignatureContracts)}`);
  }
  if (checkpoint.facts?.length) lines.push(`facts=${checkpoint.facts.join(" | ")}`);
  if (checkpoint.evidenceFacts?.length) {
    const readPaths = checkpoint.evidenceFacts
      .filter((fact) => /read_file(?:_range)?$/i.test(String(fact?.tool || "")) && fact?.path)
      .map((fact) => fact.path);
    if (readPaths.length) {
      lines.push(
        `discoveryLedger=already-read unchanged files (${readPaths.length}): ${readPaths.join(", ")}. `
        + "Do not re-read these paths merely to remember them; use their semanticAnchors below. "
        + "Read again only after a mutation, when a required edit needs an exact range absent from the anchors, "
        + "or when the tool reports changed evidence.",
      );
    }
    lines.push(`evidenceFacts=${JSON.stringify(checkpoint.evidenceFacts)}`);
  }
  lines.push(`compactedMessageCount=${(messages || []).length}`);
  lines.push(
    "Only use this summary for continuity. The checkpoint objective is the latest user goal; "
    + "do not continue an older structure/overview or refactor plan unless that latest goal asks for it. "
    + "Do not invent missing classes, modules, or GameFramework paths from memory or prior assistant prose. "
    + "Trust verified evidenceFacts and semanticAnchors for unchanged files; use tools for unread, changed, or exact-range evidence.",
  );
  return lines.join("\n");
}

function compactSnapshots(messages, checkpoint, options = {}) {
  const snapshots = snapshotMessages(messages || []);
  const configuredTurns = options.recentCompleteTurns === undefined
    ? DEFAULT_COMPACTION_CONFIG.recentCompleteTurns
    : Number(options.recentCompleteTurns);
  // 0 retained turns => systems + latest real user + current-turn tools only (no older tail).
  const tailCount = configuredTurns <= 0
    ? 0
    : Math.max(1, configuredTurns * 2);
  const latestUserIndex = findLatestRealUserIndex(snapshots);
  const systems = [];
  const older = [];
  const currentTurn = [];
  let latestUser = null;
  for (let i = 0; i < snapshots.length; i += 1) {
    const message = snapshots[i];
    if (message.role === "system") {
      systems.push(message);
      continue;
    }
    if (message.role === "user" && isMetaUserMessage(message.text)) {
      continue;
    }
    if (i === latestUserIndex) {
      latestUser = message;
      continue;
    }
    if (latestUserIndex >= 0 && i > latestUserIndex) {
      currentTurn.push(message);
      continue;
    }
    older.push(message);
  }
  const olderTailStart = tailCount === 0
    ? older.length
    : completeTailStart(older, Math.max(0, older.length - tailCount));
  const olderTail = older.slice(olderTailStart);
  let keptCurrentTurn = currentTurn;
  const maxCurrent = options.maxCurrentTurnMessages;
  if (Number.isFinite(maxCurrent) && Number(maxCurrent) >= 0 && currentTurn.length > Number(maxCurrent)) {
    const keepStart = completeTailStart(
      currentTurn,
      Math.max(0, currentTurn.length - Number(maxCurrent)),
    );
    keptCurrentTurn = currentTurn.slice(keepStart);
  }
  // Many chat templates (Qwen/ChatML/Llama) allow only ONE leading system message.
  // Emitting a second system for the checkpoint makes applyPromptTemplate fail or
  // collapse to an empty user prompt (~10 tokens) and the model loses the goal.
  const checkpointText = summarizeOldMessages(older.slice(0, olderTailStart), checkpoint);
  const systemParts = [];
  for (const message of systems) {
    const text = String(message.text || "").trim();
    if (text) systemParts.push(text);
  }
  systemParts.push(checkpointText);
  const result = [{
    role: "system",
    text: systemParts.join("\n\n"),
    toolCalls: [],
    toolResults: [],
  }];
  result.push(...olderTail);
  if (latestUser) result.push(latestUser);
  // Prefer keeping the full in-flight turn; only trim oldest pairs when the
  // caller hits the hard token margin after older history is already gone.
  result.push(...keptCurrentTurn);
  if (options.trailingMetaUser && typeof options.trailingMetaUser === "object") {
    result.push(options.trailingMetaUser);
  }
  return result;
}

function validateCheckpoint(checkpoint) {
  if (!checkpoint || checkpoint.schemaVersion !== COMPACTION_SCHEMA_VERSION) return false;
  if (!Number.isFinite(Number(checkpoint.checkpointGeneration))) return false;
  if (
    checkpoint.requiredNextTool
    && (
      typeof checkpoint.requiredNextTool !== "object"
      || Array.isArray(checkpoint.requiredNextTool)
      || typeof checkpoint.requiredNextTool.name !== "string"
      || !checkpoint.requiredNextTool.name.trim()
    )
  ) return false;
  if (!Array.isArray(checkpoint.completedToolCallIds)) return false;
  if (!checkpoint.completedToolCallIds.every((id) => typeof id === "string" && id.length > 0)) return false;
  if (checkpoint.pendingToolCall !== undefined && checkpoint.pendingToolCall !== null) {
    if (typeof checkpoint.pendingToolCall !== "object" || Array.isArray(checkpoint.pendingToolCall)) return false;
    if (typeof checkpoint.pendingToolCall.name !== "string" || !checkpoint.pendingToolCall.name.trim()) return false;
  }
  if (checkpoint.pendingToolCalls !== undefined) {
    if (!Array.isArray(checkpoint.pendingToolCalls)) return false;
    if (checkpoint.pendingToolCalls.some((call) => (
      !call
      || typeof call !== "object"
      || Array.isArray(call)
      || typeof call.name !== "string"
      || !call.name.trim()
    ))) return false;
  }
  if (checkpoint.sourceMessageCount !== undefined) {
    const count = Number(checkpoint.sourceMessageCount);
    if (!Number.isFinite(count) || count < 0) return false;
  }
  if (checkpoint.sourceHistoryHash !== undefined && typeof checkpoint.sourceHistoryHash !== "string") return false;
  if (checkpoint.catalogRefresh !== undefined && checkpoint.catalogRefresh !== null) {
    if (typeof checkpoint.catalogRefresh !== "object" || Array.isArray(checkpoint.catalogRefresh)) return false;
    if (typeof checkpoint.catalogRefresh.routeHash !== "string") return false;
    const attempts = Number(checkpoint.catalogRefresh.attempts);
    if (!Number.isInteger(attempts) || attempts < 0 || attempts > 1) return false;
    if (!["requested", "synchronized", "failed"].includes(checkpoint.catalogRefresh.status)) return false;
  }
  if (checkpoint.semanticBlocker !== undefined && checkpoint.semanticBlocker !== null) {
    if (typeof checkpoint.semanticBlocker !== "object" || Array.isArray(checkpoint.semanticBlocker)) return false;
    if (!Array.isArray(checkpoint.semanticBlocker.forbiddenTools)) return false;
    if (checkpoint.semanticBlocker.forbiddenTools.some((name) => typeof name !== "string" || !name.trim())) return false;
  }
  if (checkpoint.evidenceFacts !== undefined && !Array.isArray(checkpoint.evidenceFacts)) return false;
  if (
    checkpoint.taskRouteOwnership !== undefined
    && checkpoint.taskRouteOwnership !== null
    && !compactTaskRouteOwnership(checkpoint.taskRouteOwnership)
  ) return false;
  return true;
}

module.exports = {
  COMPACTION_SCHEMA_VERSION,
  DEFAULT_COMPACTION_CONFIG,
  stableStringify,
  sha256,
  textOf,
  roleOf,
  toolRequestsOf,
  toolResultsOf,
  messageSnapshot,
  snapshotMessages,
  parseJsonObjects,
  toolResultSucceeded,
  isNonToolNextAction,
  compactTaskRouteOwnership,
  collectSemanticBlockerFields,
  isContinuationUserMessage,
  mutationToolName,
  toolArgumentsSatisfy,
  collectControlFields,
  isReadOnlyUserGoal,
  isMetaUserMessage,
  findLatestRealUserIndex,
  extractControlState,
  buildCheckpoint,
  SESSION_MARKER_RE,
  extractSessionMarker,
  formatSessionMarker,
  messageLineageFingerprints,
  lineageContinues,
  baseSessionKey,
  sessionFingerprint,
  lmStudioConversationSessionFingerprint,
  isMajorGoalChange,
  toolNamesMatch,
  expectedToolReserve,
  budgetDecision,
  isCompleteToolPair,
  completeTailStart,
  summarizeOldMessages,
  compactSnapshots,
  validateCheckpoint,
};
