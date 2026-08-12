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

function debugAgentLog(hypothesisId: string, location: string, message: string, data: Record<string, unknown>) {
  // #region agent log
  try {
    if (process.env.LMS_CONTEXT_COMPACTOR_DEBUG_INGEST !== "1") return;
    const fs = require("node:fs") as typeof import("node:fs");
    const payload = {
      sessionId: String(process.env.LMS_CONTEXT_COMPACTOR_DEBUG_SESSION_ID || "context-compactor"),
      runId: String(process.env.LMS_CONTEXT_COMPACTOR_DEBUG_RUN || "release-harden"),
      hypothesisId,
      location,
      message,
      data,
      timestamp: Date.now(),
    };
    const debugLog = process.env.LMS_CONTEXT_COMPACTOR_DEBUG_LOG;
    if (debugLog) {
      fs.appendFileSync(debugLog, `${JSON.stringify(payload)}\n`);
    }
    const endpoint = String(process.env.LMS_CONTEXT_COMPACTOR_DEBUG_INGEST_URL || "").trim();
    if (endpoint) {
      fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Debug-Session-Id": payload.sessionId },
        body: JSON.stringify(payload),
      }).catch(() => {});
    }
  } catch {
    /* ignore */
  }
  // #endregion
}

function tryInjectSessionMarker(history: Chat, marker: string): boolean {
  const tag = core.formatSessionMarker(marker);
  if (!tag) return false;
  try {
    const messages = history.getMessagesArray();
    for (const message of messages) {
      if (message.getRole() !== "system") continue;
      const text = String(message.getText() || "");
      if (core.SESSION_MARKER_RE.test(text)) return true;
      if (typeof (message as any).appendText === "function") {
        (message as any).appendText(`\n${tag}`);
        return true;
      }
      if (typeof (message as any).replaceText === "function") {
        (message as any).replaceText(`${text}\n${tag}`);
        return true;
      }
    }
    if (typeof (history as any).append === "function") {
      (history as any).append("system", tag);
      return true;
    }
  } catch {
    return false;
  }
  return false;
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

  const users = result.getMessagesArray().filter((m) => m.getRole() === "user");
  const systems = result.getMessagesArray().filter((m) => m.getRole() === "system");
  debugAgentLog("H18", "generator.ts:buildCompactedChat", "compacted chat via asMutableCopy", {
    recentTurns,
    usedMutableCopy: Boolean(mutableCopy),
    snapshotCount: snapshots.length,
    resultLength: result.getMessagesArray().length,
    systemCount: systems.length,
    systemLen: String(systems[0]?.getText() || "").length,
    userCount: users.length,
    latestUserTextLen: String(users.at(-1)?.getText() || "").trim().length,
    hasTrailingMeta: Boolean(trailingMetaText),
  });

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
      debugAgentLog("H14", "generator.ts:compactToTarget", "applyPromptTemplate failed", {
        retainedTurns,
        chatLen: chat.getMessagesArray().length,
        roles: chat.getMessagesArray().map((m) => m.getRole()),
        userLen: String(chat.getMessagesArray().filter((m) => m.getRole() === "user").at(-1)?.getText() || "").length,
        error: String((error as any)?.message || error).slice(0, 300),
      });
      throw error;
    }
    debugAgentLog("H14", "generator.ts:compactToTarget", "compact iteration tokens", {
      retainedTurns,
      inputTokens,
      chatLen: chat.getMessagesArray().length,
      systemCount: chat.getMessagesArray().filter((m) => m.getRole() === "system").length,
      userLen: String(chat.getMessagesArray().filter((m) => m.getRole() === "user").at(-1)?.getText() || "").trim().length,
      fmtPreview: String(formatted || "").slice(0, 160),
    });
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
    debugAgentLog("H8c", "generator.ts:compactToTarget", "compact iteration", {
      retainedTurns,
      currentTurnLength,
      currentTurnCap,
      inputTokens,
      remainingTokens,
      target,
      hard,
      chatLen: chat.getMessagesArray().length,
      hasTrailingMeta: Boolean(options.trailingMetaUser),
    });
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

const ARCHITECTURE_GATE_MARKER = "[UNREAL_ARCHITECTURE_VALIDATION_GATE]";
const ARCHITECTURE_SUBMISSION_MARKER = "[UNREAL_ARCHITECTURE_SUBMISSION_REQUIRED]";
const ARCHITECTURE_PAYLOAD_REPAIR_MARKER = "[UNREAL_ARCHITECTURE_PAYLOAD_REPAIR_REQUIRED]";
const ARCHITECTURE_TOOL_NAME = "unreal_architecture_reasoning";
const FEATURE_INTENT_ATOMIC_MARKER = "[UNREAL_FEATURE_INTENT_ATOMIC_GATE]";
const FEATURE_INTENT_TOOL_NAME = "unreal_feature_intent_resolve";
const TASK_PLANNER_TOOL_NAME = "unreal_agent_plan";
const TASK_ROUTE_OWNERSHIP_MARKER = "[UNREAL_TASK_ROUTE_OWNERSHIP_GATE]";
const ARCHITECTURE_EVIDENCE_TOOLS = [
  "read_file",
  "read_file_range",
  "read_symbol",
  "unreal_symbol_lookup",
];
const ARCHITECTURE_DISCOVERY_TOOLS = [
  ...ARCHITECTURE_EVIDENCE_TOOLS,
  "search_files",
  "list_directory",
  "unreal_get_active_project",
  "get_workspace_info",
];

function latestUserGoalText(messages: ChatMessage[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.getRole() !== "user") continue;
    const value = String(message.getText() || "").trim();
    if (value && !core.isMetaUserMessage(value)) return value;
  }
  return "";
}

function injectTaskRouteOwnershipRule(chat: Chat, plannerAvailable: boolean): boolean {
  const rule = (
    `${TASK_ROUTE_OWNERSHIP_MARKER}\n`
    + "Project mutation tools are unavailable until an MCP server returns a server-issued task route. "
    + "Never construct, guess, or repair taskAuthorization. "
    + (plannerAvailable
      ? "Before any write, call unreal_agent_plan once with the original user request and continue with its returned route."
      : "The mcp/unreal-rag planner provider is missing from this chat. You may inspect source, but do not claim implementation or attempt writes; report that mcp/unreal-rag must be enabled.")
  );
  try {
    const messages = chat.getMessagesArray();
    for (const message of messages) {
      if (message.getRole() !== "system") continue;
      const current = String(message.getText() || "");
      if (current.includes(TASK_ROUTE_OWNERSHIP_MARKER)) return true;
      if (typeof (message as any).appendText === "function") {
        (message as any).appendText(`\n${rule}`);
        return true;
      }
      if (typeof (message as any).replaceText === "function") {
        (message as any).replaceText(`${current}\n${rule}`);
        return true;
      }
    }
    chat.append("system", rule);
    return true;
  } catch {
    return false;
  }
}

function architectureContractGoalText(
  messages: ChatMessage[],
  latestGoal: string,
  includeRecoveryHistory: boolean,
): string {
  if (!includeRecoveryHistory) return String(latestGoal || "");
  const goals: string[] = [];
  for (const message of messages) {
    if (message.getRole() !== "user") continue;
    const value = String(message.getText() || "").trim();
    if (!value || core.isMetaUserMessage(value)) continue;
    goals.push(value);
  }
  return goals.slice(-8).join("\n");
}

function requiresArchitectureValidation(goal: string, toolDefinitions: any[]): boolean {
  const hasTool = (toolDefinitions || []).some((tool: any) => {
    const name = tool?.function?.name || tool?.name || "";
    return toolNamesMatch("unreal_architecture_reasoning", name);
  });
  if (!hasTool) return false;
  const textValue = String(goal || "");
  return /templates_lobby|authoritative\s+multiplayer|architecture|architectural|structure\s+design|design\s+validation|구조\s*설계|설계\s*검증|아키텍처/i.test(textValue);
}

function architectureRecoveryContinuationRequested(goal: string): boolean {
  return /\b(?:continue|resume|retry|same\s+validation|this\s+validation)\b|계속|이어|재개|검증|그대로/i.test(
    String(goal || ""),
  );
}

