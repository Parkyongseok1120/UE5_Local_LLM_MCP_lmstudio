import {
  Chat,
  ChatMessage,
  type LLMTool,
  type PredictionLoopHandler,
  type PredictionLoopHandlerController,
  type ToolCallRequest,
} from "@lmstudio/sdk";
import { directConfigSchematics } from "./direct-config";
import { runOneToolRound } from "./round-loop";

// The deterministic core is CommonJS so it can also be exercised directly by
// Node's test runner without booting an LM Studio plugin host.
const core = require("./direct-compaction-core.js") as {
  buildCheckpoint(messages: Array<NormalizedMessage>, options?: Record<string, unknown>): CheckpointResult;
  shouldCompact(measurement: ContextMeasurement, options?: Record<string, unknown>): boolean;
};
const modelNotes = require("./continuity-model-notes.js") as {
  NOTE_INSTRUCTION: string;
  ContinuityNoteStore: new () => {
    read(key: string): ContinuityNote | null;
    write(key: string, note: ContinuityNote): boolean;
  };
  attachScope(draft: unknown, fingerprint: string, messages: Array<ChatMessage>): ContinuityNote | null;
  historyKey(messages: Array<ChatMessage>, workingDirectory: string): string;
  objectiveFingerprint(messages: Array<ChatMessage>): string;
  reconcileStoredNote(note: ContinuityNote, messages: Array<ChatMessage>): ContinuityNote | null;
  renderAssistantNote(note: ContinuityNote): string;
  splitVisibleAnswer(text: string): { visibleText: string; hasFooter: boolean; note: unknown };
};

type ContinuityNote = {
  scope: { objectiveFingerprint: string; projectIdentity?: string };
  decisions: Array<unknown>;
  rejectedHypotheses: Array<unknown>;
  openQuestions: Array<unknown>;
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
  assistantCheckpoint: string;
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
  pluginIdentifier?: string;
};

function numeric(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(parsed)));
}

