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

async function saveCheckpointBestEffort(
  sessionId: string,
  checkpoint: any,
): Promise<{ ok: boolean; error?: string }> {
  try {
    await store.saveCheckpoint(sessionId, checkpoint);
    return { ok: true };
  } catch (error: any) {
    const message = String(error?.message || error);
    console.warn(`[unreal-context-compactor] Checkpoint save failed: ${message}`);
    return { ok: false, error: message };
  }
}

async function persistCheckpoint(
  sessionId: string,
  checkpoint: any,
  required: boolean,
  stage: string,
): Promise<void> {
  const saved = await saveCheckpointBestEffort(sessionId, checkpoint);
  if (!saved.ok && required) {
    throw new Error(
      `Context safety checkpoint could not be persisted (${stage}): ${saved.error || "unknown error"}. `
      + "Generation was stopped before unsafe output was committed.",
    );
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

function snapshotToChatContent(snapshot: any): any[] {
  const role = String(snapshot?.role || "assistant");
  const text = String(snapshot?.text || "");
  const toolCalls = Array.isArray(snapshot?.toolCalls) ? snapshot.toolCalls : [];
  const toolResults = Array.isArray(snapshot?.toolResults) ? snapshot.toolResults : [];
  if (role === "tool") {
    return toolResults.map((result: any) => ({
      type: "toolCallResult",
      toolCallId: result.toolCallId || null,
      content: String(result.content || ""),
    }));
  }
  const content: any[] = [];
  if (text) content.push({ type: "text", text });
  for (const call of toolCalls) {
    content.push({
      type: "toolCallRequest",
      toolCallRequest: {
        id: call.id || null,
        type: "function",
        name: call.name || "",
        arguments: call.arguments || {},
      },
    });
  }
  if (content.length === 0) content.push({ type: "text", text: "" });
  return content;
}

function buildCompactedChat(
  history: Chat,
  checkpoint: any,
  recentTurns: number,
  options: {
    trailingMetaUser?: ChatMessage | null;
    maxCurrentTurnMessages?: number;
  } = {},
): Chat {
  const source = plainMessages(history);
  const trailingMetaText = options.trailingMetaUser
    ? String(options.trailingMetaUser.getText() || "").trim()
    : "";
  const snapshots = core.compactSnapshots(source, checkpoint, {
    recentCompleteTurns: recentTurns,
    maxCurrentTurnMessages: options.maxCurrentTurnMessages,
    trailingMetaUser: trailingMetaText
      ? { role: "user", text: trailingMetaText, toolCalls: [], toolResults: [] }
      : undefined,
  });

  // LM Studio's applyPromptTemplate/respond IPC only serializes the inbound history
  // Chat (or an asMutableCopy of it). Chat.empty()/Chat.from() created inside the
  // generator produce getText()-visible messages but ~10-token empty prompts.
  const mutableCopy = typeof (history as any).asMutableCopy === "function"
    ? (history as any).asMutableCopy()
    : null;
  const result: Chat = mutableCopy || Chat.empty();
  while (result.getLength() > 0) result.pop();
  for (const snapshot of snapshots) {
    const role = String(snapshot?.role || "assistant");
    const content = snapshotToChatContent(snapshot);
    if (role === "tool" && content.length === 0) continue;
    if ((role === "system" || role === "user" || role === "assistant")
      && content.every((part: any) => part?.type === "text" && !String(part.text || "").trim())
      && !content.some((part: any) => part?.type === "toolCallRequest")) {
      continue;
    }
    if (mutableCopy) {
      result.append({ role, content } as any);
    } else if (role === "system" || role === "user" || role === "assistant") {
      const text = content.filter((part: any) => part?.type === "text").map((part: any) => part.text).join("");
      if (role === "assistant" && content.some((part: any) => part?.type === "toolCallRequest")) {
        result.append({ role, content } as any);
      } else {
        result.append(role, text);
      }
    } else {
      result.append({ role, content } as any);
    }
  }

  // #region agent log
  try {
    const users = result.getMessagesArray().filter((m) => m.getRole() === "user");
    const systems = result.getMessagesArray().filter((m) => m.getRole() === "system");
    fetch("http://127.0.0.1:7430/ingest/0688ca65-d016-4b7d-bcca-51d06f27568c", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "49b048" },
      body: JSON.stringify({
        sessionId: "49b048",
        runId: "post-fix",
        hypothesisId: "H18",
        location: "generator.ts:buildCompactedChat",
        message: "compacted chat via asMutableCopy",
        data: {
          recentTurns,
          usedMutableCopy: Boolean(mutableCopy),
          snapshotCount: snapshots.length,
          resultLength: result.getMessagesArray().length,
          systemCount: systems.length,
          systemLen: String(systems[0]?.getText() || "").length,
          userCount: users.length,
          latestUserPreview: String(users.at(-1)?.getText() || "").slice(0, 80),
          latestUserTextLen: String(users.at(-1)?.getText() || "").trim().length,
          hasTrailingMeta: Boolean(trailingMetaText),
        },
        timestamp: Date.now(),
      }),
    }).catch(() => {});
  } catch {
    /* ignore */
  }
  // #endregion

  return result;
}

