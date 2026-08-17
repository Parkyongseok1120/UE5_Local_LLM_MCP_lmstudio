"use strict";

const { attachControlEnvelope } = require("./control-envelope.js");
const { sanitizeModelPayload } = require("./public-contract.js");
const {
  pendingDirectEvidenceGate,
  taskAuthorizationForState,
} = require("./task-auth.js");
const {
  commitControlTransition,
  isSourceEvidenceTask,
} = require("./task-control-transition.js");

function postReadGatePayload(commitResult, toolName) {
  const state = commitResult?.state && typeof commitResult.state === "object"
    ? commitResult.state
    : null;
  if (!state || String(state.status || "") !== "running") return null;
  const gateName = pendingDirectEvidenceGate(state, toolName);
  let control = state.controlState && typeof state.controlState === "object"
    ? { ...state.controlState }
    : {};
  if (!gateName && control.authoritative !== true) return null;
  let authoritative = Number(control.version || 0) >= 2 && control.authoritative === true;
  let requiredName = String(
    authoritative ? control.requiredTool?.name || "" : control.requiredTool?.name || gateName || ""
  );
  const initialReadiness = state.synthesisReadiness && typeof state.synthesisReadiness === "object"
    ? state.synthesisReadiness
    : {};
  if (
    authoritative
    && isSourceEvidenceTask(state)
    && initialReadiness.ready === false
    && !requiredName
  ) {
    // Defensive repair for mixed-version callers: a read result must never
    // project a non-tool continuation while evidence is incomplete.
    commitControlTransition(state);
    control = state.controlState && typeof state.controlState === "object"
      ? { ...state.controlState }
      : control;
    authoritative = Number(control.version || 0) >= 2 && control.authoritative === true;
    requiredName = String(control.requiredTool?.name || "");
  }
  const projectedReadiness = state.synthesisReadiness && typeof state.synthesisReadiness === "object"
    ? state.synthesisReadiness
    : control.synthesisReadiness && typeof control.synthesisReadiness === "object"
      ? control.synthesisReadiness
      : {};

  const taskAuthorization = taskAuthorizationForState(state);
  const requiredArgs = control.requiredTool?.args && typeof control.requiredTool.args === "object"
    ? { ...control.requiredTool.args }
    : {};
  return {
    ok: true,
    status: gateName ? "direct_source_evidence_recorded" : "tool_outcome_committed",
    summary: gateName
      ? "Direct source evidence was recorded; resume the pending gate once."
      : "Tool outcome and authoritative task control were committed atomically.",
    taskSessionId: String(state.taskSessionId || ""),
    mutationGeneration: Math.max(0, Number(state.mutationGeneration || 0)),
    sourceEvidence: state.sourceEvidence && typeof state.sourceEvidence === "object"
      ? state.sourceEvidence
      : undefined,
    inspectionProgress: state.inspectionProgress && typeof state.inspectionProgress === "object"
      ? state.inspectionProgress
      : undefined,
    synthesisReadiness: projectedReadiness
      && typeof projectedReadiness === "object"
      ? projectedReadiness
      : undefined,
    controlEpoch: Math.max(0, Number(state.controlEpoch || 0)),
    nextAction: requiredName
      || (projectedReadiness.ready === false ? "evidence_recovery_blocked" : "use_authoritative_control"),
    nextActionIsTool: Boolean(requiredName),
    requiredNextTool: control.requiredTool || null,
    nextActionArgs: {
      ...requiredArgs,
      ...(requiredName ? { taskAuthorization } : {}),
    },
    taskAuthorization,
    toolRoute: state.toolRoute && typeof state.toolRoute === "object"
      ? state.toolRoute
      : {},
    ...(Object.keys(control).length ? { control } : {}),
    retryable: false,
    agentInstruction: gateName && requiredName
      ? (
        `The required direct source read succeeded. Call ${requiredName} once with `
        + "the returned taskAuthorization; do not read the same file again."
      )
      : "Follow only control.requiredTool/allowedTools from this response.",
  };
}

function currentRouteProjection(value) {
  const route = value && typeof value === "object" ? value : {};
  return Object.fromEntries([
    "version", "phase", "roleSession", "activeTools", "pendingGates",
    "selectedSlice", "maxToolCallsPerPhase", "maxFilesPerSlice", "routeHash",
  ].filter((key) => route[key] !== undefined).map((key) => [key, route[key]]));
}

function modelTextProjection(structuredContent, toolName, sourceText) {
  const control = structuredContent.control && typeof structuredContent.control === "object"
    ? structuredContent.control
    : {};
  const requiredName = String(control.requiredTool?.name || "");
  const readiness = structuredContent.synthesisReadiness && typeof structuredContent.synthesisReadiness === "object"
    ? structuredContent.synthesisReadiness
    : {};
  return {
    ok: structuredContent.ok,
    status: structuredContent.status,
    summary: structuredContent.summary,
    taskSessionId: structuredContent.taskSessionId,
    mutationGeneration: structuredContent.mutationGeneration,
    sourceEvidence: structuredContent.sourceEvidence,
    inspectionProgress: structuredContent.inspectionProgress,
    synthesisReadiness: structuredContent.synthesisReadiness,
    controlEpoch: structuredContent.controlEpoch,
    control,
    taskAuthorization: structuredContent.taskAuthorization,
    toolRoute: currentRouteProjection(structuredContent.toolRoute),
    nextAction: requiredName
      || (readiness.ready === false ? "evidence_recovery_blocked" : "use_authoritative_control"),
    nextActionIsTool: Boolean(requiredName),
    retryable: structuredContent.retryable,
    agentInstruction: structuredContent.agentInstruction,
    ...(String(toolName || "").startsWith("read_file")
      ? { fileContent: sourceText }
      : { toolOutput: sourceText }),
  };
}

function attachCommittedToolOutcomeControl(result, commitResult, toolName) {
  const payload = postReadGatePayload(commitResult, toolName);
  if (!payload) return result;
  const structuredContent = sanitizeModelPayload(
    attachControlEnvelope(payload, String(toolName || ""))
  );
  const sourceText = Array.isArray(result?.content)
    ? result.content
      .filter((block) => block && block.type === "text")
      .map((block) => String(block.text || ""))
      .join("\n")
    : "";
  return {
    ...result,
    // LM Studio versions differ in whether structuredContent is retained in
    // the conversation view passed to a generator plugin. Mirror the same
    // authoritative envelope into one JSON text block while preserving the
    // exact source body needed by the model.
    content: [{
      type: "text",
      text: JSON.stringify(modelTextProjection(
        structuredContent,
        toolName,
        sourceText
      )),
    }],
    structuredContent,
  };
}

module.exports = {
  attachCommittedToolOutcomeControl,
  attachPostReadRouteControl: attachCommittedToolOutcomeControl,
  postReadGatePayload,
};
