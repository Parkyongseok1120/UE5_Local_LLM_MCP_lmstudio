"use strict";

const { attachControlEnvelope } = require("./control-envelope.js");
const { sanitizeModelPayload } = require("./public-contract.js");
const {
  pendingDirectEvidenceGate,
  taskAuthorizationForState,
} = require("./task-auth.js");

function postReadGatePayload(commitResult, toolName) {
  const state = commitResult?.state && typeof commitResult.state === "object"
    ? commitResult.state
    : null;
  if (!state || String(state.status || "") !== "running") return null;
  const gateName = pendingDirectEvidenceGate(state, toolName);
  const control = state.controlState && typeof state.controlState === "object"
    ? { ...state.controlState }
    : {};
  if (!gateName && control.authoritative !== true) return null;
  const requiredName = String(control.requiredTool?.name || gateName || "");

  const taskAuthorization = taskAuthorizationForState(state);
  return {
    ok: true,
    status: gateName ? "direct_source_evidence_recorded" : "tool_outcome_committed",
    summary: gateName
      ? "Direct source evidence was recorded; resume the pending gate once."
      : "Tool outcome and authoritative task control were committed atomically.",
    taskSessionId: String(state.taskSessionId || ""),
    controlEpoch: Math.max(0, Number(state.controlEpoch || 0)),
    nextAction: requiredName || "use_authoritative_control",
    nextActionIsTool: Boolean(requiredName),
    requiredNextTool: control.requiredTool || null,
    nextActionArgs: { taskAuthorization },
    taskAuthorization,
    toolRoute: state.toolRoute && typeof state.toolRoute === "object"
      ? state.toolRoute
      : {},
    ...(Object.keys(control).length ? { control } : {}),
    retryable: false,
    agentInstruction: gateName
      ? (
        `The required direct source read succeeded. Call ${requiredName} once with `
        + "the returned taskAuthorization; do not read the same file again."
      )
      : "Follow only control.requiredTool/allowedTools from this response.",
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
      text: JSON.stringify({
        ...structuredContent,
        ...(String(toolName || "").startsWith("read_file")
          ? { fileContent: sourceText }
          : { toolOutput: sourceText }),
      }),
    }],
    structuredContent,
  };
}

module.exports = {
  attachCommittedToolOutcomeControl,
  attachPostReadRouteControl: attachCommittedToolOutcomeControl,
  postReadGatePayload,
};