function injectArchitectureValidationRule(chat: Chat): boolean {
  const rule = (
    `${ARCHITECTURE_GATE_MARKER}\n`
    + "For an architecture/design-validation objective, investigate direct project source first, then submit the "
    + "self-derived proposal to unreal_architecture_reasoning. Do not provide the final design until "
    + "proposalValidation.ok is true. If validation fails, materially revise the rejected ownership, concrete "
    + "caller-to-authority path, truth-source inventory, lifecycle recovery, validation matrix, or slice contract "
    + "and validate once more. Prove that a client-originated Server RPC is invoked on an actor/component actually "
    + "owned by that client's connection, and ground every claimed existing participant/roster truth source in "
    + "direct project or inherited framework evidence. If proposalValidation.repairStrategy is full_replan or "
    + "repairSubmission.mode is fullProposal, reuse already-read direct-source evidence while the source snapshot "
    + "is unchanged; re-read only when source changed, evidence is missing, or needed lines were not covered. Then "
    + "submit one complete proposal; never use proposalPatch/proposalRepairs or preserve the rejected central owner. "
    + "Otherwise apply every returned repair "
    + "requirement at its exact jsonPath and prefer repairSubmission.argumentShape with one complete replacement value "
    + "per path. Never repeat a jsonPath for individual array rows. "
    + "Keep private reasoning brief and submit a compact proposal: one concise sentence per scalar field and only "
    + "the evidence needed for the contract, targeting under 2500 output tokens before the tool call completes. "
    + "Never replace a concrete callable path with an unresolved 'RPC or local call' option."
  );
  try {
    const messages = chat.getMessagesArray();
    for (const message of messages) {
      if (message.getRole() !== "system") continue;
      const current = String(message.getText() || "");
      if (current.includes(ARCHITECTURE_GATE_MARKER)) return true;
      if (typeof (message as any).appendText === "function") {
        (message as any).appendText(`\n${rule}`);
        return true;
      }
      if (typeof (message as any).replaceText === "function") {
        (message as any).replaceText(`${current}\n${rule}`);
        return true;
      }
    }
    if (typeof (chat as any).append === "function") {
      (chat as any).append("system", rule);
      return true;
    }
  } catch {
    return false;
  }
  return false;
}

function injectFeatureIntentAtomicRule(chat: Chat): boolean {
  const rule = (
    `${FEATURE_INTENT_ATOMIC_MARKER}\n`
    + "Submit exactly one unreal_feature_intent_resolve model-facing call for this gate. If selectedSlice.files "
    + "is empty, include every already-discovered bounded 1-2 file slice in its slices argument and select one "
    + "with activeSliceId. SelectIntent, ResolveSlice, CaptureSnapshot, and BindIntent are server-owned internal "
    + "phases. Never call unreal_task_define_slices separately for feature intent."
  );
  try {
    const messages = chat.getMessagesArray();
    for (const message of messages) {
      if (message.getRole() !== "system") continue;
      const current = String(message.getText() || "");
      if (current.includes(FEATURE_INTENT_ATOMIC_MARKER)) return true;
      if (typeof (message as any).appendText === "function") {
        (message as any).appendText(`\n${rule}`);
        return true;
      }
      if (typeof (message as any).replaceText === "function") {
        (message as any).replaceText(`${current}\n${rule}`);
        return true;
      }
    }
    if (typeof (chat as any).append === "function") {
      (chat as any).append("system", rule);
      return true;
    }
  } catch {
    return false;
  }
  return false;
}

function injectArchitectureSubmissionRule(chat: Chat): boolean {
  const rule = (
    `${ARCHITECTURE_SUBMISSION_MARKER}\n`
    + "Enough direct-source evidence has been collected for this architecture-validation turn. Submit your own "
    + "complete proposal to unreal_architecture_reasoning now. The validator tool is forced: do not emit a final "
    + "design, call another discovery tool, or ask the user to retry. If a prior proposal failed, follow its retained "
    + "repairSubmission contract and continue until proposalValidation.ok is true."
  );
  try {
    const messages = chat.getMessagesArray();
    for (const message of messages) {
      if (message.getRole() !== "system") continue;
      const current = String(message.getText() || "");
      if (current.includes(ARCHITECTURE_SUBMISSION_MARKER)) return true;
      if (typeof (message as any).appendText === "function") {
        (message as any).appendText(`\n${rule}`);
        return true;
      }
      if (typeof (message as any).replaceText === "function") {
        (message as any).replaceText(`${current}\n${rule}`);
        return true;
      }
    }
    if (typeof (chat as any).append === "function") {
      (chat as any).append("system", rule);
      return true;
    }
  } catch {
    return false;
  }
  return false;
}

function injectArchitectureCoreChangeRule(chat: Chat, jsonPaths: string[]): boolean {
  const paths = [...new Set(
    (jsonPaths || []).map((path) => String(path || "").trim()).filter(Boolean),
  )].slice(0, 24);
  if (!paths.length) return false;
  const rule = (
    "[UNREAL_ARCHITECTURE_CORE_CHANGE_REQUIRED]\n"
    + `The previous full proposal was rejected because these implicated paths remained structurally unchanged: ${paths.join(", ")}. `
    + "Submit a complete independently re-derived proposal now. Every listed path must materially differ from the "
    + "rejected payload while satisfying direct-source evidence. These are negative constraints only; derive all "
    + "replacement values yourself and do not emit a final answer before validator success."
  );
  try {
    const messages = chat.getMessagesArray();
    for (const message of messages) {
      if (message.getRole() !== "system") continue;
      const current = String(message.getText() || "");
      if (current.includes("[UNREAL_ARCHITECTURE_CORE_CHANGE_REQUIRED]")) {
        if (typeof (message as any).replaceText === "function") {
          (message as any).replaceText(
            current.replace(
              /\[UNREAL_ARCHITECTURE_CORE_CHANGE_REQUIRED\][\s\S]*?(?=\n\[|$)/,
              rule,
            ),
          );
        }
        return true;
      }
      if (typeof (message as any).appendText === "function") {
        (message as any).appendText(`\n${rule}`);
        return true;
      }
      if (typeof (message as any).replaceText === "function") {
        (message as any).replaceText(`${current}\n${rule}`);
        return true;
      }
    }
    if (typeof (chat as any).append === "function") {
      (chat as any).append("system", rule);
      return true;
    }
  } catch {
    return false;
  }
  return false;
}

function injectArchitecturePayloadRepairRule(chat: Chat, jsonPaths: string[]): boolean {
  const paths = [...new Set(
    (jsonPaths || []).map((path) => String(path || "").trim()).filter(Boolean),
  )].slice(0, 64);
  if (!paths.length) return false;
  const rule = (
    `${ARCHITECTURE_PAYLOAD_REPAIR_MARKER}\n`
    + "The previous validator call was withheld locally because its tool payload omitted required JSON-schema "
    + `paths: ${paths.join(", ")}. Reissue exactly one complete unreal_architecture_reasoning call now. `
    + "Populate every listed field from your own source-grounded design; the paths specify serialization shape "
    + "only and do not supply design values. Do not emit final text before validator success."
  );
  try {
    const messages = chat.getMessagesArray();
    for (const message of messages) {
      if (message.getRole() !== "system") continue;
      const current = String(message.getText() || "");
      if (current.includes(ARCHITECTURE_PAYLOAD_REPAIR_MARKER)) {
        if (typeof (message as any).replaceText === "function") {
          (message as any).replaceText(
            current.replace(
              /\[UNREAL_ARCHITECTURE_PAYLOAD_REPAIR_REQUIRED\][\s\S]*?(?=\n\[|$)/,
              rule,
            ),
          );
        }
        return true;
      }
      if (typeof (message as any).appendText === "function") {
        (message as any).appendText(`\n${rule}`);
        return true;
      }
      if (typeof (message as any).replaceText === "function") {
        (message as any).replaceText(`${current}\n${rule}`);
        return true;
      }
    }
    if (typeof (chat as any).append === "function") {
      (chat as any).append("system", rule);
      return true;
    }
  } catch {
    return false;
  }
  return false;
}

function trailingMetaUserMessage(messages: ChatMessage[]): ChatMessage | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.getRole() !== "user") continue;
    const text = String(message.getText() || "").trim();
    if (!text) continue;
    return core.isMetaUserMessage(text) ? message : null;
  }
  return null;
}