function measureCurrentTurnLength(history: Chat): number {
  const source = plainMessages(history);
  let latestUserIndex = -1;
  for (let index = 0; index < source.length; index += 1) {
    const message = source[index];
    const text = String(message.getText() || "");
    if (message.getRole() === "user" && text.trim() && !core.isMetaUserMessage(text)) {
      latestUserIndex = index;
    }
  }
  if (latestUserIndex < 0) return 0;
  let count = 0;
  for (let index = latestUserIndex + 1; index < source.length; index += 1) {
    const message = source[index];
    const role = message.getRole();
    const text = String(message.getText() || "");
    if (role === "system") continue;
    if (role === "user" && core.isMetaUserMessage(text)) continue;
    count += 1;
  }
  return count;
}

async function compactToTarget(
  model: any,
  history: Chat,
  checkpoint: any,
  config: any,
  contextLength: number,
  reservedTokens: number,
  options: {
    trailingMetaUser?: ChatMessage | null;
  } = {},
): Promise<{ chat: Chat; inputTokens: number; remainingTokens: number; retainedTurns: number; currentTurnCap: number | null }> {
  let retainedTurns = Math.max(0, Math.floor(Number(config.recentCompleteTurns || 0)));
  let currentTurnCap: number | null = null;
  let best: {
    chat: Chat;
    inputTokens: number;
    remainingTokens: number;
    retainedTurns: number;
    currentTurnCap: number | null;
  } | null = null;
  const target = Math.max(Number(config.hardRemainingTokens), Number(config.targetRemainingTokensAfterCompaction));
  const hard = Number(config.hardRemainingTokens);
  const currentTurnLength = measureCurrentTurnLength(history);

  while (true) {
    const chat = buildCompactedChat(history, checkpoint, retainedTurns, {
      trailingMetaUser: options.trailingMetaUser || null,
      maxCurrentTurnMessages: currentTurnCap === null ? undefined : currentTurnCap,
    });
    let formatted: string;
    let inputTokens: number;
    try {
      formatted = await model.applyPromptTemplate(chat);
      inputTokens = await model.countTokens(formatted);
    } catch (error) {
      // #region agent log
      try {
        const fs = require("node:fs") as typeof import("node:fs");
        const debugLog = process.env.LMS_CONTEXT_COMPACTOR_DEBUG_LOG;
        if (debugLog) {
          fs.appendFileSync(debugLog, `${JSON.stringify({
            sessionId: "49b048",
            runId: "post-fix",
            hypothesisId: "H14",
            location: "generator.ts:compactToTarget",
            message: "applyPromptTemplate failed",
            data: {
              retainedTurns,
              chatLen: chat.getMessagesArray().length,
              roles: chat.getMessagesArray().map((m) => m.getRole()),
              userLen: String(chat.getMessagesArray().filter((m) => m.getRole() === "user").at(-1)?.getText() || "").length,
              error: String((error as any)?.message || error).slice(0, 300),
            },
            timestamp: Date.now(),
          })}\n`);
        }
      } catch { /* ignore */ }
      // #endregion
      throw error;
    }
    // #region agent log
    try {
      const fs = require("node:fs") as typeof import("node:fs");
      const debugLog = process.env.LMS_CONTEXT_COMPACTOR_DEBUG_LOG;
      if (debugLog) {
        fs.appendFileSync(debugLog, `${JSON.stringify({
          sessionId: "49b048",
          runId: "post-fix",
          hypothesisId: "H14",
          location: "generator.ts:compactToTarget",
          message: "compact iteration tokens",
          data: {
            retainedTurns,
            inputTokens,
            chatLen: chat.getMessagesArray().length,
            systemCount: chat.getMessagesArray().filter((m) => m.getRole() === "system").length,
            userLen: String(chat.getMessagesArray().filter((m) => m.getRole() === "user").at(-1)?.getText() || "").trim().length,
            fmtPreview: String(formatted || "").slice(0, 160),
          },
          timestamp: Date.now(),
        })}\n`);
      }
    } catch { /* ignore */ }
    // #endregion
    const userPreview = String(
      chat.getMessagesArray().filter((m) => m.getRole() === "user").at(-1)?.getText() || "",
    ).trim();
    const promptLostUser = Boolean(
      userPreview.length >= 8
      && !String(formatted).includes(userPreview.slice(0, Math.min(16, userPreview.length))),
    );
    if (inputTokens < 20 || promptLostUser) {
      throw new Error(
        `Context compaction produced an near-empty/desynced model prompt (${inputTokens} tokens`
        + `${promptLostUser ? ", user text missing from template" : ""}). `
        + "Refusing to continue with an empty/amnesiac chat.",
      );
    }
    const remainingTokens = Number(contextLength) - Number(inputTokens) - Number(reservedTokens);
    if (!best || remainingTokens > best.remainingTokens) {
      best = { chat, inputTokens, remainingTokens, retainedTurns, currentTurnCap };
    }
    // #region agent log
    try {
      fetch("http://127.0.0.1:7430/ingest/0688ca65-d016-4b7d-bcca-51d06f27568c", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "49b048" },
        body: JSON.stringify({
          sessionId: "49b048",
          runId: "post-fix-review",
          hypothesisId: "H8c",
          location: "generator.ts:compactToTarget",
          message: "compact iteration",
          data: {
            retainedTurns,
            currentTurnLength,
            currentTurnCap,
            inputTokens,
            remainingTokens,
            target,
            hard,
            chatLen: chat.getMessagesArray().length,
            hasTrailingMeta: Boolean(options.trailingMetaUser),
          },
          timestamp: Date.now(),
        }),
      }).catch(() => {});
    } catch {
      /* ignore */
    }
    // #endregion
    if (remainingTokens >= target) break;
    if (retainedTurns > 0) {
      retainedTurns -= 1;
      continue;
    }
    // Cap must be based on in-flight turn length, not full chat length
    // (systems/checkpoint/user would otherwise make the first trim a no-op).
    if (currentTurnCap === null) {
      if (currentTurnLength <= 0) break;
      currentTurnCap = Math.max(0, currentTurnLength - 2);
    } else if (currentTurnCap > 0) {
      currentTurnCap = Math.max(0, currentTurnCap - 2);
    } else {
      break;
    }
    if (remainingTokens >= hard && currentTurnCap === 0) break;
  }

  if (!best) throw new Error("Context compaction could not construct a model-facing chat.");
  if (best.remainingTokens < hard) {
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
  const requireCheckpointPersistence = Boolean(configValue(ctl, "requireCheckpointPersistence", true));
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

  let unresolvedPendingCalls: any[] = [
    ...(Array.isArray(checkpoint?.pendingToolCalls) ? checkpoint.pendingToolCalls : []),
    ...(checkpoint?.pendingToolCall ? [checkpoint.pendingToolCall] : []),
  ];
  if (checkpoint && unresolvedPendingCalls.length > 0) {
    const currentSnapshots = core.snapshotMessages(messages);
    const completed = currentSnapshots.flatMap((message: any) => message.toolResults || []);
    const anonymousCompletedCount = completed.filter((result: any) => !result.toolCallId).length;
    const matchedIds: string[] = [];
    const remainingPending = unresolvedPendingCalls.filter((pending: any) => {
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
    if (remainingPending.length !== unresolvedPendingCalls.length) {
      checkpoint.completedToolCallIds = [
        ...(checkpoint.completedToolCallIds || []),
        ...matchedIds,
      ].filter((id: string, index: number, ids: string[]) => ids.indexOf(id) === index).slice(-256);
      checkpoint.pendingToolCall = null;
      checkpoint.pendingToolCalls = remainingPending;
      await persistCheckpoint(
        sessionId,
        checkpoint,
        requireCheckpointPersistence,
        "pending_tool_result_reconciliation",
      );
    }
    unresolvedPendingCalls = remainingPending;
  }
  if (unresolvedPendingCalls.length > 0) {
    await appendEventBestEffort(sessionId, {
      type: "generation_blocked",
      at: new Date().toISOString(),
      reason: "pending_tool_result_missing",
      pendingToolCalls: unresolvedPendingCalls.map((pending: any) => ({
        id: pending?.id || null,
        name: pending?.name || "",
      })),
    });
    throw new Error(
      `Generation is paused because ${unresolvedPendingCalls.length} prior tool call(s) still lack a result. `
      + "Wait for LM Studio to record the tool result, or resolve/cancel the failed tool call before sending another message.",
    );
  }

  const currentFormatted = await model.applyPromptTemplate(history);
  const inputTokens = await model.countTokens(currentFormatted);
  const contextLength = await model.getContextLength();
  const toolDefinitions = ctl.getToolDefinitions();
  const toolSchemaTokens = await model.countTokens(JSON.stringify(toolDefinitions));
  const nextToolName = checkpoint?.requiredNextTool?.name || "";
  const hardRemainingTokens = finiteNumber(configValue(ctl, "hardRemainingTokens", 8000), 8000);
  const config = {
    enabled,
    observeOnly,
    strictToolControlPlane: Boolean(configValue(ctl, "strictToolControlPlane", false)),
    bufferUntilPredictionComplete: Boolean(configValue(ctl, "bufferUntilPredictionComplete", true)),
    rejectTruncatedPredictions: Boolean(configValue(ctl, "rejectTruncatedPredictions", true)),
    requireCheckpointPersistence,
    softRemainingTokens: finiteNumber(configValue(ctl, "softRemainingTokens", 14000), 14000, hardRemainingTokens),
    hardRemainingTokens,
    maxOutputReserve: finiteNumber(configValue(ctl, "maxOutputReserve", 4096), 4096, 1),
    safetyMarginTokens: finiteNumber(configValue(ctl, "safetyMarginTokens", 1024), 1024),
    temperature: finiteNumber(configValue(ctl, "temperature", 0.1), 0.1, 0, 1),
    normalToolResultReserve: finiteNumber(configValue(ctl, "normalToolResultReserve", 3000), 3000),
    buildToolResultReserve: finiteNumber(configValue(ctl, "buildToolResultReserve", 8000), 8000),
    recentCompleteTurns: Math.floor(finiteNumber(configValue(ctl, "recentCompleteTurns", 1), 1, 0, 100)),
    minimumTurnsBetweenCompactions: Math.floor(finiteNumber(configValue(ctl, "minimumTurnsBetweenCompactions", 0), 0, 0, 100)),
    targetRemainingTokensAfterCompaction: finiteNumber(
      configValue(ctl, "targetRemainingTokensAfterCompaction", 24000), 24000, hardRemainingTokens,
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
    workingDirectory,
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

  // Mid-chat goal switches must compact even when the token budget is still healthy;
  // otherwise prior structure dumps contaminate bug-hunt / follow-up turns.
  const priorObjective = String(checkpoint?.objective || "").trim();
  const latestObjective = String(nextCheckpoint.objective || "").trim();
  const goalChanged = Boolean(
    priorObjective
    && latestObjective
    && priorObjective !== latestObjective
    && !core.isMetaUserMessage(latestObjective),
  );
  const userGoalCount = messages.filter((message) => {
    const text = String(message.getText() || "").trim();
    return message.getRole() === "user" && text && !core.isMetaUserMessage(text);
  }).length;
  const hasPriorAssistant = messages.some((message) => message.getRole() === "assistant");
  const latestIsReadOnly = core.isReadOnlyUserGoal(latestObjective);
  let trailingMetaUser: ChatMessage | null = null;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.getRole() !== "user") continue;
    const text = String(message.getText() || "").trim();
    if (!text) continue;
    if (core.isMetaUserMessage(text)) trailingMetaUser = message;
    break;
  }
  // Force compaction only on a real objective change. Related follow-up questions
  // still change the objective string, so we compact to drop prior dumps — but the
  // compacted chat must keep a single merged system + latest user (see compactSnapshots).
  const goalChangeCompact = Boolean(
    enabled
    && !observeOnly
    && !trailingMetaUser
    && goalChanged,
  );
  // Zero older-tail only on goal change. Never strip the current user/checkpoint;
  // dual-system chats previously collapsed to ~10 empty-user tokens on Qwen.
  const zeroRetainedTurns = Boolean(!trailingMetaUser && goalChanged);
  if (goalChangeCompact && effectiveAction !== "hard_compact") {
    effectiveAction = "soft_compact";
  }

  // #region agent log
  try {
    fetch("http://127.0.0.1:7430/ingest/0688ca65-d016-4b7d-bcca-51d06f27568c", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "49b048" },
      body: JSON.stringify({
        sessionId: "49b048",
        runId: "post-fix-review",
        hypothesisId: "H9",
        location: "generator.ts:generate",
        message: "goal-change and meta gate",
        data: {
          priorObjective: priorObjective.slice(0, 120),
          latestObjective: latestObjective.slice(0, 120),
          goalChanged,
          latestIsReadOnly,
          answeringMeta: Boolean(trailingMetaUser),
          userGoalCount,
          hasPriorAssistant,
          decisionAction: decision.action,
          effectiveAction,
          goalChangeCompact,
          zeroRetainedTurns,
        },
        timestamp: Date.now(),
      }),
    }).catch(() => {});
  } catch {
    /* ignore */
  }
  // #endregion

  const shouldCompact = effectiveAction === "soft_compact" || effectiveAction === "hard_compact";
  let compactedMetrics: any = null;
  if (shouldCompact) {
    nextCheckpoint.compactionGeneration += 1;
    const applied = Boolean(!observeOnly && enabled);
    if (applied) {
      const compactConfig = zeroRetainedTurns
        ? { ...config, recentCompleteTurns: 0 }
        : config;
      compactedMetrics = await compactToTarget(
        model,
        history,
        nextCheckpoint,
        compactConfig,
        contextLength,
        decision.reservedTokens,
        { trailingMetaUser },
      );
      modelChat = compactedMetrics.chat;
      nextCheckpoint.lastCompactionSourceMessageCount = messages.length;
    }
    if (applied && compactedMetrics) modelChat = compactedMetrics.chat;
    await appendEventBestEffort(sessionId, {
      type: "compaction_decision",
      at: new Date().toISOString(),
      action: decision.action,
      effectiveAction,
      goalChangeCompact,
      zeroRetainedTurns,
      answeringMeta: Boolean(trailingMetaUser),
      applied,
      checkpointGeneration: nextCheckpoint.checkpointGeneration,
      postInputTokens: compactedMetrics?.inputTokens,
      postRemainingTokens: compactedMetrics?.remainingTokens,
      retainedTurns: compactedMetrics?.retainedTurns,
      currentTurnCap: compactedMetrics?.currentTurnCap,
      objectivePreview: String(nextCheckpoint.objective || "").slice(0, 160),
    });
  } else {
    await appendEventBestEffort(sessionId, {
      type: "compaction_decision",
      at: new Date().toISOString(),
      action: decision.action,
      effectiveAction,
      goalChangeCompact,
      zeroRetainedTurns,
      answeringMeta: Boolean(trailingMetaUser),
      applied: false,
      messagesSinceLastCompaction,
      objectivePreview: String(nextCheckpoint.objective || "").slice(0, 160),
    });
  }
  await persistCheckpoint(
    sessionId,
    nextCheckpoint,
    requireCheckpointPersistence,
    "before_prediction",
  );
  if (decision.action === "hard_compact" && (!enabled || observeOnly)) {
    await appendEventBestEffort(sessionId, {
      type: "generation_blocked",
      at: new Date().toISOString(),
      reason: enabled ? "observe_only_at_hard_limit" : "compactor_disabled_at_hard_limit",
      decision,
    });
    throw new Error(
      "Context is below the hard safety threshold, but compaction is not active. "
      + "Enable the context compactor and disable observe-only mode before continuing.",
    );
  }
  const events: any[] = [];
  const requests: any[] = [];
  const strictToolControlPlane = Boolean(config.strictToolControlPlane);
  const bufferUntilPredictionComplete = Boolean(config.bufferUntilPredictionComplete)
    || requireCheckpointPersistence
    || Boolean(config.rejectTruncatedPredictions);
  const emitEvent = (event: any) => {
    if (event.kind === "fragment") ctl.fragmentGenerated(event.content, event.opts);
    else if (event.kind === "start") ctl.toolCallGenerationStarted({ toolCallId: event.toolCallId });
    else if (event.kind === "name") ctl.toolCallGenerationNameReceived(event.name);
    else if (event.kind === "args") ctl.toolCallGenerationArgumentFragmentGenerated(event.content);
    else if (event.kind === "end") ctl.toolCallGenerationEnded(event.request);
    else if (event.kind === "failure") ctl.toolCallGenerationFailed(new Error(event.error));
  };
  const recordEvent = (event: any) => {
    if (strictToolControlPlane || bufferUntilPredictionComplete) events.push(event);
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
  const predictionResult: any = await prediction.result();
  const stopReason = String(predictionResult?.stats?.stopReason || "");
  const unsafeStopReasons = new Set(["contextLengthReached", "failed", "modelUnloaded"]);
  const truncated = unsafeStopReasons.has(stopReason)
    || (Boolean(config.rejectTruncatedPredictions) && stopReason === "maxPredictedTokensReached");
  await appendEventBestEffort(sessionId, {
    type: "prediction_completion",
    at: new Date().toISOString(),
    stopReason: stopReason || "unspecified",
    bufferedEventCount: events.length,
    toolRequestCount: requests.length,
    outputCommitted: false,
    outputCommitPending: !truncated,
  });
  if (truncated) {
    const safelyBuffered = strictToolControlPlane || bufferUntilPredictionComplete;
    throw new Error(
      `Model prediction was discarded because it did not complete safely (stopReason=${stopReason}). `
      + (safelyBuffered
        ? "No buffered text or tool call was committed; compact the context or increase the model context/output limit."
        : "Atomic output was explicitly disabled, so already-streamed output may be partial. Enable atomic output before retrying."),
    );
  }

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
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      "pending_tool_calls",
    );
  }

  if (strictToolControlPlane || bufferUntilPredictionComplete) {
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
  await appendEventBestEffort(sessionId, {
    type: "prediction_output_committed",
    at: new Date().toISOString(),
    stopReason: stopReason || "unspecified",
    emittedEventCount: events.length,
    toolRequestCount: requests.length,
    outputCommitted: true,
  });
}

export { generate };
// End of module.
