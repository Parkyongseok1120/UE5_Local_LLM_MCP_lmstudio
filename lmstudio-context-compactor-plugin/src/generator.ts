import { Chat, type ChatMessage, type GeneratorController } from "@lmstudio/sdk";
import { configSchematics } from "./config";

// The core is intentionally dependency-free so it can be unit-tested outside LM Studio.
// @ts-ignore CommonJS core is shipped beside the plugin entrypoint.
import core = require("./compaction-core.js");
// @ts-ignore CommonJS store is shipped beside the plugin entrypoint.
import store = require("./checkpoint-store.js");

function configValue(ctl: GeneratorController, key: any, fallback: unknown) {
  try {
    const value = ctl.getPluginConfig(configSchematics).get(key);
    return value === undefined || value === null ? fallback : value;
  } catch {
    return fallback;
  }
}

function finiteNumber(value: unknown, fallback: number, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(maximum, Math.max(minimum, parsed));
}

async function loadCheckpointBestEffort(sessionId: string): Promise<any | null> {
  try {
    const checkpoint = await store.loadCheckpoint(sessionId);
    if (checkpoint && !core.validateCheckpoint(checkpoint)) {
      console.warn(`[unreal-context-compactor] Ignoring invalid checkpoint for ${sessionId}.`);
      return null;
    }
    return checkpoint;
  } catch (error: any) {
    console.warn(`[unreal-context-compactor] Checkpoint load failed; continuing without it: ${error?.message || error}`);
    return null;
  }
}

async function saveCheckpointBestEffort(sessionId: string, checkpoint: any): Promise<void> {
  try {
    await store.saveCheckpoint(sessionId, checkpoint);
  } catch (error: any) {
    console.warn(`[unreal-context-compactor] Checkpoint save failed; generation will continue: ${error?.message || error}`);
  }
}

async function appendEventBestEffort(sessionId: string, event: any): Promise<void> {
  try {
    await store.appendEvent(sessionId, event);
  } catch (error: any) {
    console.warn(`[unreal-context-compactor] Telemetry write failed; generation will continue: ${error?.message || error}`);
  }
}

function fragmentOptions(fragment: any): any {
  return {
    tokenCount: Number.isFinite(Number(fragment?.tokensCount)) ? Number(fragment.tokensCount) : undefined,
    containsDrafted: typeof fragment?.containsDrafted === "boolean" ? fragment.containsDrafted : undefined,
    reasoningType: fragment?.reasoningType,
    isStructural: typeof fragment?.isStructural === "boolean" ? fragment.isStructural : undefined,
  };
}

function plainMessages(history: Chat): ChatMessage[] {
  return history.getMessagesArray();
}

function buildCompactedChat(history: Chat, checkpoint: any, recentTurns: number): Chat {
  const source = plainMessages(history);
  const result = Chat.empty();
  const nonPinned: ChatMessage[] = [];
  const systemMessages: ChatMessage[] = [];
  let firstUserMessage: ChatMessage | null = null;
  let firstUserKept = false;

  for (const message of source) {
    const role = message.getRole();
    if (role === "system") {
      systemMessages.push(message);
    } else if (role === "user" && !firstUserKept) {
      firstUserMessage = message;
      firstUserKept = true;
    } else {
      nonPinned.push(message);
    }
  }

  const snapshots = core.compactSnapshots(source, checkpoint, { recentCompleteTurns: recentTurns });
  const checkpointMessage = snapshots.find((message: any) =>
    message.role === "system" && String(message.text || "").startsWith("Conversation checkpoint"),
  );
  const systemSections = systemMessages.map((message) => message.getText()).filter((text) => text.trim());
  if (checkpointMessage) systemSections.push(checkpointMessage.text);
  if (systemSections.length > 0) result.append("system", systemSections.join("\n\n"));
  if (firstUserMessage) result.append(firstUserMessage);

  const tailCount = Math.max(1, recentTurns * 2);
  const tailSnapshots = core.snapshotMessages(nonPinned);
  const tailStart = core.completeTailStart(tailSnapshots, Math.max(0, tailSnapshots.length - tailCount));
  for (const message of nonPinned.slice(tailStart)) result.append(message);
  return result;
}