function architectureGateStatus(messages: ChatMessage[], checkpoint: any): {
  attempted: boolean;
  validated: boolean;
  directEvidenceCount: number;
  declarationEvidenceCount: number;
  implementationEvidenceCount: number;
  evidenceCallsSinceLastAttempt: number;
  discoveryCallsSinceLastAttempt: number;
  uniqueEvidenceSinceLastAttempt: number;
  lastValidationFailed: boolean;
  lastRepairStrategy: string;
  lastRepairMode: string;
  requiresFullProposal: boolean;
  lastErrorCode: string;
  unchangedCorePaths: string[];
  stagedContractRequired: boolean;
  networkedContractRequired: boolean;
} {
  const snapshots = core.snapshotMessages(messages);
  const resultsById = new Map<string, any>();
  for (const snapshot of snapshots) {
    for (const result of snapshot.toolResults || []) {
      const id = String(result?.toolCallId || "").trim();
      if (id) resultsById.set(id, result);
    }
  }
  let attempted = Boolean(checkpoint?.architectureProposal);
  let validated = checkpoint?.architectureProposal?.validationOk === true;
  let lastValidationFailed = checkpoint?.architectureProposal?.validationOk === false;
  let lastRepairStrategy = String(checkpoint?.architectureProposal?.repairStrategy || "").trim();
  let lastRepairMode = String(checkpoint?.architectureProposal?.repairMode || "").trim();
  let requiresFullProposal = Boolean(
    checkpoint?.architectureProposal?.requiresFullReplan
    || lastRepairStrategy === "full_replan"
    || lastRepairMode === "fullProposal",
  );
  let lastErrorCode = String(checkpoint?.architectureProposal?.lastErrorCode || "").trim();
  let unchangedCorePaths = Array.isArray(checkpoint?.architectureProposal?.unchangedCorePaths)
    ? checkpoint.architectureProposal.unchangedCorePaths.map((path: any) => String(path || "").trim()).filter(Boolean)
    : [];
  let stagedContractRequired = checkpoint?.architectureProposal?.stagedContractRequired === true;
  let networkedContractRequired = Boolean(
    checkpoint?.architectureProposal?.networkedContractRequired === true
    || unchangedCorePaths.some((path: string) => path === "networking" || path.startsWith("networking.")),
  );
  const evidence = new Set<string>();
  const declarationEvidence = new Set<string>();
  const implementationEvidence = new Set<string>();
  const evidenceSinceLastAttempt = new Set<string>();
  let evidenceCallsSinceLastAttempt = 0;
  let discoveryCallsSinceLastAttempt = 0;
  for (const snapshot of snapshots) {
    for (const call of snapshot.toolCalls || []) {
      const name = String(call?.name || "").trim();
      const result = resultsById.get(String(call?.id || "").trim());
      if (toolNamesMatch(ARCHITECTURE_TOOL_NAME, name)) {
        attempted = true;
        evidenceCallsSinceLastAttempt = 0;
        discoveryCallsSinceLastAttempt = 0;
        evidenceSinceLastAttempt.clear();
        if (result) {
          for (const payload of core.parseJsonObjects(result.content)) {
            const payloadErrorCode = String(payload?.errorCode || "").trim();
            if (payloadErrorCode) lastErrorCode = payloadErrorCode;
            unchangedCorePaths = (
              payloadErrorCode === "ARCHITECTURE_PROPOSAL_REPLAN_CORE_UNCHANGED"
              && Array.isArray(payload?.requiredChangedPaths)
            )
              ? payload.requiredChangedPaths.map((path: any) => String(path || "").trim()).filter(Boolean).slice(0, 24)
              : [];
            const validation = payload?.proposalValidation;
            const designContract = validation?.designContract;
            if (typeof designContract?.stagedImplementation === "boolean") {
              stagedContractRequired = designContract.stagedImplementation;
            }
            if (typeof designContract?.networkedProposal === "boolean") {
              networkedContractRequired = designContract.networkedProposal;
            }
            if (validation?.ok === true) {
              validated = true;
              lastValidationFailed = false;
              requiresFullProposal = false;
            } else if (validation?.ok === false) {
              validated = false;
              lastValidationFailed = true;
            }
            const repairStrategy = String(validation?.repairStrategy || "").trim();
            const repairMode = String(payload?.repairSubmission?.mode || "").trim();
            if (repairStrategy) lastRepairStrategy = repairStrategy;
            if (repairMode) lastRepairMode = repairMode;
            const currentRequiresFullProposal = Boolean(
              validation?.designContract?.requiresFullReplan === true
              || repairStrategy === "full_replan"
              || repairMode === "fullProposal",
            );
            if (validation?.ok === true) {
              requiresFullProposal = false;
            } else if (validation?.ok === false || repairStrategy || repairMode) {
              // The newest validator decision replaces the persisted mode. Keeping
              // a prior full-replan flag sticky after the validator has narrowed the
              // issue to proposalRepairs makes the model regenerate the entire
              // proposal and can reintroduce already-fixed ownership mistakes.
              requiresFullProposal = currentRequiresFullProposal;
            }
          }
        }
        continue;
      }
      if (!result || !core.toolResultSucceeded(result)) continue;
      if (ARCHITECTURE_DISCOVERY_TOOLS.some((tool) => toolNamesMatch(tool, name))) {
        discoveryCallsSinceLastAttempt += 1;
      }
      if (!ARCHITECTURE_EVIDENCE_TOOLS.some((tool) => toolNamesMatch(tool, name))) continue;
      const args = call?.arguments && typeof call.arguments === "object" ? call.arguments : {};
      const sourceIdentity = String(
        args.path || args.filePath || args.symbol || args.symbolName || args.query || call.id || "",
      ).trim();
      const rangeIdentity = [args.startLine, args.endLine, args.lineStart, args.lineEnd]
        .filter((value) => value !== undefined && value !== null && String(value).trim())
        .join(":");
      const identity = sourceIdentity
        ? `${name.toLowerCase()}:${sourceIdentity}${rangeIdentity ? `:${rangeIdentity}` : ""}`
        : "";
      if (!identity) continue;
      evidence.add(identity);
      evidenceSinceLastAttempt.add(identity);
      evidenceCallsSinceLastAttempt += 1;
      const normalizedSource = sourceIdentity.replace(/\\/g, "/").toLowerCase();
      if (/\.(?:h|hh|hpp|hxx|inl)$/.test(normalizedSource)) declarationEvidence.add(identity);
      if (
        /\.(?:c|cc|cpp|cxx|m|mm|cs)$/.test(normalizedSource)
        || ["read_symbol", "unreal_symbol_lookup"].some((tool) => toolNamesMatch(tool, name))
      ) {
        implementationEvidence.add(identity);
      }
    }
  }
  return {
    attempted,
    validated,
    directEvidenceCount: evidence.size,
    declarationEvidenceCount: declarationEvidence.size,
    implementationEvidenceCount: implementationEvidence.size,
    evidenceCallsSinceLastAttempt,
    discoveryCallsSinceLastAttempt,
    uniqueEvidenceSinceLastAttempt: evidenceSinceLastAttempt.size,
    lastValidationFailed,
    lastRepairStrategy,
    lastRepairMode,
    requiresFullProposal,
    lastErrorCode,
    unchangedCorePaths,
    stagedContractRequired,
    networkedContractRequired,
  };
}

function appendRequired(schema: any, fields: string[]): void {
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) return;
  schema.required = [...new Set([
    ...(Array.isArray(schema.required) ? schema.required : []),
    ...fields,
  ])];
}

function architectureSubmissionTool(
  tool: any,
  requireCompleteProposal: boolean,
  options: { stagedContract?: boolean; networkedContract?: boolean } = {},
): any {
  if (!requireCompleteProposal) return tool;
  let cloned: any;
  try {
    cloned = JSON.parse(JSON.stringify(tool));
  } catch {
    return tool;
  }
  const callable = cloned?.function && typeof cloned.function === "object"
    ? cloned.function
    : cloned;
  const parameters = callable?.parameters;
  if (!parameters || typeof parameters !== "object" || Array.isArray(parameters)) return cloned;
  const properties = parameters.properties;
  if (!properties || typeof properties !== "object" || !("proposal" in properties)) return cloned;
  appendRequired(parameters, ["proposal"]);
  const proposal = properties.proposal;
  if (!proposal || typeof proposal !== "object" || Array.isArray(proposal)) return cloned;
  const proposalProperties = proposal.properties;
  if (!proposalProperties || typeof proposalProperties !== "object") return cloned;
  if (options.stagedContract) {
    appendRequired(proposal, [
      "decision",
      "invariants",
      "impactedSurfaces",
      "validationPlan",
      "alternatives",
      "selectedAlternative",
      "selectionRationale",
      "implementationFiles",
      "ownership",
      "stateInventory",
      "lifecycleTransitions",
      "migrationPlan",
      "validationMatrix",
      "implementationSlices",
    ]);
    for (const field of [
      "alternatives",
      "implementationFiles",
      "stateInventory",
      "lifecycleTransitions",
      "migrationPlan",
      "validationMatrix",
      "implementationSlices",
    ]) {
      const fieldSchema = proposalProperties[field];
      if (fieldSchema && typeof fieldSchema === "object" && !Array.isArray(fieldSchema)) {
        fieldSchema.minItems = field === "alternatives" ? 2 : 1;
      }
    }
    appendRequired(proposalProperties.ownership, [
      "stateOwner",
      "dataOwner",
      "lifecycleOwner",
      "failurePolicy",
      "recoveryPolicy",
    ]);
    const matrixInvariant = proposalProperties.validationMatrix?.items?.properties?.invariant;
    if (matrixInvariant && typeof matrixInvariant === "object") {
      matrixInvariant.description = (
        "Must exactly equal one string from proposal.invariants so validation coverage is machine-checkable."
      );
    }
    const sliceInvariants = proposalProperties.implementationSlices?.items?.properties?.invariants;
    if (sliceInvariants && typeof sliceInvariants === "object") {
      sliceInvariants.description = (
        "Every entry must exactly equal one string from proposal.invariants; do not invent slice-only invariants."
      );
    }
  }
  if (options.networkedContract && proposalProperties.networking) {
    appendRequired(proposal, ["networking"]);
    appendRequired(proposalProperties.networking, [
      "authorityOwner",
      "clientInitiated",
      "requestPath",
      "rpcOwner",
      "owningConnection",
      "serverValidation",
      "replicatedState",
    ]);
    const requestPath = proposalProperties.networking?.properties?.requestPath;
    if (requestPath && typeof requestPath === "object") requestPath.minItems = 3;
  }
  return cloned;
}