function readConfig(ctl: PredictionLoopHandlerController): DirectConfig {
  const config = ctl.getPluginConfig(directConfigSchematics);
  return {
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
  tools: Array<RemoteToolLike & { description?: string; parametersJsonSchema?: unknown }> = [],
): Promise<ContextMeasurement> {
  const source = tokenSource as {
    getContextLength?: () => Promise<number>;
    applyPromptTemplate?: (chat: Chat, opts?: { toolDefinitions?: Array<LLMTool> }) => Promise<string>;
    countTokens?: (text: string) => Promise<number>;
  };
  const toolDefinitions: Array<LLMTool> = tools.map((tool) => ({
    type: "function",
    function: {
      name: tool.name,
      ...(tool.description ? { description: tool.description } : {}),
      ...(tool.parametersJsonSchema
        ? { parameters: tool.parametersJsonSchema as LLMTool["function"]["parameters"] }
        : {}),
    },
  }));
  let contextLength = config.assumedContextLength;
  let inputTokens = Math.ceil(
    (history.toString().length + JSON.stringify(toolDefinitions).length) / 4,
  );
  let exact = false;
  try {
    if (typeof source.getContextLength === "function") {
      contextLength = numeric(await source.getContextLength(), config.assumedContextLength, 2048, 4_000_000);
    }
    if (typeof source.applyPromptTemplate === "function" && typeof source.countTokens === "function") {
      const prompt = await source.applyPromptTemplate(history, { toolDefinitions });
      inputTokens = numeric(await source.countTokens(prompt), inputTokens, 0, 4_000_000);
      exact = true;
    }
  } catch {
    // A selected generator or experimental model handle may not expose token
    // measurement. Character estimation remains conservative, and the
    // configured message-count fallback independently protects this path.
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
  checkpointOptions: Record<string, unknown> = {},
): { history: Chat; checkpoint: CheckpointResult } {
  const messages = history.getMessagesArray();
  const normalized = normalizeHistory(history);
  const checkpoint = core.buildCheckpoint(normalized, {
    recentCompleteTurns,
    maxCheckpointChars: config.maxCheckpointChars,
    maxToolResultChars: config.maxToolResultChars,
    ...checkpointOptions,
  });
  if (checkpoint.omittedMessageCount <= 0) return { history, checkpoint };
  const retained = new Set(checkpoint.retainedIndexes);
  const compacted = Chat.empty();
  for (let index = 0; index < messages.length; index += 1) {
    if (messages[index].isSystemPrompt() && retained.has(index)) compacted.append(messages[index]);
  }
  compacted.append("system", checkpoint.checkpoint);
  if (checkpoint.assistantCheckpoint) compacted.append("assistant", checkpoint.assistantCheckpoint);
  for (let index = 0; index < messages.length; index += 1) {
    if (!messages[index].isSystemPrompt() && retained.has(index)) compacted.append(messages[index]);
  }
  return { history: compacted, checkpoint };
}

function composeModelHistory(history: Chat, note: ContinuityNote | null, config: DirectConfig) {
  const noteText = note ? modelNotes.renderAssistantNote(note) : "";
  const instruction = modelNotes.NOTE_INSTRUCTION;
  const canIncludeInstruction = config.maxCheckpointChars >= 2000 + instruction.length;
  const canIncludeNote = canIncludeInstruction
    && config.maxCheckpointChars >= 2000 + instruction.length + noteText.length;
  const overhead = canIncludeInstruction ? instruction.length + (canIncludeNote ? noteText.length : 0) : 0;
  const composed = Chat.empty();
  const messages = history.getMessagesArray();
  let index = 0;
  while (index < messages.length && messages[index].isSystemPrompt()) {
    composed.append(messages[index]);
    index += 1;
  }
  if (canIncludeInstruction) composed.append("system", instruction);
  if (canIncludeNote && noteText) composed.append("assistant", noteText);
  for (; index < messages.length; index += 1) composed.append(messages[index]);
  return { history: composed, overhead };
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
  const registeredCallIds = new Set<number>();
  const unidentifiedRequestCallIds: Array<number> = [];
  const unidentifiedResultCallIds: Array<number> = [];
  let fallbackCallId = 1_000_000;

  const beginRound = () => {
    callIdsByToolRequestId.clear();
    registeredCallIds.clear();
    unidentifiedRequestCallIds.length = 0;
    unidentifiedResultCallIds.length = 0;
  };

  const registerRequest = (callId: number, request: ToolCallRequest) => {
    if (registeredCallIds.has(callId)) return;
    registeredCallIds.add(callId);
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

  return { beginRound, emit, registerRequest };
}

export function createPredictionLoopHandler(
  noteStore: InstanceType<typeof modelNotes.ContinuityNoteStore> = new modelNotes.ContinuityNoteStore(),
): PredictionLoopHandler {
  return async (ctl) => {
  ctl.guardAbort();
  const config = readConfig(ctl);
  const originalHistory = await ctl.pullHistory();
  const tokenSource = await ctl.tokenSource();
  if (selectedSourceIsThisPlugin(tokenSource)) {
    throw new Error("Select the actual Qwen/LLM in LM Studio. The context compactor is middleware, not a chat model.");
  }

  const toolSession = await ctl.startToolUseSession();
  const emitter = createMessageEmitter(ctl, toolSession.tools);
  const visibleHistory = Chat.from(originalHistory);
  const objectiveFingerprint = modelNotes.objectiveFingerprint(originalHistory.getMessagesArray());
  let workingDirectory = "";
  if (!config.observeOnly) {
    try { workingDirectory = ctl.getWorkingDirectory(); } catch { /* No stable chat workspace is available. */ }
  }
  const originalMessages = originalHistory.getMessagesArray();
  const priorHistoryKey = originalMessages.at(-1)?.isUserMessage()
    ? modelNotes.historyKey(originalMessages.slice(0, -1), workingDirectory) : "";
  const priorNote = priorHistoryKey ? noteStore.read(priorHistoryKey) : null;
  let activeNote = priorNote?.scope.objectiveFingerprint === objectiveFingerprint
    ? modelNotes.reconcileStoredNote(priorNote, originalMessages) : null;
  let workingHistory = originalHistory;
  let roundIndex = 0;
  try {
    while (true) {
      ctl.guardAbort();
      const noteEnabled = Boolean(workingDirectory && objectiveFingerprint && !config.observeOnly);
      if (noteEnabled && activeNote) {
        activeNote = modelNotes.reconcileStoredNote(activeNote, visibleHistory.getMessagesArray());
      }
      const beforeInput = noteEnabled ? composeModelHistory(workingHistory, activeNote, config)
        : { history: workingHistory, overhead: 0 };
      const before = await measureContext(tokenSource, beforeInput.history, config, toolSession.tools);
      let modelHistory = workingHistory;
      let compacted = false;
      if (core.shouldCompact(before, config)) {
        const hard = before.remainingTokens <= config.hardRemainingTokens;
        let candidate = buildCompactedHistory(
          workingHistory,
          hard ? 0 : config.recentCompleteTurns,
          config,
          { ...(hard ? { maxCurrentTurnMessages: 2 } : {}),
            maxCheckpointChars: config.maxCheckpointChars - beforeInput.overhead },
        );
        if (!hard) {
          let needsBoundedCurrentTurn = candidate.history === workingHistory;
          if (!needsBoundedCurrentTurn) {
            const measuredCandidate = noteEnabled ? composeModelHistory(candidate.history, activeNote, config).history
              : candidate.history;
            const afterFirst = await measureContext(tokenSource, measuredCandidate, config, toolSession.tools);
            needsBoundedCurrentTurn = core.shouldCompact(afterFirst, config);
          }
          if (needsBoundedCurrentTurn) {
            candidate = buildCompactedHistory(
              workingHistory,
              0,
              config,
              { maxCurrentTurnMessages: 2,
                maxCheckpointChars: config.maxCheckpointChars - beforeInput.overhead },
            );
          }
        }
        if (candidate.history !== workingHistory) {
          modelHistory = candidate.history;
          compacted = modelHistory !== workingHistory;
        }
      }
      workingHistory = modelHistory;
      const modelInput = noteEnabled ? composeModelHistory(workingHistory, activeNote, config).history
        : workingHistory;

      ctl.debug({
        event: "direct_context_measurement",
        roundIndex,
        compacted,
        observeOnly: config.observeOnly,
        exactMeasurement: before.exact,
        messageCount: before.messageCount,
        inputTokens: before.inputTokens,
        remainingTokens: before.remainingTokens,
      });
      ctl.guardAbort();

      // SDK call IDs are guaranteed unique only within one .act invocation.
      // Clear correlation state after the prior round's captured messages have
      // been emitted, while keeping guard/finalized registration idempotent.
      emitter.beginRound();
      const captured = await runOneToolRound(
        tokenSource,
        modelInput,
        toolSession.tools,
        ctl.abortSignal,
        {
          onToolCallRequestFinalized: (_roundIndex, callId, info) => {
            emitter.registerRequest(callId, info.toolCallRequest);
          },
          guardToolCall: async (_roundIndex, callId, controller) => {
            const request = controller.toolCallRequest;
            // The SDK does not invoke onToolCallRequestFinalized for denied
            // calls, so register the stable call ID at the confirmation edge.
            emitter.registerRequest(callId, request);
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
        },
      );
      for (const message of captured.messages) {
        let visibleMessage = message;
        if (message.isAssistantMessage() && message.getText()) {
          const extracted = modelNotes.splitVisibleAnswer(message.getText());
          if (extracted.hasFooter) {
            visibleMessage = ChatMessage.from(message);
            visibleMessage.replaceText(extracted.visibleText);
            if (noteEnabled && extracted.note) {
              activeNote = modelNotes.attachScope(
                extracted.note, objectiveFingerprint, visibleHistory.getMessagesArray(),
              );
              if (activeNote && activeNote.decisions.length + activeNote.rejectedHypotheses.length
                + activeNote.openQuestions.length === 0) activeNote = null;
            }
          }
        }
        workingHistory.append(visibleMessage);
        visibleHistory.append(visibleMessage);
        emitter.emit(visibleMessage);
      }
      if (captured.failure !== undefined) throw captured.failure;
      if (!captured.continueAfterTools) break;
      roundIndex += 1;
    }
    if (activeNote && workingDirectory) {
      const nextHistoryKey = modelNotes.historyKey(visibleHistory.getMessagesArray(), workingDirectory);
      const verifiedNote = modelNotes.reconcileStoredNote(activeNote, visibleHistory.getMessagesArray());
      if (nextHistoryKey && verifiedNote) noteStore.write(nextHistoryKey, verifiedNote);
    }
  } finally {
    toolSession[Symbol.dispose]();
  }
  };
}

export const handlePredictionLoop = createPredictionLoopHandler();

export const __test = {
  buildCompactedHistory,
  measureContext,
  normalizeHistory,
  readConfig,
  selectedSourceIsThisPlugin,
};
