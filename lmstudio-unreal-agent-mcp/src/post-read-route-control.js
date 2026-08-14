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
  if (!gateName) return null;

  const taskAuthorization = taskAuthorizationForState(state);
  return {
    ok: true,
    status: "direct_source_evidence_recorded",
    summary: "Direct source evidence was recorded; resume the pending gate once.",
    taskSessionId: String(state.taskSessionId || ""),
    controlEpoch: Math.max(0, Number(state.controlEpoch || 0)),
    nextAction: gateName,
    nextActionIsTool: true,
    nextActionArgs: { taskAuthorization },
    taskAuthorization,
    toolRoute: state.toolRoute && typeof state.toolRoute === "object"
      ? state.toolRoute
      : {},
    retryable: true,
    agentInstruction: (
      `The required direct source read succeeded. Call ${gateName} once with `
      + "the returned taskAuthorization; do not read the same file again."
    ),
  };
}

function attachPostReadRouteControl(result, commitResult, toolName) {
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
        fileContent: sourceText,
      }),
    }],
    structuredContent,
  };
}

module.exports = {
  attachPostReadRouteControl,
  postReadGatePayload,
};
