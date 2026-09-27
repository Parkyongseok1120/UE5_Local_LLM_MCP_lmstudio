import type { LLMActionOpts, PredictionLoopHandlerController } from "@lmstudio/sdk";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { reserveReadResult, type BatchReservation } from "./budget-broker";
import { telemetryFingerprint } from "./evidence-telemetry";
import type { DirectConfig } from "./execution-contracts";
import { type OutputLimitStage } from "./execution-contracts";
import { createMessageEmitter, createToolGenerationTracker, toolPluginIdentifier } from "./prediction-ui";
import { isObservationOnlyToolCall, readOnlyOperationProfiles, type RemoteToolLike } from "./tool-capability-registry";
import { bindProjectArguments, type ScopedTool } from "./tool-scope";

export type HostToolApprovalPolicy = {
  neverAskForToolConfirmation?: boolean;
  skipToolConfirmationPatterns?: string[];
};

// SDK 1.5.0's explicit confirmation RPC always waits for a response; it does
// not consult the app's Allow all preferences. Read those user-owned settings
// at each confirmation edge, without caching or changing them. Missing or
// unreadable settings retain interactive confirmation.
export function readHostToolApprovalPolicy(): HostToolApprovalPolicy {
  try {
    const home = process.env.LMSTUDIO_HOME?.trim() || path.join(os.homedir(), ".lmstudio");
    const file = path.join(home, "settings.json");
    if (fs.statSync(file).size > 1024 * 1024) return {};
    const chat = JSON.parse(fs.readFileSync(file, "utf8")).chat;
    return {
      neverAskForToolConfirmation: chat?.neverAskForToolConfirmation === true,
      skipToolConfirmationPatterns: Array.isArray(chat?.skipToolConfirmationPatterns)
        ? chat.skipToolConfirmationPatterns.filter((value: unknown) => typeof value === "string") : [],
    };
  } catch { return {}; }
}

export function hostToolAutoApproval(policy: HostToolApprovalPolicy, provider: string | undefined, name: string): boolean {
  if (!provider || !name) return false;
  return policy.neverAskForToolConfirmation === true
    || Boolean(policy.skipToolConfirmationPatterns?.some(pattern =>
      pattern === `${provider}:*` || pattern === `${provider}:${name}`));
}

export type ToolCallBoundary = {
  state: string;
  rawToolIntent: boolean;
  structuredRequestCount: number;
  sdkStartedCount: number;
  sdkFinalizedCount: number;
  guardAllowedCount: number;
  dispatchCount: number | null;
  resultCount: number;
};

export function classifyToolCallBoundary(options: {
  rawToolIntent: boolean;
  outputLimitStage?: OutputLimitStage;
  structuredRequestCount: number;
  sdkStartedCount: number;
  sdkFinalizedCount: number;
  sdkFailureCount: number;
  guardAllowedCount: number;
  guardDeniedCount: number;
  dispatchCount: number | null;
  resultCount: number;
}): ToolCallBoundary {
  let state = "no_tool_request";
  if (options.rawToolIntent && options.structuredRequestCount === 0) {
    if (options.sdkStartedCount > 0 && options.sdkFinalizedCount === 0) state = "sdk_tool_event_not_finalized";
    else if (options.outputLimitStage === "tool_arguments") state = "raw_text_at_tool_argument_limit";
    else state = "provider_returned_raw_tool_text_without_structured_event";
  } else if (options.structuredRequestCount > 0 && options.sdkFinalizedCount === 0) {
    state = "captured_structured_message_without_sdk_finalization";
  } else if (options.sdkFinalizedCount > 0 && options.guardDeniedCount > 0) {
    state = "guard_denied_before_provider_execution";
  } else if (options.sdkFinalizedCount > 0 && options.guardAllowedCount === 0) {
    state = "guard_not_allowed_or_not_observed";
  } else if (options.guardAllowedCount > 0 && options.dispatchCount === null && options.resultCount === 0) {
    state = "guard_allowed_provider_execution_unknown";
  } else if (options.guardAllowedCount > 0 && options.dispatchCount === 0) {
    state = "guard_allowed_without_provider_dispatch";
  } else if (options.dispatchCount !== null && options.dispatchCount > 0 && options.resultCount === 0) {
    state = "provider_dispatch_without_result";
  } else if (options.resultCount > 0) {
    state = "structured_request_dispatched_and_result_received";
  } else if (options.sdkFailureCount > 0) {
    state = "sdk_tool_event_failed";
  } else if (options.structuredRequestCount > 0) {
    state = "structured_request_captured";
  }
  return {
    state, rawToolIntent: options.rawToolIntent,
    structuredRequestCount: options.structuredRequestCount,
    sdkStartedCount: options.sdkStartedCount,
    sdkFinalizedCount: options.sdkFinalizedCount,
    guardAllowedCount: options.guardAllowedCount,
    dispatchCount: options.dispatchCount,
    resultCount: options.resultCount
  };
}

