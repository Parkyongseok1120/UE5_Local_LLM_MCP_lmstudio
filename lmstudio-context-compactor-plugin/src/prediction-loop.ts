import {
  Chat,
  type ChatMessage,
  type PredictionLoopHandler,
  type PredictionLoopHandlerController,
  type ToolCallRequest,
} from "@lmstudio/sdk";
import { directConfigSchematics } from "./direct-config";

// The deterministic core is CommonJS so it can also be exercised directly by
// Node's test runner without booting an LM Studio plugin host.
const core = require("./direct-compaction-core.js") as {
  buildCheckpoint(messages: Array<NormalizedMessage>, options?: Record<string, unknown>): CheckpointResult;
  shouldCompact(measurement: ContextMeasurement, options?: Record<string, unknown>): boolean;
};

type NormalizedMessage = {
  role: string;
  text: string;
  hasFiles: boolean;
  toolRequests: Array<ToolCallRequest>;
  toolResults: Array<{ content: string; toolCallId?: string }>;
};

type CheckpointResult = {
  checkpoint: string;
  retainedIndexes: Array<number>;
  omittedMessageCount: number;
  latestUserVerbatim: string;
};

export type ContextMeasurement = {
  contextLength: number;
  inputTokens: number;
  remainingTokens: number;
  exact: boolean;
  messageCount: number;
};

type DirectConfig = {
  enabled: boolean;
  observeOnly: boolean;
  softRemainingTokens: number;
  hardRemainingTokens: number;
  maxOutputReserve: number;
  safetyMarginTokens: number;
  assumedContextLength: number;
  recentCompleteTurns: number;
  compactAboveMessageCount: number;
  maxCheckpointChars: number;
  maxToolResultChars: number;
};

type RemoteToolLike = {
  name: string;
  pluginIdentifier: string;
};

function numeric(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(parsed)));
}

function readConfig(ctl: PredictionLoopHandlerController): DirectConfig {
  const config = ctl.getPluginConfig(directConfigSchematics);
  return {
    enabled: config.get("enabled") === true,
    observeOnly: config.get("observeOnly") === true,
    softRemainingTokens: numeric(config.get("softRemainingTokens"), 14000, 0, 1_000_000),
    hardRemainingTokens: numeric(config.get("hardRemainingTokens"), 8000, 0, 1_000_000),
    maxOutputReserve: numeric(config.get("maxOutputReserve"), 4096, 256, 131072),
    safetyMarginTokens: numeric(config.get("safetyMarginTokens"), 1024, 0, 131072),
    assumedContextLength: numeric(config.get("assumedContextLength"), 32768, 2048, 4_000_000),
    recentCompleteTurns: numeric(config.get("recentCompleteTurns"), 2, 0, 20),
    compactAboveMessageCount: numeric(config.get("compactAboveMessageCount"), 24, 4, 10000),
    maxCheckpointChars: numeric(config.get("maxCheckpointChars"), 12000, 2000, 100000),
    maxToolResultChars: numeric(config.get("maxToolResultChars"), 1200, 200, 10000),
  };
}

function normalizeHistory(history: Chat): Array<NormalizedMessage> {
  return history.getMessagesArray().map((message) => ({
    role: message.getRole(),
    text: message.getText(),
    hasFiles: message.hasFiles(),
    toolRequests: message.getToolCallRequests(),
    toolResults: message.getToolCallResults(),
  }));
}

async function measureContext(
  tokenSource: unknown,
  history: Chat,
  config: DirectConfig,
): Promise<ContextMeasurement> {
  const source = tokenSource as {
    getContextLength?: () => Promise<number>;
    applyPromptTemplate?: (chat: Chat) => Promise<string>;
    countTokens?: (text: string) => Promise<number>;
  };
  let contextLength = config.assumedContextLength;
  let inputTokens = Math.ceil(history.toString().length / 4);
  let exact = false;
  try {
    if (typeof source.getContextLength === "function") {
      contextLength = numeric(await source.getContextLength(), config.assumedContextLength, 2048, 4_000_000);
    }
    if (typeof source.applyPromptTemplate === "function" && typeof source.countTokens === "function") {
      const prompt = await source.applyPromptTemplate(history);
      inputTokens = numeric(await source.countTokens(prompt), inputTokens, 0, 4_000_000);
      exact = true;
    }
  } catch {
    // A selected generator or experimental model handle may not expose token
    // measurement. Character estimation remains advisory and never routes.
  }
  return {
    contextLength,
    inputTokens,
    remainingTokens: contextLength - inputTokens - config.maxOutputReserve - config.safetyMarginTokens,
    exact,
    messageCount: history.length,
  };
}

function buildCompactedHistory(
  history: Chat,
  recentCompleteTurns: number,
  config: DirectConfig,
): { history: Chat; checkpoint: CheckpointResult } {
  const messages = history.getMessagesArray();
  const normalized = normalizeHistory(history);
  const checkpoint = core.buildCheckpoint(normalized, {
    recentCompleteTurns,
    maxCheckpointChars: config.maxCheckpointChars,
    maxToolResultChars: config.maxToolResultChars,
  });
  if (checkpoint.omittedMessageCount <= 0) return { history, checkpoint };
  const retained = new Set(checkpoint.retainedIndexes);
  const compacted = Chat.empty();
  for (let index = 0; index < messages.length; index += 1) {
    if (messages[index].isSystemPrompt() && retained.has(index)) compacted.append(messages[index]);
  }
  compacted.append("system", checkpoint.checkpoint);
  for (let index = 0; index < messages.length; index += 1) {
    if (!messages[index].isSystemPrompt() && retained.has(index)) compacted.append(messages[index]);
  }
  return { history: compacted, checkpoint };
}