function requiredSchemaLeafPaths(schema: any, basePath: string, depth = 0): string[] {
  if (!schema || typeof schema !== "object" || depth > 12) return basePath ? [basePath] : [];
  const required = Array.isArray(schema.required)
    ? schema.required.map((field: any) => String(field || "").trim()).filter(Boolean)
    : [];
  const properties = schema.properties && typeof schema.properties === "object"
    ? schema.properties
    : {};
  if (!required.length) return basePath ? [basePath] : [];
  const paths: string[] = [];
  for (const field of required) {
    const childPath = basePath ? `${basePath}.${field}` : field;
    const childSchema = properties[field];
    const descendants = requiredSchemaLeafPaths(childSchema, childPath, depth + 1);
    paths.push(...(descendants.length ? descendants : [childPath]));
  }
  return paths;
}

function schemaContractViolationPaths(
  schema: any,
  value: any,
  basePath = "",
  depth = 0,
): string[] {
  if (!schema || typeof schema !== "object" || depth > 12) return [];
  const schemaTypes = Array.isArray(schema.type) ? schema.type : [schema.type].filter(Boolean);
  const expectsObject = schemaTypes.includes("object") || Boolean(schema.properties || schema.required);
  const expectsArray = schemaTypes.includes("array");
  if (expectsObject) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      const requiredLeaves = requiredSchemaLeafPaths(schema, basePath, depth);
      return requiredLeaves.length ? requiredLeaves : [basePath || "$arguments"];
    }
    const required = Array.isArray(schema.required)
      ? schema.required.map((field: any) => String(field || "").trim()).filter(Boolean)
      : [];
    const properties = schema.properties && typeof schema.properties === "object"
      ? schema.properties
      : {};
    const violations: string[] = [];
    for (const field of required) {
      const childPath = basePath ? `${basePath}.${field}` : field;
      if (!Object.prototype.hasOwnProperty.call(value, field) || value[field] == null) {
        const requiredLeaves = requiredSchemaLeafPaths(properties[field], childPath, depth + 1);
        violations.push(...(requiredLeaves.length ? requiredLeaves : [childPath]));
        continue;
      }
      violations.push(...schemaContractViolationPaths(
        properties[field], value[field], childPath, depth + 1,
      ));
    }
    for (const [field, childSchema] of Object.entries(properties)) {
      if (required.includes(field) || !Object.prototype.hasOwnProperty.call(value, field)) continue;
      const childPath = basePath ? `${basePath}.${field}` : field;
      violations.push(...schemaContractViolationPaths(
        childSchema, value[field], childPath, depth + 1,
      ));
    }
    return violations;
  }
  if (expectsArray) {
    if (!Array.isArray(value)) return [basePath || "$arguments"];
    const minItems = Number(schema.minItems || 0);
    if (Number.isFinite(minItems) && value.length < minItems) return [basePath || "$arguments"];
    if (schema.items && typeof schema.items === "object") {
      return value.flatMap((item: any, index: number) => schemaContractViolationPaths(
        schema.items, item, `${basePath}[${index}]`, depth + 1,
      ));
    }
  }
  return [];
}

function architecturePayloadViolationPaths(request: any, tool: any): string[] {
  if (!toolNamesMatch(ARCHITECTURE_TOOL_NAME, requestedToolName(request))) return [];
  const callable = tool?.function && typeof tool.function === "object" ? tool.function : tool;
  const parameters = callable?.parameters;
  const args = request?.arguments && typeof request.arguments === "object"
    ? request.arguments
    : {};
  return [...new Set(schemaContractViolationPaths(parameters, args))].slice(0, 64);
}

function stagedArchitectureContractRequired(goal: string): boolean {
  return /templates_lobby|implementation\s+slice|migration\s+(?:order|plan)|lifecycle|alternative|ownership|구현\s*슬라이스|마이그레이션|생명주기|대안|소유권/i.test(
    String(goal || ""),
  );
}