export function createToolGuard(options: {
  ctl: PredictionLoopHandlerController; emitter: ReturnType<typeof createMessageEmitter>;
  roundTools: Array<RemoteToolLike>; config: DirectConfig; scope: { projectIdentity: string };
  toolPlanningRetryRound: boolean; toolPlanningRetryBlockedFingerprints: Set<string>;
  batchReservation: BatchReservation | null; reservationIds: Map<string, string>;
  toolGeneration: ReturnType<typeof createToolGenerationTracker>;
  traceCall: (id: number, values: Record<string, unknown>) => void;
  readApprovalPolicy?: () => HostToolApprovalPolicy;
}): NonNullable<LLMActionOpts["guardToolCall"]> {
  const { ctl, emitter, roundTools, config, scope, toolPlanningRetryRound, toolPlanningRetryBlockedFingerprints,
    batchReservation, reservationIds, toolGeneration, traceCall } = options;
  return async (_roundIndex, callId, controller) => {
    const request = controller.toolCallRequest;
    // The SDK does not invoke onToolCallRequestFinalized for denied
    // calls, so register the stable call ID at the confirmation edge.
    emitter.registerRequest(callId, request);
    const allowedTool = roundTools.find((tool) => tool.name === request.name);
    if (!allowedTool) {
      traceCall(callId, { validationState: "denied_scope", executionState: "not_executed" });
      controller.deny("Tool withheld by the deterministic project-engine scope.");
      return;
    }
    const projectBindingIdentity = config.observeOnly ? "" : scope.projectIdentity;
    const boundArguments = bindProjectArguments(
      allowedTool as ScopedTool, request, projectBindingIdentity,
    );
    const proposedArguments = boundArguments || request.arguments || {};
    if (readOnlyOperationProfiles.has(allowedTool)
      && !isObservationOnlyToolCall(allowedTool, { ...request, arguments: proposedArguments })) {
      traceCall(callId, { validationState: "denied_read_profile_operation", executionState: "not_executed" });
      controller.deny("Operation is outside the published read-only profile.");
      return;
    }
    const retryFingerprint = telemetryFingerprint({ name: request.name, arguments: proposedArguments });
    if (toolPlanningRetryRound && toolPlanningRetryBlockedFingerprints.has(retryFingerprint)) {
      traceCall(callId, { validationState: "denied_duplicate_retry", executionState: "not_executed" });
      controller.deny("Fresh tool-planning retry cannot repeat an already successful identical read.");
      return;
    }
    if (isObservationOnlyToolCall(allowedTool, { ...request, arguments: proposedArguments })) {
      const reservationId = String(callId);
      const reservation = batchReservation
        ? reserveReadResult(batchReservation, reservationId, allowedTool, proposedArguments)
        : { allowed: true, arguments: proposedArguments, reservedTokens: 0, bounded: false };
      if (!reservation.allowed) {
        traceCall(callId, { validationState: "denied_batch_budget", executionState: "not_executed" });
        controller.deny("Shared read-result budget exhausted. Split the read batch; use the returned evidence before requesting more.");
        return;
      }
      reservationIds.set(String(request.id), reservationId);
      toolGeneration.executing(callId);
      traceCall(callId, {
        validationState: "allowed",
        approvalState: "not_required_observation",
        executionState: "unknown", guardAllowed: true,
        proposedArgumentsFingerprint: telemetryFingerprint(request.arguments || {}),
        executedArgumentsFingerprint: telemetryFingerprint(reservation.arguments),
        reservedToolResultTokens: reservation.reservedTokens,
        resultLimitBounded: reservation.bounded,
      });
      if (boundArguments || reservation.arguments !== proposedArguments) controller.allowAndOverrideParameters(reservation.arguments);
      else controller.allow();
      return;
    }
    ctl.guardAbort();
    const pluginIdentifier = toolPluginIdentifier(roundTools, request.name);
    const autoApproved = hostToolAutoApproval(
      (options.readApprovalPolicy || readHostToolApprovalPolicy)(), pluginIdentifier, request.name);
    // The host renders confirmation controls inside a toolStatus card, matched
    // by callId. A plain loading status and a confirmation request are invisible.
    emitter.emitRequest(callId, { ...request, arguments: proposedArguments });
    toolGeneration.approvalCard(callId, request.id, autoApproved);
    const decision = autoApproved ? { type: "allow" as const } : await ctl.requestConfirmToolCall({
      callId,
      pluginIdentifier,
      name: request.name,
      parameters: proposedArguments,
    });
    ctl.guardAbort();
    if (decision.type === "deny") {
      toolGeneration.denied(callId, decision.denyReason);
      traceCall(callId, { approvalState: "denied", executionState: "not_executed" });
      controller.deny(decision.denyReason);
    }
    else {
      const confirmedArguments = decision.toolArgsOverride || proposedArguments;
      const reboundArguments = bindProjectArguments(
        allowedTool as ScopedTool,
        { ...request, arguments: confirmedArguments },
        projectBindingIdentity,
      );
      const finalArguments = reboundArguments || confirmedArguments;
      emitter.replaceRequest(callId, { ...request, arguments: finalArguments });
      if (boundArguments || decision.toolArgsOverride || reboundArguments) {
        controller.allowAndOverrideParameters(finalArguments);
      } else controller.allow();
      toolGeneration.executing(callId);
      traceCall(callId, {
        validationState: "allowed",
        approvalState: autoApproved ? "allowed_host_policy" : "allowed",
        executionState: "unknown", guardAllowed: true,
        proposedArgumentsFingerprint: telemetryFingerprint(request.arguments || {}),
        executedArgumentsFingerprint: telemetryFingerprint(finalArguments),
      });
    }
  };
}