function selectedSourceIsThisPlugin(source: unknown): boolean {
  const identifier = String((source as { identifier?: string })?.identifier || "").toLowerCase();
  return identifier.includes("unreal-context-compactor") || identifier.includes("codex/unreal-context-compactor");
}

function toolPluginIdentifier(tools: Array<RemoteToolLike>, name: string): string | undefined {
  return tools.find((tool) => tool.name === name)?.pluginIdentifier;
}

function createMessageEmitter(
  ctl: PredictionLoopHandlerController,
  tools: Array<RemoteToolLike>,
) {
  const callIdsByToolRequestId = new Map<string, number>();
  const unidentifiedRequestCallIds: Array<number> = [];
  const unidentifiedResultCallIds: Array<number> = [];
  let fallbackCallId = 1_000_000;

  const registerRequest = (callId: number, request: ToolCallRequest) => {
    if (request.id) callIdsByToolRequestId.set(request.id, callId);
    else unidentifiedRequestCallIds.push(callId);
  };

  const resolveRequestCallId = (toolCallId?: string): number => {
    if (toolCallId && callIdsByToolRequestId.has(toolCallId)) return callIdsByToolRequestId.get(toolCallId)!;
    const callId = unidentifiedRequestCallIds.shift() ?? fallbackCallId++;
    unidentifiedResultCallIds.push(callId);
    return callId;
  };

  const resolveResultCallId = (toolCallId?: string): number => {
    if (toolCallId && callIdsByToolRequestId.has(toolCallId)) return callIdsByToolRequestId.get(toolCallId)!;
    return unidentifiedResultCallIds.shift() ?? fallbackCallId++;
  };

  const emit = (message: ChatMessage) => {
    const block = ctl.createContentBlock({ roleOverride: message.getRole() });
    const text = message.getText();
    if (text) block.appendText(text);
    for (const request of message.getToolCallRequests()) {
      const callId = resolveRequestCallId(request.id);
      block.appendToolRequest({
        callId,
        toolCallRequestId: request.id,
        name: request.name,
        parameters: request.arguments || {},
        pluginIdentifier: toolPluginIdentifier(tools, request.name),
      });
    }
    for (const result of message.getToolCallResults()) {
      block.appendToolResult({
        callId: resolveResultCallId(result.toolCallId),
        toolCallRequestId: result.toolCallId,
        content: result.content,
      });
    }
  };

  return { emit, registerRequest };
}

export const handlePredictionLoop: PredictionLoopHandler = async (ctl) => {
  ctl.guardAbort();
  const config = readConfig(ctl);
  const originalHistory = await ctl.pullHistory();
  const tokenSource = await ctl.tokenSource();
  if (selectedSourceIsThisPlugin(tokenSource)) {
    throw new Error("Select the actual Qwen/LLM in LM Studio. The context compactor is middleware, not a chat model.");
  }

  const before = await measureContext(tokenSource, originalHistory, config);
  let modelHistory = originalHistory;
  let compacted = false;
  if (core.shouldCompact(before, config)) {
    const hard = before.remainingTokens <= config.hardRemainingTokens;
    let candidate = buildCompactedHistory(originalHistory, hard ? 0 : config.recentCompleteTurns, config);
    if (candidate.history !== originalHistory) {
      const afterFirst = await measureContext(tokenSource, candidate.history, config);
      if (!hard && afterFirst.remainingTokens <= config.hardRemainingTokens) {
        candidate = buildCompactedHistory(originalHistory, 0, config);
      }
      modelHistory = candidate.history;
      compacted = modelHistory !== originalHistory;
    }
  }
  if (config.observeOnly || !config.enabled) modelHistory = originalHistory;

  ctl.debug({
    event: "direct_context_measurement",
    compacted,
    observeOnly: config.observeOnly,
    exactMeasurement: before.exact,
    messageCount: before.messageCount,
    inputTokens: before.inputTokens,
    remainingTokens: before.remainingTokens,
  });
  ctl.guardAbort();

  const toolSession = await ctl.startToolUseSession();
  const emitter = createMessageEmitter(ctl, toolSession.tools);
  try {
    await tokenSource.act(modelHistory, toolSession.tools, {
      signal: ctl.abortSignal,
      onToolCallRequestFinalized: (_roundIndex, callId, info) => {
        emitter.registerRequest(callId, info.toolCallRequest);
      },
      guardToolCall: async (_roundIndex, callId, controller) => {
        const request = controller.toolCallRequest;
        const decision = await ctl.requestConfirmToolCall({
          callId,
          pluginIdentifier: toolPluginIdentifier(toolSession.tools, request.name),
          name: request.name,
          parameters: request.arguments || {},
        });
        if (decision.type === "deny") controller.deny(decision.denyReason);
        else if (decision.toolArgsOverride) controller.allowAndOverrideParameters(decision.toolArgsOverride);
        else controller.allow();
      },
      onMessage: emitter.emit,
    });
  } finally {
    toolSession[Symbol.dispose]();
  }
};

export const __test = {
  buildCompactedHistory,
  measureContext,
  normalizeHistory,
  readConfig,
  selectedSourceIsThisPlugin,
};