async function compactToTarget(
  model: any,
  history: Chat,
  checkpoint: any,
  config: any,
  contextLength: number,
  reservedTokens: number,
): Promise<{ chat: Chat; inputTokens: number; remainingTokens: number; retainedTurns: number }> {
  let retainedTurns = Math.max(0, Math.floor(Number(config.recentCompleteTurns || 0)));
  let best: { chat: Chat; inputTokens: number; remainingTokens: number; retainedTurns: number } | null = null;
  const target = Math.max(Number(config.hardRemainingTokens), Number(config.targetRemainingTokensAfterCompaction));
  while (retainedTurns >= 0) {
    const chat = buildCompactedChat(history, checkpoint, retainedTurns);
    const formatted = await model.applyPromptTemplate(chat);
    const inputTokens = await model.countTokens(formatted);
    const remainingTokens = Number(contextLength) - Number(inputTokens) - Number(reservedTokens);
    if (!best || remainingTokens > best.remainingTokens) {
      best = { chat, inputTokens, remainingTokens, retainedTurns };
    }
    if (remainingTokens >= target || retainedTurns === 0) break;
    retainedTurns -= 1;
  }
  if (!best) throw new Error("Context compaction could not construct a model-facing chat.");
  if (best.remainingTokens < Number(config.hardRemainingTokens)) {
    throw new Error(
      `Context remains below the hard safety margin after maximum compaction: ${best.remainingTokens} tokens remain. `
      + "Reduce the system prompt/tool schema or load the model with a larger context length.",
    );
  }
  return best;
}
function requestedToolName(request: any): string {
  return String(request?.name || "").trim();
}

function toolNamesMatch(expected: string, actual: string): boolean {
  return core.toolNamesMatch(expected, actual);
}

function validateToolRequest(request: any, checkpoint: any): { ok: boolean; reason?: string } {
  const required = checkpoint?.requiredNextTool?.name;
  if (required && !toolNamesMatch(required, requestedToolName(request))) {
    return { ok: false, reason: `requiredNextTool=${required}; received=${requestedToolName(request)}` };
  }
  const completed = new Set(checkpoint?.completedToolCallIds || []);
  if (request?.id && completed.has(request.id)) {
    return { ok: false, reason: `tool call id already completed: ${request.id}` };
  }
  return { ok: true };
}