function networkedArchitectureContractRequired(goal: string): boolean {
  const source = String(goal || "");
  const term = /authoritative|authority|multiplayer|replication|network|\brpc\b|\bserver\b|\bclient\b|멀티플레이|네트워크|리플리케이션|서버|클라이언트|권한/gi;
  for (const match of source.matchAll(term)) {
    const start = Number(match.index || 0);
    const before = source.slice(Math.max(0, start - 48), start);
    const after = source.slice(start + String(match[0] || "").length, start + 120);
    const negated = (
      /(?:do\s+not|don't|dont|without|exclude|excluding|out\s+of\s+scope)[^.!?\n]{0,48}$/i.test(before)
      || /^(?:[^.!?\n]{0,72})(?:do\s+not|don't|dont|without|exclude|excluding|out\s+of\s+scope)/i.test(after)
      || /(?:하지\s*마|말고|제외|건드리지|필요\s*없|사용하지|대상\s*아님)[^.!?\n]{0,32}$/i.test(before)
      || /^(?:[^.!?\n]{0,72})(?:하지\s*마|말고|제외|건드리지|필요\s*없|사용하지|대상\s*아님)/i.test(after)
    );
    if (!negated) return true;
  }
  return false;
}

function architectureDiscoveryToolAllowed(name: string): boolean {
  return toolNamesMatch(ARCHITECTURE_TOOL_NAME, name)
    || ARCHITECTURE_DISCOVERY_TOOLS.some((tool) => toolNamesMatch(tool, name));
}

const SESSION_SCOPED_ANALYSIS_TOOLS = [
  "unreal_rag_search",
  "unreal_agent_session",
  "unreal_architecture_reasoning",
  "list_directory",
  "read_file",
  "read_file_range",
  "read_symbol",
  "search_files",
];

function enrichToolRequestSession(request: any, sessionId: string): any {
  const name = requestedToolName(request);
  if (!SESSION_SCOPED_ANALYSIS_TOOLS.some((tool) => toolNamesMatch(tool, name))) return request;
  const args = request?.arguments && typeof request.arguments === "object"
    ? request.arguments
    : {};
  if (String(args.sessionId || args.session_id || "").trim()) return request;
  return {
    ...request,
    arguments: { ...args, sessionId },
  };
}

function replaceBufferedArgumentFragments(events: any[], callId: number, args: any): void {
  const matches = events.filter((event) => event.kind === "args" && event.callId === callId);
  if (matches.length === 0) return;
  matches[0].content = JSON.stringify(args || {});
  for (const event of matches.slice(1)) event.content = "";
}

const RECOVERY_CONTROL_TOOLS = [
  "unreal_task_checkpoint",
  "unreal_task_status",
  "unreal_task_recover_active",
  "unreal_task_cancel",
  "unreal_task_cancel_active",
];

// tools/list can be refreshed independently by the unreal-rag and
// unreal-agent MCP servers. LM Studio may therefore hand the generator a
// short-lived union containing a tool from the previous task phase. Keep the
// server call-time authorization as the final authority, but defensively
// intersect Unreal work tools with the latest server-owned route before the
// model sees them. Non-Unreal MCP tools are deliberately left untouched.
const ALWAYS_DISCOVERABLE_UNREAL_TOOLS = [
  "unreal_get_active_project",
  "unreal_set_active_project",
  "unreal_rag_health",
  "unreal_agent_plan",
  "unreal_task_status",
  "unreal_task_list_active",
  "unreal_task_recover_active",
  "unreal_task_cancel_active",
  "unreal_task_quarantine_corrupt",
  "unreal_task_retry_job_cancel",
  "unreal_task_define_slices",
  "unreal_task_resume",
  "unreal_task_cancel",
  "get_workspace_info",
  "get_active_project",
  "list_active_tasks",
  "cancel_active_task",
  "quarantine_corrupt_task",
];

const UNPREFIXED_UNREAL_AGENT_TOOLS = [
  ...ALWAYS_DISCOVERABLE_UNREAL_TOOLS.filter((name) => !name.startsWith("unreal_")),
  "list_directory",
  "read_file",
  "read_file_range",
  "read_symbol",
  "search_files",
  "write_file",
  "replace_in_file",
  "apply_edit_bundle",
  "static_validate_project",
  "build_unreal_project",
  "run_unreal_automation_tests",
  "read_unreal_logs",
  "write_session_handoff",
  "record_bootstrap_step",
];

const TASK_CHECKPOINT_TOOL_NAME = "unreal_task_checkpoint";

function isRecoveryControlTool(name: string): boolean {
  return RECOVERY_CONTROL_TOOLS.some((control) => toolNamesMatch(control, name));
}

function isUnrealStackTool(name: string): boolean {
  const normalized = String(name || "").trim().toLowerCase();
  return normalized.includes("unreal_")
    || UNPREFIXED_UNREAL_AGENT_TOOLS.some((known) => toolNamesMatch(known, normalized));
}

function routeAllowsTool(tool: any, checkpoint: any): boolean {
  const name = String(tool?.function?.name || tool?.name || "").trim();
  if (!isUnrealStackTool(name)) return true;

  const required = String(checkpoint?.requiredNextTool?.name || "").trim();
  if (required && !core.isNonToolNextAction(required) && toolNamesMatch(required, name)) {
    return true;
  }
  // A checkpoint is not a normal always-visible status control. It is exposed
  // only for an exact server-requested continuity handoff, otherwise compact
  // models tend to poll it instead of performing routed work.
  if (toolNamesMatch(TASK_CHECKPOINT_TOOL_NAME, name)) return false;
  if (ALWAYS_DISCOVERABLE_UNREAL_TOOLS.some((control) => toolNamesMatch(control, name))) {
    return true;
  }
  const activeTools = Array.isArray(checkpoint?.toolRoute?.activeTools)
    ? checkpoint.toolRoute.activeTools.map((item: any) => String(item || "").trim()).filter(Boolean)
    : [];
  return activeTools.some((active: string) => toolNamesMatch(active, name));
}

function validateToolRequest(request: any, checkpoint: any): { ok: boolean; reason?: string } {
  const required = checkpoint?.requiredNextTool?.name;
  const actual = requestedToolName(request);
  if (
    required
    && !core.isNonToolNextAction(required)
    && !toolNamesMatch(required, actual)
    && !isRecoveryControlTool(actual)
  ) {
    return { ok: false, reason: `requiredNextTool=${required}; received=${actual}` };
  }
  if (
    required
    && toolNamesMatch(required, actual)
    && !core.toolArgumentsSatisfy(checkpoint?.requiredNextTool?.args, request?.arguments)
    && !isRecoveryControlTool(actual)
  ) {
    return { ok: false, reason: `requiredNextTool=${required} arguments do not satisfy the server-owned requiredNextToolArgs` };
  }
  const completed = new Set(checkpoint?.completedToolCallIds || []);
  if (request?.id && completed.has(request.id)) {
    return { ok: false, reason: `tool call id already completed: ${request.id}` };
  }
  return { ok: true };
}

function reconcilePendingToolCalls(pendingCalls: any[], currentSnapshots: any[]): {
  remainingPending: any[];
  matchedIds: string[];
  abandonedIds: string[];
} {
  const completed = currentSnapshots.flatMap((message: any) => message.toolResults || []);
  const activeCallIds = new Set(
    currentSnapshots
      .flatMap((message: any) => message.toolCalls || [])
      .map((call: any) => String(call?.id || "").trim())
      .filter(Boolean),
  );
  const anonymousCompletedCount = completed.filter((result: any) => !result.toolCallId).length;
  const matchedIds: string[] = [];
  const abandonedIds: string[] = [];
  const remainingPending = pendingCalls.filter((pending: any) => {
    const pendingId = String(pending?.id || "").trim();
    const observedResultCount = Number(pending?.observedToolResultCount || 0);
    const hasAnonymousBaseline = Number.isFinite(Number(pending?.observedAnonymousToolResultCount));
    const matched = pendingId
      ? completed.some((result: any) => String(result?.toolCallId || "").trim() === pendingId)
      : (hasAnonymousBaseline
        ? anonymousCompletedCount > Number(pending.observedAnonymousToolResultCount)
        : completed.length > observedResultCount);
    if (matched) {
      if (pendingId) matchedIds.push(pendingId);
      return false;
    }
    const architectureCallRemovedFromActiveHistory = Boolean(
      pendingId
      && !activeCallIds.has(pendingId)
      && toolNamesMatch(ARCHITECTURE_TOOL_NAME, String(pending?.name || "")),
    );
    if (architectureCallRemovedFromActiveHistory) {
      // LM Studio can retain a durable pending checkpoint after the user stops
      // generation and deletes or replaces that assistant version. A read-only
      // architecture validator call that no longer exists in active history can
      // never receive a result, so retaining it deadlocks every later recovery
      // turn. Limit abandonment to this validator; unresolved mutation tools stay
      // fail-closed until an explicit result is recorded.
      abandonedIds.push(pendingId);
      return false;
    }
    return true;
  });
  return { remainingPending, matchedIds, abandonedIds };
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

  const salt = `${workingDirectory}\n${resolvedTargetModel}`;
  const lineage = core.messageLineageFingerprints(messages);
  const baseKey = core.baseSessionKey(messages, salt);
  const envSessionId = String(process.env.LMS_CONTEXT_COMPACTOR_SESSION_ID || "").trim();
  const marker = core.extractSessionMarker(messages) || envSessionId;
  const conversationSessionId = core.lmStudioConversationSessionFingerprint(
    workingDirectory,
    resolvedTargetModel,
  );
  let sessionResolution: any;
  if (conversationSessionId) {
    sessionResolution = {
      sessionId: conversationSessionId,
      reason: "lmstudio_conversation_directory",
      minted: false,
      baseKey,
    };
  } else if (marker) {
    sessionResolution = {
      sessionId: core.sessionFingerprint(messages, salt, { sessionMarker: marker }),
      reason: envSessionId ? "env" : "marker",
      minted: false,
      baseKey,
    };
  } else {
    sessionResolution = await (store as any).resolveSessionFork({
      baseKey,
      lineage,
      envSessionId,
    });
  }
  const sessionId = String(sessionResolution.sessionId);
  if (conversationSessionId) {
    tryInjectSessionMarker(history, conversationSessionId);
  } else if (!marker && sessionResolution.minted) {
    tryInjectSessionMarker(history, sessionId);
  } else if (marker) {
    tryInjectSessionMarker(history, marker);
  }
  await (store as any).touchSessionFork(baseKey, sessionId, lineage).catch(() => undefined);
  debugAgentLog("H-SESSION", "generator.ts:generate", "session resolved", {
    reason: sessionResolution.reason,
    minted: Boolean(sessionResolution.minted),
    baseKey: String(baseKey).slice(0, 12),
    sessionId: String(sessionId).slice(0, 12),
    lineageLen: lineage.length,
    hasMarker: Boolean(marker),
  });
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
    const { remainingPending, matchedIds, abandonedIds } = reconcilePendingToolCalls(
      unresolvedPendingCalls,
      currentSnapshots,
    );
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
      if (abandonedIds.length > 0) {
        await appendEventBestEffort(sessionId, {
          type: "pending_tool_calls_abandoned",
          at: new Date().toISOString(),
          reason: "read_only_architecture_call_absent_from_active_history",
          toolCallIds: abandonedIds,
        });
      }
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

  const contextLength = await model.getContextLength();
  const toolDefinitions = ctl.getToolDefinitions();
  const nextCheckpoint = core.buildCheckpoint(messages, checkpoint || {}, { maxCheckpointFacts: 32 });
  nextCheckpoint.compactionGeneration = Number(checkpoint?.compactionGeneration || 0);
  const trailingMetaUser = trailingMetaUserMessage(messages);
  const architectureGoal = latestUserGoalText(messages);
  const persistedArchitectureRecovery = Boolean(
    nextCheckpoint?.architectureProposal?.validationOk === false
    && architectureRecoveryContinuationRequested(architectureGoal),
  );
  // Once the MCP supplies an architecture control envelope, the server FSM is
  // authoritative. Legacy heuristic orchestration remains only for histories
  // that predate the envelope/checkpoint migration.
  const serverOwnedArchitectureControl = Boolean(nextCheckpoint?.architectureControl);
  const architectureValidationRequired = !serverOwnedArchitectureControl && !trailingMetaUser && (
    requiresArchitectureValidation(architectureGoal, toolDefinitions)
    || persistedArchitectureRecovery
  );
  if (architectureValidationRequired) {
    injectArchitectureValidationRule(history);
  }
  if (toolNamesMatch(
    FEATURE_INTENT_TOOL_NAME,
    String(nextCheckpoint?.requiredNextTool?.name || ""),
  )) {
    injectFeatureIntentAtomicRule(history);
  }
  const plannerAvailable = toolDefinitions.some((tool: any) => toolNamesMatch(
    TASK_PLANNER_TOOL_NAME,
    tool?.function?.name || tool?.name || "",
  ));
  const routeOwnershipAvailable = Boolean(nextCheckpoint?.taskRouteOwnership);
  const exactToolRouteAvailable = Boolean(
    nextCheckpoint?.toolRoute?.routeHash
    && Array.isArray(nextCheckpoint?.toolRoute?.activeTools)
    && nextCheckpoint.toolRoute.activeTools.length > 0
  );
  const projectAgentDiscoveryAvailable = [
    "get_workspace_info",
    "get_active_project",
    "list_active_tasks",
  ].some((expected) => toolDefinitions.some((tool: any) => toolNamesMatch(
    expected,
    tool?.function?.name || tool?.name || "",
  )));
  const unroutedMutationDefinitionsPresent = Boolean(
    !routeOwnershipAvailable
    && toolDefinitions.some((tool: any) => core.mutationToolName(
      tool?.function?.name || tool?.name || "",
    )),
  );
  if (!routeOwnershipAvailable && (projectAgentDiscoveryAvailable || unroutedMutationDefinitionsPresent)) {
    injectTaskRouteOwnershipRule(history, plannerAvailable);
  }
  const architectureEvidenceReadThreshold = Math.floor(finiteNumber(
    configValue(ctl, "architectureEvidenceReadThreshold", 4), 4, 1, 64,
  ));
  const architectureEvidenceHardLimit = Math.floor(finiteNumber(
    configValue(ctl, "architectureEvidenceHardLimit", 8), 8, architectureEvidenceReadThreshold, 128,
  ));
  const architectureReplanEvidenceReadBudget = Math.floor(finiteNumber(
    configValue(ctl, "architectureReplanEvidenceReadBudget", 4), 4, 0, 32,
  ));
  const architectureStatus = architectureGateStatus(messages, nextCheckpoint);
  const architectureContractGoal = architectureContractGoalText(
    messages,
    architectureGoal,
    architectureStatus.attempted && architectureStatus.lastValidationFailed,
  );
  const stagedContractRequired = Boolean(
    architectureStatus.stagedContractRequired
    || stagedArchitectureContractRequired(architectureContractGoal),
  );
  const networkedContractRequired = Boolean(
    architectureStatus.networkedContractRequired
    || networkedArchitectureContractRequired(architectureContractGoal),
  );
  if (nextCheckpoint?.architectureProposal) {
    nextCheckpoint.architectureProposal.stagedContractRequired = stagedContractRequired;
    nextCheckpoint.architectureProposal.networkedContractRequired = networkedContractRequired;
  }
  const architectureTool = toolDefinitions.find((tool: any) => {
    const name = tool?.function?.name || tool?.name || "";
    return toolNamesMatch(ARCHITECTURE_TOOL_NAME, name);
  });
  const requiredArchitectureTool = toolNamesMatch(
    ARCHITECTURE_TOOL_NAME,
    String(nextCheckpoint?.requiredNextTool?.name || ""),
  );
  const initialArchitectureEvidenceReady = Boolean(
    architectureStatus.directEvidenceCount >= architectureEvidenceReadThreshold
    && (
      architectureStatus.implementationEvidenceCount > 0
      || architectureStatus.directEvidenceCount >= architectureEvidenceHardLimit
    )
  );
  const architectureEvidenceRefillActive = Boolean(
    architectureValidationRequired
    && architectureTool
    && architectureStatus.attempted
    && architectureStatus.lastValidationFailed
    && architectureStatus.requiresFullProposal
    && architectureStatus.lastErrorCode !== "ARCHITECTURE_PROPOSAL_REPLAN_CORE_UNCHANGED"
    && architectureReplanEvidenceReadBudget > 0
    && architectureStatus.discoveryCallsSinceLastAttempt < architectureReplanEvidenceReadBudget
    && !requiredArchitectureTool
  );
  const architectureToolForced = Boolean(
    architectureValidationRequired
    && !architectureStatus.validated
    && architectureTool
    && (
      (!architectureStatus.attempted && initialArchitectureEvidenceReady)
      || (architectureStatus.attempted && !architectureEvidenceRefillActive)
      || requiredArchitectureTool
    )
  );
  if (architectureToolForced) {
    injectArchitectureSubmissionRule(history);
    if (architectureStatus.lastErrorCode === "ARCHITECTURE_PROPOSAL_REPLAN_CORE_UNCHANGED") {
      injectArchitectureCoreChangeRule(history, architectureStatus.unchangedCorePaths);
    }
  }
  const requireCompleteArchitectureProposal = Boolean(
    (architectureToolForced || architectureEvidenceRefillActive)
    && (!nextCheckpoint?.architectureProposal || architectureStatus.requiresFullProposal),
  );
  const architectureContractTool = architectureSubmissionTool(
    architectureTool,
    requireCompleteArchitectureProposal,
    {
      stagedContract: stagedContractRequired,
      networkedContract: networkedContractRequired,
    },
  );
  const semanticForbiddenTools = Array.isArray(nextCheckpoint?.semanticBlocker?.forbiddenTools)
    ? nextCheckpoint.semanticBlocker.forbiddenTools.map((name: any) => String(name || "").trim()).filter(Boolean)
    : [];
  const toolAllowedBySemanticBlocker = (tool: any): boolean => {
    const name = String(tool?.function?.name || tool?.name || "").trim();
    return !semanticForbiddenTools.some((forbidden: string) => toolNamesMatch(forbidden, name));
  };
  const phaseToolDefinitions = architectureToolForced
    ? [architectureContractTool].filter(toolAllowedBySemanticBlocker)
    : (architectureEvidenceRefillActive
      ? toolDefinitions
        .filter((tool: any) => architectureDiscoveryToolAllowed(
          tool?.function?.name || tool?.name || "",
        ))
        .map((tool: any) => (
          toolNamesMatch(ARCHITECTURE_TOOL_NAME, tool?.function?.name || tool?.name || "")
            ? architectureContractTool
            : tool
        ))
        .filter(toolAllowedBySemanticBlocker)
      : toolDefinitions.filter(toolAllowedBySemanticBlocker));
  // Exact catalog/state enforcement: a generated write is intent, not proof of
  // ownership. Remove mutation schemas until a server result has supplied the
  // compact taskSessionId + ownerCapability pair. This also protects older
  // agent MCP revisions that advertised write tools on a clean startup.
  const checkpointExplicitlyRequired = toolNamesMatch(
    TASK_CHECKPOINT_TOOL_NAME,
    String(nextCheckpoint?.requiredNextTool?.name || ""),
  );
  // Exact phase filtering also closes the tools/list refresh race between the
  // two Unreal MCP providers. Architecture recovery already constructs its
  // own narrower catalog, so it remains authoritative while active.
  const routedToolDefinitions = routeOwnershipAvailable
    && exactToolRouteAvailable
    && !architectureToolForced
    && !architectureEvidenceRefillActive
    ? phaseToolDefinitions.filter((tool: any) => routeAllowsTool(tool, nextCheckpoint))
    : (checkpointExplicitlyRequired
      ? phaseToolDefinitions
      : phaseToolDefinitions.filter((tool: any) => !toolNamesMatch(
        TASK_CHECKPOINT_TOOL_NAME,
        tool?.function?.name || tool?.name || "",
      )));
  const effectiveToolDefinitions = routeOwnershipAvailable
    ? routedToolDefinitions
    : routedToolDefinitions.filter((tool: any) => !core.mutationToolName(
      tool?.function?.name || tool?.name || "",
    ));
  const currentFormatted = await model.applyPromptTemplate(history);
  const inputTokens = await model.countTokens(currentFormatted);
  const toolSchemaTokens = await model.countTokens(JSON.stringify(effectiveToolDefinitions));
  const persistedNextToolName = nextCheckpoint?.requiredNextTool?.name || "";
  const nextToolName = core.isNonToolNextAction(persistedNextToolName) ? "" : persistedNextToolName;
  const hardRemainingTokens = finiteNumber(configValue(ctl, "hardRemainingTokens", 8000), 8000);
  const configuredOutputReserve = finiteNumber(
    configValue(ctl, "maxOutputReserve", 4096), 4096, 1,
  );
  const architectureOutputReserve = finiteNumber(
    configValue(ctl, "architectureMaxOutputReserve", 6144), 6144, configuredOutputReserve,
  );
  const config = {
    enabled,
    observeOnly,
    strictToolControlPlane: Boolean(configValue(ctl, "strictToolControlPlane", false)),
    bufferUntilPredictionComplete: Boolean(configValue(ctl, "bufferUntilPredictionComplete", true)),
    streamReasoningProgress: Boolean(configValue(ctl, "streamReasoningProgress", true)),
    rejectTruncatedPredictions: Boolean(configValue(ctl, "rejectTruncatedPredictions", true)),
    requireCheckpointPersistence,
    softRemainingTokens: finiteNumber(configValue(ctl, "softRemainingTokens", 14000), 14000, hardRemainingTokens),
    hardRemainingTokens,
    maxOutputReserve: architectureValidationRequired
      ? Math.max(configuredOutputReserve, architectureOutputReserve)
      : configuredOutputReserve,
    safetyMarginTokens: finiteNumber(configValue(ctl, "safetyMarginTokens", 1024), 1024),
    temperature: finiteNumber(configValue(ctl, "temperature", 0.1), 0.1, 0, 1),
    normalToolResultReserve: finiteNumber(configValue(ctl, "normalToolResultReserve", 3000), 3000),
    buildToolResultReserve: finiteNumber(configValue(ctl, "buildToolResultReserve", 8000), 8000),
    recentCompleteTurns: Math.floor(finiteNumber(configValue(ctl, "recentCompleteTurns", 1), 1, 0, 100)),
    minimumTurnsBetweenCompactions: Math.floor(finiteNumber(configValue(ctl, "minimumTurnsBetweenCompactions", 0), 0, 0, 100)),
    targetRemainingTokensAfterCompaction: finiteNumber(
      configValue(ctl, "targetRemainingTokensAfterCompaction", 24000), 24000, hardRemainingTokens,
    ),
    architectureEvidenceReadThreshold,
    architectureEvidenceHardLimit,
    architectureReplanEvidenceReadBudget,
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
    architectureValidationRequired,
    serverOwnedArchitectureControl,
    architectureToolForced,
    architectureEvidenceRefillActive,
    architectureAttempted: architectureStatus.attempted,
    architectureValidated: architectureStatus.validated,
    architectureDirectEvidenceCount: architectureStatus.directEvidenceCount,
    architectureDeclarationEvidenceCount: architectureStatus.declarationEvidenceCount,
    architectureImplementationEvidenceCount: architectureStatus.implementationEvidenceCount,
    architectureEvidenceCallsSinceLastAttempt: architectureStatus.evidenceCallsSinceLastAttempt,
    architectureDiscoveryCallsSinceLastAttempt: architectureStatus.discoveryCallsSinceLastAttempt,
    architectureUniqueEvidenceSinceLastAttempt: architectureStatus.uniqueEvidenceSinceLastAttempt,
    architectureLastRepairStrategy: architectureStatus.lastRepairStrategy,
    architectureLastRepairMode: architectureStatus.lastRepairMode,
    architectureLastErrorCode: architectureStatus.lastErrorCode,
    architectureUnchangedCorePaths: architectureStatus.unchangedCorePaths,
    architectureStagedContractRequired: stagedContractRequired,
    architectureNetworkedContractRequired: networkedContractRequired,
    requireCompleteArchitectureProposal,
    plannerAvailable,
    routeOwnershipAvailable,
    projectAgentDiscoveryAvailable,
    unroutedMutationDefinitionsPresent,
  });

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

  // Mid-chat major goal switches may compact when budget is already soft/hard.
  // Ordinary follow-ups must not wipe retained turns solely because the objective string changed.
  const priorObjective = String(checkpoint?.objective || "").trim();
  const latestObjective = String(nextCheckpoint.objective || "").trim();
  const goalChanged = core.isMajorGoalChange(priorObjective, latestObjective);
  const userGoalCount = messages.filter((message) => {
    const text = String(message.getText() || "").trim();
    return message.getRole() === "user" && text && !core.isMetaUserMessage(text);
  }).length;
  const hasPriorAssistant = messages.some((message) => message.getRole() === "assistant");
  const latestIsReadOnly = core.isReadOnlyUserGoal(latestObjective);
  const budgetPressed = decision.action === "soft_compact" || decision.action === "hard_compact";
  // Major mode flips may soft-compact even when the budget is healthy, but ordinary
  // objective-string churn must not. Retained turns are never zeroed by goal change alone.
  const goalChangeCompact = Boolean(
    enabled
    && !observeOnly
    && !trailingMetaUser
    && goalChanged,
  );
  const zeroRetainedTurns = false;
  if (goalChangeCompact && effectiveAction === "normal") {
    effectiveAction = "soft_compact";
  }

  debugAgentLog("H9", "generator.ts:generate", "goal-change and meta gate", {
    priorObjectiveLen: priorObjective.length,
    latestObjectiveLen: latestObjective.length,
    goalChanged,
    latestIsReadOnly,
    answeringMeta: Boolean(trailingMetaUser),
    userGoalCount,
    hasPriorAssistant,
    decisionAction: decision.action,
    effectiveAction,
    goalChangeCompact,
    zeroRetainedTurns,
    budgetPressed,
  });

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

  // Qwen multi_step_tool Jinja raises:
  //   raise_exception('No user query found in messages.')
  // which surfaces as applyPromptTemplate HTTP 400. Never send a user-less chat.
  const chatHasRealUser = (chat: Chat): boolean => chat.getMessagesArray().some((message) => {
    if (message.getRole() !== "user") return false;
    const text = String(message.getText() || "").trim();
    return Boolean(text) && !core.isMetaUserMessage(text);
  });
  if (!chatHasRealUser(modelChat)) {
    if (chatHasRealUser(history)) {
      console.warn(
        "[unreal-context-compactor] Compacted/model chat lost the user query; "
        + "falling back to inbound history to avoid Jinja 400.",
      );
      modelChat = history;
      debugAgentLog("H-userless", "generator.ts:before_respond", "fallback inbound history", {
        compactedLostUser: true,
      });
    } else {
      throw new Error(
        "Chat history has no user query. Qwen/Jinja templates require a user message "
        + "(applyPromptTemplate 400: No user query found in messages).",
      );
    }
  }

  const events: any[] = [];
  const requests: any[] = [];
  const strictToolControlPlane = Boolean(config.strictToolControlPlane);
  // A persisted requiredNextTool is a safety gate even when the optional
  // strict mode is disabled. Otherwise a model can emit an unrelated tool
  // call and the checkpoint would record progress that never happened.
  const requiredToolGateActive = Boolean(
    nextCheckpoint?.requiredNextTool?.name
    && !core.isNonToolNextAction(nextCheckpoint.requiredNextTool.name),
  );
  const toolControlPlaneEnforced = strictToolControlPlane
    || requiredToolGateActive
    || architectureToolForced
    || architectureEvidenceRefillActive
    || semanticForbiddenTools.length > 0;
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
  let streamedReasoningEventCount = 0;
  const recordEvent = (event: any) => {
    const outputBuffered = toolControlPlaneEnforced || bufferUntilPredictionComplete;
    const reasoningType = String(event?.opts?.reasoningType || "none");
    const streamAsProgress = Boolean(
      outputBuffered
      && config.streamReasoningProgress
      && event.kind === "fragment"
      && reasoningType !== "none"
    );
    if (streamAsProgress) {
      // Reasoning is transient progress, not a committed final answer or tool
      // request. Streaming it keeps the UI alive while atomic final/tool output
      // and truncation rejection remain intact.
      streamedReasoningEventCount += 1;
      emitEvent(event);
    } else if (outputBuffered) events.push(event);
    else emitEvent(event);
  };
  const runPrediction = async (predictionTools: any[], forceTool: boolean): Promise<string> => {
    const prediction = model.respond(modelChat, {
      maxTokens: Number(config.maxOutputReserve),
      temperature: Number(config.temperature),
      ...(predictionTools.length > 0 ? {
        rawTools: {
          type: "toolArray",
          tools: predictionTools,
          ...(forceTool ? { force: true } : {}),
        },
      } : {}),
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
        const rawRequest = info?.toolCallRequest || {};
        const request = enrichToolRequestSession(rawRequest, sessionId);
        if (request !== rawRequest && (toolControlPlaneEnforced || bufferUntilPredictionComplete)) {
          replaceBufferedArgumentFragments(events, callId, request.arguments);
        }
        requests.push({ callId, request });
        recordEvent({ kind: "end", callId, request });
      },
      onToolCallRequestFailure(callId: number, error: Error) {
        recordEvent({ kind: "failure", callId, error: String(error?.message || error) });
      },
    });
    const predictionResult: any = await prediction.result();
    return String(predictionResult?.stats?.stopReason || "");
  };
  const unsafeStopReasons = new Set(["contextLengthReached", "failed", "modelUnloaded"]);
  const predictionTruncated = (reason: string): boolean => (
    unsafeStopReasons.has(reason)
    || (Boolean(config.rejectTruncatedPredictions) && reason === "maxPredictedTokensReached")
  );
  const recordPredictionCompletion = async (
    reason: string,
    recoveryAttempt: boolean,
  ): Promise<void> => {
    const truncatedPrediction = predictionTruncated(reason);
    await appendEventBestEffort(sessionId, {
      type: "prediction_completion",
      at: new Date().toISOString(),
      stopReason: reason || "unspecified",
      bufferedEventCount: events.length,
      streamedReasoningEventCount,
      toolRequestCount: requests.length,
      outputCommitted: false,
      outputCommitPending: !truncatedPrediction,
      architectureFinalRecoveryAttempt: recoveryAttempt,
    });
  };

  let stopReason = await runPrediction(effectiveToolDefinitions, architectureToolForced);
  await recordPredictionCompletion(stopReason, false);
  if (predictionTruncated(stopReason)) {
    const safelyBuffered = toolControlPlaneEnforced || bufferUntilPredictionComplete;
    throw new Error(
      `Model prediction was discarded because it did not complete safely (stopReason=${stopReason}). `
      + (safelyBuffered
        ? "No buffered final text or tool call was committed; transient reasoning progress may already be visible. Compact the context or increase the model context/output limit."
        : "Atomic output was explicitly disabled, so already-streamed output may be partial. Enable atomic output before retrying."),
    );
  }
  if (requiredToolGateActive && !architectureValidationRequired && requests.length === 0) {
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      "server_required_tool_missing",
    );
    await appendEventBestEffort(sessionId, {
      type: "server_required_tool_missing",
      at: new Date().toISOString(),
      requiredTool: nextCheckpoint?.requiredNextTool?.name || "",
      protocolControl: nextCheckpoint?.protocolControl || null,
    });
    throw new Error(
      `Server control requires ${nextCheckpoint?.requiredNextTool?.name}; `
      + "the prose-only prediction was discarded without executing that tool.",
    );
  }
  const incompleteArchitecturePaths = requireCompleteArchitectureProposal
    ? [...new Set(requests.flatMap((entry) => (
      architecturePayloadViolationPaths(entry.request, architectureContractTool)
    )))]
    : [];
  if (incompleteArchitecturePaths.length > 0) {
    // Some smaller local models acknowledge the complete schema in reasoning but
    // omit required sections from the emitted tool arguments. Do not send that
    // malformed call to the MCP server and enter a result/retry loop. Give the
    // model one bounded serialization-only retry with missing JSON paths; values
    // still have to be derived by the model from retained source evidence.
    await appendEventBestEffort(sessionId, {
      type: "architecture_payload_repair_started",
      at: new Date().toISOString(),
      missingRequiredPaths: incompleteArchitecturePaths,
      priorToolRequestCount: requests.length,
    });
    events.length = 0;
    requests.length = 0;
    injectArchitecturePayloadRepairRule(modelChat, incompleteArchitecturePaths);
    stopReason = await runPrediction([architectureContractTool], true);
    await recordPredictionCompletion(stopReason, true);
    if (predictionTruncated(stopReason)) {
      throw new Error(
        `Forced architecture payload repair was discarded because it did not complete safely (stopReason=${stopReason}).`,
      );
    }
    const retryArchitectureRequests = requests.filter((entry) => (
      toolNamesMatch(ARCHITECTURE_TOOL_NAME, requestedToolName(entry.request))
    ));
    const remainingViolationPaths = [...new Set(retryArchitectureRequests.flatMap((entry) => (
      architecturePayloadViolationPaths(entry.request, architectureContractTool)
    )))];
    if (retryArchitectureRequests.length === 0 || remainingViolationPaths.length > 0) {
      nextCheckpoint.requiredNextTool = {
        name: ARCHITECTURE_TOOL_NAME,
        reference: "architecture_payload_repair_failed",
        args: {},
      };
      await persistCheckpoint(
        sessionId,
        nextCheckpoint,
        requireCheckpointPersistence,
        "architecture_payload_repair_failed",
      );
      await appendEventBestEffort(sessionId, {
        type: "architecture_payload_repair_failed",
        at: new Date().toISOString(),
        reason: retryArchitectureRequests.length === 0
          ? "validator_call_missing"
          : "required_schema_paths_still_missing",
        missingRequiredPaths: remainingViolationPaths,
      });
      throw new Error(
        "Architecture validator output was discarded after one bounded payload repair because required JSON-schema "
        + `paths are still missing: ${(remainingViolationPaths.length
          ? remainingViolationPaths
          : incompleteArchitecturePaths).join(", ")}.`,
      );
    }
    await appendEventBestEffort(sessionId, {
      type: "architecture_payload_repair_completed",
      at: new Date().toISOString(),
      repairedRequiredPaths: incompleteArchitecturePaths,
    });
  }
  let architectureRequestProduced = requests.some((entry) => (
    toolNamesMatch(ARCHITECTURE_TOOL_NAME, requestedToolName(entry.request))
  ));
  if (
    architectureValidationRequired
    && !architectureStatus.validated
    && requests.length === 0
    && !architectureRequestProduced
  ) {
    // A smaller local model can understand a rejected proposal in prose but
    // still try to end the turn instead of producing the mandatory replacement
    // payload. Discard that unvalidated text and make one bounded recovery
    // prediction in the same GUI turn with only the validator schema available.
    // The model still derives every proposal value; this supplies no design
    // answer and cannot execute project writes.
    await appendEventBestEffort(sessionId, {
      type: "architecture_final_recovery_started",
      at: new Date().toISOString(),
      reason: "proposal_validation_missing",
      directEvidenceCount: architectureStatus.directEvidenceCount,
      proposalAttempted: architectureStatus.attempted,
    });
    events.length = 0;
    requests.length = 0;
    injectArchitectureSubmissionRule(modelChat);
    stopReason = await runPrediction([architectureContractTool], true);
    await recordPredictionCompletion(stopReason, true);
    if (predictionTruncated(stopReason)) {
      throw new Error(
        `Forced architecture recovery was discarded because it did not complete safely (stopReason=${stopReason}).`
      );
    }
    architectureRequestProduced = requests.some((entry) => (
      toolNamesMatch(ARCHITECTURE_TOOL_NAME, requestedToolName(entry.request))
    ));
    await appendEventBestEffort(sessionId, {
      type: "architecture_final_recovery_completed",
      at: new Date().toISOString(),
      stopReason: stopReason || "unspecified",
      architectureRequestProduced,
    });
  }
  if (
    architectureValidationRequired
    && !architectureStatus.validated
    && requests.length === 0
    && !architectureRequestProduced
  ) {
    nextCheckpoint.requiredNextTool = {
      name: ARCHITECTURE_TOOL_NAME,
      reference: "architecture_final_blocked",
      args: {},
    };
    await persistCheckpoint(
      sessionId,
      nextCheckpoint,
      requireCheckpointPersistence,
      "architecture_final_blocked",
    );
    await appendEventBestEffort(sessionId, {
      type: "architecture_final_blocked",
      at: new Date().toISOString(),
      reason: "proposal_validation_missing",
      directEvidenceCount: architectureStatus.directEvidenceCount,
      proposalAttempted: architectureStatus.attempted,
    });
    throw new Error(
      "Architecture final output was discarded because unreal_architecture_reasoning has not returned "
      + "proposalValidation.ok=true. Retry this generation; the validator tool is now required.",
    );
  }

  const verdictByCallId = new Map<number, { ok: boolean; reason?: string }>();
  for (const entry of requests) {
    const requestedName = requestedToolName(entry.request);
    const requestArguments = entry.request?.arguments && typeof entry.request.arguments === "object"
      ? entry.request.arguments
      : {};
    const missingRequiredProposal = Boolean(
      requireCompleteArchitectureProposal
      && toolNamesMatch(ARCHITECTURE_TOOL_NAME, requestedName)
      && (
        !requestArguments.proposal
        || typeof requestArguments.proposal !== "object"
        || Array.isArray(requestArguments.proposal)
      )
    );
    const architectureToolRejected = architectureToolForced
      && !toolNamesMatch(ARCHITECTURE_TOOL_NAME, requestedName);
    const discoveryToolRejected = architectureEvidenceRefillActive
      && !architectureDiscoveryToolAllowed(requestedName);
    const semanticToolRejected = semanticForbiddenTools.some(
      (forbidden: string) => toolNamesMatch(forbidden, requestedName),
    );
    const verdict = semanticToolRejected
      ? {
        ok: false,
        reason: `semantic blocker forbids ${requestedName || "<unnamed>"}; errorCode=${nextCheckpoint?.semanticBlocker?.errorCode || "BLOCKED"}`,
      }
      : (architectureToolRejected
      ? {
        ok: false,
        reason: `Architecture submission is required; expected ${ARCHITECTURE_TOOL_NAME}, got ${requestedName || "<unnamed>"}.`,
      }
      : (discoveryToolRejected
        ? {
          ok: false,
          reason: `Architecture evidence refill only allows direct-source discovery or ${ARCHITECTURE_TOOL_NAME}; received ${requestedName || "<unnamed>"}.`,
        }
      : (missingRequiredProposal
        ? {
          ok: false,
          reason: "Architecture submission is required; the validator call must include one complete proposal object.",
        }
      : (toolControlPlaneEnforced
        ? validateToolRequest(entry.request, nextCheckpoint)
        : { ok: true }))));
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

  if (toolControlPlaneEnforced || bufferUntilPredictionComplete) {
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
    streamedReasoningEventCount,
    toolRequestCount: requests.length,
    outputCommitted: true,
  });
}

export {
  architectureGateStatus,
  generate,
  injectFeatureIntentAtomicRule,
  injectTaskRouteOwnershipRule,
  networkedArchitectureContractRequired,
  reconcilePendingToolCalls,
};
// End of module.