async function generate(ctl: GeneratorController, history: Chat): Promise<void> {
  const enabled = Boolean(configValue(ctl, "enabled", true));
  const observeOnly = Boolean(configValue(ctl, "observeOnly", false));
  const configuredTargetModel = String(configValue(ctl, "targetModel", "") || "").trim();

  const messages = plainMessages(history);
  let workingDirectory = "";
  try {
    workingDirectory = String(ctl.getWorkingDirectory() || "");
  } catch {
    workingDirectory = "";
  }

  let model: any;
  let resolvedTargetModel = configuredTargetModel;
  let autoSelected = false;
  if (configuredTargetModel) {
    model = await ctl.client.llm.model(configuredTargetModel);
  } else {
    const loaded = await ctl.client.llm.listLoaded();
    if (loaded.length !== 1) {
      const names = loaded.map((item: any) => item.identifier || item.modelKey).join(", ") || "(none)";
      throw new Error(`Set targetModel because automatic selection requires exactly one loaded LLM. Loaded: ${names}`);
    }
    model = loaded[0];
    resolvedTargetModel = String(model.identifier || model.modelKey || "auto-selected");
    autoSelected = true;
  }

  const sessionId = core.sessionFingerprint(messages, `${workingDirectory}\n${resolvedTargetModel}`);
  let checkpoint = await loadCheckpointBestEffort(sessionId);
  if (autoSelected) {
    await appendEventBestEffort(sessionId, {
      type: "target_model_auto_selected",
      at: new Date().toISOString(),
      targetModel: resolvedTargetModel,
    });
  }

  const pendingCalls = [
    ...(Array.isArray(checkpoint?.pendingToolCalls) ? checkpoint.pendingToolCalls : []),
    ...(checkpoint?.pendingToolCall ? [checkpoint.pendingToolCall] : []),
  ];
  if (checkpoint && pendingCalls.length > 0) {
    const currentSnapshots = core.snapshotMessages(messages);
    const completed = currentSnapshots.flatMap((message: any) => message.toolResults || []);
    const anonymousCompletedCount = completed.filter((result: any) => !result.toolCallId).length;
    const matchedIds: string[] = [];
    const remainingPending = pendingCalls.filter((pending: any) => {
      const pendingId = pending?.id || null;
      const observedResultCount = Number(pending?.observedToolResultCount || 0);
      const hasAnonymousBaseline = Number.isFinite(Number(pending?.observedAnonymousToolResultCount));
      const matched = pendingId
        ? completed.some((result: any) => result.toolCallId === pendingId)
        : (hasAnonymousBaseline
          ? anonymousCompletedCount > Number(pending.observedAnonymousToolResultCount)
          : completed.length > observedResultCount);
      if (matched && pendingId) matchedIds.push(String(pendingId));
      return !matched;
    });
    if (remainingPending.length !== pendingCalls.length) {
      checkpoint.completedToolCallIds = [
        ...(checkpoint.completedToolCallIds || []),
        ...matchedIds,
      ].filter((id: string, index: number, ids: string[]) => ids.indexOf(id) === index).slice(-256);
      checkpoint.pendingToolCall = null;
      checkpoint.pendingToolCalls = remainingPending;
      await saveCheckpointBestEffort(sessionId, checkpoint);
    }
  }

  const currentFormatted = await model.applyPromptTemplate(history);
  const inputTokens = await model.countTokens(currentFormatted);
  const contextLength = await model.getContextLength();
  const toolDefinitions = ctl.getToolDefinitions();
  const toolSchemaTokens = await model.countTokens(JSON.stringify(toolDefinitions));
  const nextToolName = checkpoint?.requiredNextTool?.name || "";
  const hardRemainingTokens = finiteNumber(configValue(ctl, "hardRemainingTokens", 5000), 5000);
  const config = {
    enabled,
    observeOnly,
    strictToolControlPlane: Boolean(configValue(ctl, "strictToolControlPlane", false)),
    softRemainingTokens: finiteNumber(configValue(ctl, "softRemainingTokens", 10000), 10000, hardRemainingTokens),
    hardRemainingTokens,
    maxOutputReserve: finiteNumber(configValue(ctl, "maxOutputReserve", 4096), 4096, 1),
    temperature: finiteNumber(configValue(ctl, "temperature", 0.1), 0.1, 0, 1),
    normalToolResultReserve: finiteNumber(configValue(ctl, "normalToolResultReserve", 3000), 3000),
    buildToolResultReserve: finiteNumber(configValue(ctl, "buildToolResultReserve", 8000), 8000),
    recentCompleteTurns: Math.floor(finiteNumber(configValue(ctl, "recentCompleteTurns", 6), 6, 0, 100)),
    minimumTurnsBetweenCompactions: Math.floor(finiteNumber(configValue(ctl, "minimumTurnsBetweenCompactions", 3), 3, 0, 100)),
    targetRemainingTokensAfterCompaction: finiteNumber(
      configValue(ctl, "targetRemainingTokensAfterCompaction", 20000), 20000, hardRemainingTokens,
    ),
  };
  const decision = core.budgetDecision({ contextLength, inputTokens, nextToolName, config, toolSchemaTokens });

  console.info(
    `[unreal-context-compactor] Proxy active: target=${resolvedTargetModel} `
    + `input=${inputTokens} context=${contextLength} action=${decision.action}`,
  );

  await appendEventBestEffort(sessionId, {
    type: "context_measurement",
    at: new Date().toISOString(),
    proxyActive: true,
    targetModel: resolvedTargetModel,
    inputTokens,
    contextLength,
    decision,
  });

  const nextCheckpoint = core.buildCheckpoint(messages, checkpoint || {}, { maxCheckpointFacts: 32 });
  nextCheckpoint.compactionGeneration = Number(checkpoint?.compactionGeneration || 0);

  let modelChat = history;
  const lastCompactionCount = Number(checkpoint?.lastCompactionSourceMessageCount || 0);
  const messagesSinceLastCompaction = Math.max(0, messages.length - lastCompactionCount);
  let effectiveAction = decision.action;
  if (
    decision.action === "soft_compact"
    && lastCompactionCount > 0
    && messagesSinceLastCompaction < Number(config.minimumTurnsBetweenCompactions)
  ) {
    effectiveAction = "deferred";
  }
  const shouldCompact = effectiveAction === "soft_compact" || effectiveAction === "hard_compact";
  let compactedMetrics: any = null;
  if (shouldCompact) {
    nextCheckpoint.compactionGeneration += 1;
    const applied = Boolean(!observeOnly && enabled);
    if (applied) {
      compactedMetrics = await compactToTarget(
        model, history, nextCheckpoint, config, contextLength, decision.reservedTokens,
      );
      modelChat = compactedMetrics.chat;
      nextCheckpoint.lastCompactionSourceMessageCount = messages.length;
    }
    await saveCheckpointBestEffort(sessionId, nextCheckpoint);
    if (applied && compactedMetrics) modelChat = compactedMetrics.chat;
    await appendEventBestEffort(sessionId, {
      type: "compaction_decision",
      at: new Date().toISOString(),
      action: decision.action,
      effectiveAction,
      applied,
      checkpointGeneration: nextCheckpoint.checkpointGeneration,
      postInputTokens: compactedMetrics?.inputTokens,
      postRemainingTokens: compactedMetrics?.remainingTokens,
      retainedTurns: compactedMetrics?.retainedTurns,
    });
  } else if (effectiveAction === "deferred") {
    // Record why a soft threshold did not compact this turn.
    await appendEventBestEffort(sessionId, {
      type: "compaction_decision",
      at: new Date().toISOString(),
      action: decision.action,
      effectiveAction,
      applied: false,
      messagesSinceLastCompaction,
    });
  }
  const events: any[] = [];
  const requests: any[] = [];
  const strictToolControlPlane = Boolean(config.strictToolControlPlane);
  const emitEvent = (event: any) => {
    if (event.kind === "fragment") ctl.fragmentGenerated(event.content, event.opts);
    else if (event.kind === "start") ctl.toolCallGenerationStarted({ toolCallId: event.toolCallId });
    else if (event.kind === "name") ctl.toolCallGenerationNameReceived(event.name);
    else if (event.kind === "args") ctl.toolCallGenerationArgumentFragmentGenerated(event.content);
    else if (event.kind === "end") ctl.toolCallGenerationEnded(event.request);
    else if (event.kind === "failure") ctl.toolCallGenerationFailed(new Error(event.error));
  };
  const recordEvent = (event: any) => {
    if (strictToolControlPlane) events.push(event);
    else emitEvent(event);
  };
  const prediction = model.respond(modelChat, {
    maxTokens: Number(config.maxOutputReserve),
    temperature: Number(config.temperature),
    ...(toolDefinitions.length > 0 ? { rawTools: { type: "toolArray", tools: toolDefinitions } } : {}),
    contextOverflowPolicy: "stopAtLimit",
    signal: ctl.abortSignal,
    onPredictionFragment(fragment: any) {
      recordEvent({
        kind: "fragment",
        content: String(fragment.content || ""),
        opts: fragmentOptions(fragment),
      });
    },
    onToolCallRequestStart(callId: number, info: any) {
      recordEvent({ kind: "start", callId, toolCallId: info?.toolCallId });
    },
    onToolCallRequestNameReceived(callId: number, name: string) {
      recordEvent({ kind: "name", callId, name });
    },
    onToolCallRequestArgumentFragmentGenerated(callId: number, content: string) {
      recordEvent({ kind: "args", callId, content });
    },
    onToolCallRequestEnd(callId: number, info: any) {
      const request = info?.toolCallRequest || {};
      requests.push({ callId, request });
      recordEvent({ kind: "end", callId, request });
    },
    onToolCallRequestFailure(callId: number, error: Error) {
      recordEvent({ kind: "failure", callId, error: String(error?.message || error) });
    },
  });
  await prediction.result();

  const verdictByCallId = new Map<number, { ok: boolean; reason?: string }>();
  for (const entry of requests) {
    const verdict = strictToolControlPlane
      ? validateToolRequest(entry.request, nextCheckpoint)
      : { ok: true };
    verdictByCallId.set(entry.callId, verdict);
    if (!verdict.ok) {
      await appendEventBestEffort(sessionId, {
        type: "tool_call_rejected",
        at: new Date().toISOString(),
        request: entry.request,
        reason: verdict.reason,
      });
    }
  }

  if (strictToolControlPlane) {
    for (const event of events) {
      if (event.kind !== "end") {
        emitEvent(event);
        continue;
      }
      const verdict = verdictByCallId.get(event.callId) || { ok: true };
      if (verdict.ok) emitEvent(event);
      else ctl.toolCallGenerationFailed(new Error(`Tool call rejected by control plane: ${verdict.reason}`));
    }
  }

  const acceptedRequests = requests.filter((entry) => verdictByCallId.get(entry.callId)?.ok !== false);
  if (acceptedRequests.length > 0) {
    const observedResults = core.snapshotMessages(messages)
      .flatMap((message: any) => message.toolResults || []);
    const observedToolResultCount = observedResults.length;
    const observedAnonymousToolResultCount = observedResults
      .filter((result: any) => !result.toolCallId).length;
    let anonymousRequestOffset = 0;
    nextCheckpoint.pendingToolCall = null;
    nextCheckpoint.pendingToolCalls = acceptedRequests.map((entry) => {
      const pending = {
        ...entry.request,
        observedToolResultCount,
      } as any;
      if (!entry.request?.id) {
        pending.observedAnonymousToolResultCount = observedAnonymousToolResultCount + anonymousRequestOffset;
        anonymousRequestOffset += 1;
      }
      return pending;
    });
    await saveCheckpointBestEffort(sessionId, nextCheckpoint);
  }
}

export { generate };
// End of module.
