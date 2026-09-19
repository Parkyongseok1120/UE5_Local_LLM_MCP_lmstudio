import {
  Chat,
  ChatMessage,
  type LMStudioClient,
  type LLMTool,
  type PredictionLoopHandler,
  type PredictionLoopHandlerController,
  type PredictionProcessStatusController,
  type Tool,
  type ToolCallRequest,
} from "@lmstudio/sdk";
import { createAttachmentContext } from "./attachment-tools";
import { directConfigSchematics } from "./direct-config";
import { runOneToolRound } from "./round-loop";
import { PredictionStreamRenderer } from "./prediction-stream";
import {
  bindProjectArguments,
  detectMentionedProject,
  filterToolsForScope,
  renderToolScopeInstruction,
  resolveToolScope,
  type ProjectEngineSetting,
  type ScopedTool,
} from "./tool-scope";

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
  projectEngine: ProjectEngineSetting;
  projectIdentity: string;
  observeOnly: boolean;
  showDebugInfo: boolean;
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

type RemoteToolLike = Tool & {
  name: string;
  pluginIdentifier?: string;
  description: string;
  parametersJsonSchema?: unknown;
};

const UNITY_OBSERVATION_TOOLS = new Set([
  "unity_references",
  "unity_snapshot",
  "unity_debug_query",
  "unity_symbols",
  "unity_status",
  "list_directory",
  "search_files",
  "read_file",
  "structured_data_read",
  "unity_find",
  "unity_object_read",
  "unity_logs",
]);

const UNREAL_OBSERVATION_TOOLS = new Set([
  "get_workspace_info",
  "list_unreal_projects",
  "get_active_project",
  "detect_unreal_project",
  "list_directory",
  "search_files",
  "read_file",
  "read_file_range",
  "read_symbol",
  "read_unreal_logs",
  "propose_file_deletions",
]);

/**
 * LM Studio 0.4.24 can leave requestConfirmToolCall pending without rendering
 * its approval controls. Observation-only calls must not block the prediction
 * loop on that host UI. Mutations and long-running work still use the host's
 * confirmation flow.
 */
function isObservationOnlyToolCall(
  tool: RemoteToolLike,
  request: Pick<ToolCallRequest, "name" | "arguments">,
): boolean {
  const plugin = String(tool.pluginIdentifier || "").trim().toLowerCase();
  const name = String(request.name || tool.name || "").trim();
  const args = request.arguments && typeof request.arguments === "object"
    ? request.arguments as Record<string, unknown> : {};

  if (name === "read_attached_document") return true;
  if (name.startsWith("evidence_first_")) return true;
  if (plugin === "mcp/unity-tools" || plugin.endsWith("/unity-tools")) {
    if (UNITY_OBSERVATION_TOOLS.has(name)) return true;
    if (name === "unity_scene") return args.action === "list";
    if (name === "unity_prefab") return ["read", "contents", "overrides"].includes(String(args.action || ""));
    if (name === "unity_approval") return args.action === "status";
    if (name === "unity_operation") return args.action === "get";
    if (name === "unity_tests") return ["status", "results", "release"].includes(String(args.action || ""));
    return false;
  }
  if (plugin === "mcp/unreal-agent" || plugin.endsWith("/unreal-agent")
    || plugin === "mcp/unreal-rag" || plugin.endsWith("/unreal-rag")) {
    return UNREAL_OBSERVATION_TOOLS.has(name);
  }
  return false;
}

function numeric(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(parsed)));
}

function readConfig(ctl: PredictionLoopHandlerController): DirectConfig {
  const config = ctl.getPluginConfig(directConfigSchematics);
  const configuredEngine = String(config.get("projectEngine") || "auto");
  const projectEngine: ProjectEngineSetting = ["auto", "unity", "unreal", "mixed"].includes(configuredEngine)
    ? configuredEngine as ProjectEngineSetting : "auto";
  return {
    projectEngine,
    projectIdentity: String(config.get("projectIdentity") || "").trim().slice(0, 4096),
    observeOnly: config.get("observeOnly") === true,
    showDebugInfo: config.get("showDebugInfo") === true,
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
  const systemText = [
    ...messages.filter((message, index) => message.isSystemPrompt() && retained.has(index))
      .map((message) => message.getText().trim()).filter(Boolean),
    checkpoint.checkpoint,
  ].filter(Boolean).join("\n\n");
  if (systemText) compacted.append("system", systemText);
  if (checkpoint.assistantCheckpoint) compacted.append("assistant", checkpoint.assistantCheckpoint);
  for (let index = 0; index < messages.length; index += 1) {
    if (!messages[index].isSystemPrompt() && retained.has(index)) compacted.append(messages[index]);
  }
  return { history: compacted, checkpoint };
}

function composeModelHistory(
  history: Chat,
  note: ContinuityNote | null,
  config: DirectConfig,
  systemInstructions: Array<string> = [],
  enableNoteProtocol = Boolean(note),
) {
  const instructions = systemInstructions.map((value) => String(value || "").trim()).filter(Boolean);
  const messages = history.getMessagesArray();
  const systemIndexes = messages.map((message, index) => message.isSystemPrompt() ? index : -1)
    .filter((index) => index >= 0);
  const systemLayoutIsCompatible = systemIndexes.length === 0
    || (systemIndexes.length === 1 && systemIndexes[0] === 0);
  if (!enableNoteProtocol && instructions.length === 0 && systemLayoutIsCompatible) {
    return { history, overhead: 0 };
  }
  const noteText = note ? modelNotes.renderAssistantNote(note) : "";
  const instruction = modelNotes.NOTE_INSTRUCTION;
  const canIncludeInstruction = enableNoteProtocol
    && config.maxCheckpointChars >= 2000 + instruction.length;
  const canIncludeNote = canIncludeInstruction
    && config.maxCheckpointChars >= 2000 + instruction.length + noteText.length;
  const overhead = canIncludeInstruction ? instruction.length + (canIncludeNote ? noteText.length : 0) : 0;
  const composed = Chat.empty();
  const combinedSystemText = [
    ...messages.filter((message) => message.isSystemPrompt())
      .map((message) => message.getText().trim()).filter(Boolean),
    ...instructions,
    ...(canIncludeInstruction ? [instruction] : []),
  ].join("\n\n");
  if (combinedSystemText) composed.append("system", combinedSystemText);
  if (canIncludeNote && noteText) composed.append("assistant", noteText);
  for (const message of messages) {
    if (!message.isSystemPrompt()) composed.append(message);
  }
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
  const emittedCallIds = new Set<number>();
  let fallbackCallId = 1_000_000;

  const beginRound = () => {
    callIdsByToolRequestId.clear();
    registeredCallIds.clear();
    unidentifiedRequestCallIds.length = 0;
    unidentifiedResultCallIds.length = 0;
    emittedCallIds.clear();
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

  const emit = (message: ChatMessage, skipText = false) => {
    const requests = message.getToolCallRequests().map((request) => ({
      request,
      callId: resolveRequestCallId(request.id),
    }));
    const pendingRequests = requests.filter(({ callId }) => !emittedCallIds.has(callId));
    const results = message.getToolCallResults();
    const text = message.getText();
    if ((!text || skipText) && pendingRequests.length === 0 && results.length === 0) return;
    const block = ctl.createContentBlock({ roleOverride: message.getRole() });
    if (text && !skipText) block.appendText(text);
    for (const { request, callId } of pendingRequests) {
      emittedCallIds.add(callId);
      block.appendToolRequest({
        callId,
        toolCallRequestId: request.id,
        name: request.name,
        parameters: request.arguments || {},
        pluginIdentifier: toolPluginIdentifier(tools, request.name),
      });
    }
    for (const result of results) {
      block.appendToolResult({
        callId: resolveResultCallId(result.toolCallId),
        toolCallRequestId: result.toolCallId,
        content: result.content,
      });
    }
  };

  const emitRequest = (callId: number, request: ToolCallRequest) => {
    registerRequest(callId, request);
    if (emittedCallIds.has(callId)) return;
    emittedCallIds.add(callId);
    const block = ctl.createContentBlock({ roleOverride: "assistant" });
    block.appendToolRequest({
      callId,
      toolCallRequestId: request.id,
      name: request.name,
      parameters: request.arguments || {},
      pluginIdentifier: toolPluginIdentifier(tools, request.name),
    });
  };

  return { beginRound, emit, emitRequest, registerRequest };
}

function createToolGenerationTracker(ctl: PredictionLoopHandlerController) {
  const statuses = new Map<number, {
    controller: PredictionProcessStatusController;
    name: string;
    argumentChars: number;
  }>();
  const start = (_roundIndex: number, callId: number) => {
    statuses.set(callId, {
      controller: ctl.createStatus({ status: "loading", text: "도구 호출 생성 중…" }),
      name: "",
      argumentChars: 0,
    });
  };
  const name = (_roundIndex: number, callId: number, toolName: string) => {
    const state = statuses.get(callId);
    if (!state) return;
    state.name = toolName;
    state.controller.setText(`도구 호출 생성 중: ${toolName}`);
  };
  const argument = (_roundIndex: number, callId: number, content: string) => {
    const state = statuses.get(callId);
    if (!state) return;
    state.argumentChars += content.length;
    state.controller.setText(`도구 호출 생성 중${state.name ? `: ${state.name}` : ""} (${state.argumentChars}자)`);
  };
  const end = (_roundIndex: number, callId: number) => {
    const state = statuses.get(callId);
    if (!state) return;
    state.controller.setText(`도구 실행 확인 중${state.name ? `: ${state.name}` : ""}`);
  };
  const waitingForApproval = (callId: number) => {
    const state = statuses.get(callId);
    if (!state) return;
    state.controller.setText(`도구 실행 승인 대기${state.name ? `: ${state.name}` : ""}`);
  };
  const executing = (callId: number) => {
    const state = statuses.get(callId);
    if (!state) return;
    state.controller.setText(`도구 실행 중${state.name ? `: ${state.name}` : ""}`);
  };
  const finalized = (callId: number) => {
    statuses.get(callId)?.controller.remove();
    statuses.delete(callId);
  };
  const failure = (_roundIndex: number, callId: number) => {
    const state = statuses.get(callId);
    if (!state) return;
    state.controller.setState({ status: "error", text: "도구 호출 생성 실패" });
    statuses.delete(callId);
  };
  return { start, name, argument, end, waitingForApproval, executing, finalized, failure };
}

function createRoundActivityTracker(
  ctl: PredictionLoopHandlerController,
  outerRoundIndex: number,
) {
  const roundSuffix = outerRoundIndex > 0 ? ` · 후속 호출 ${outerRoundIndex + 1}` : "";
  const controller = ctl.createStatus({
    status: "loading",
    text: `컨텍스트 계산 중…${roundSuffix}`,
  });
  let removed = false;
  let lastPercent = -1;
  const setText = (text: string) => {
    if (!removed) controller.setText(text);
  };
  const waitingForPrompt = () => setText(`프롬프트 처리 준비 중…${roundSuffix}`);
  const progress = (_roundIndex: number, value: number) => {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) return;
    const percent = Math.max(0, Math.min(100, numericValue * 100));
    // Keep the UI responsive without sending hundreds of nearly identical
    // status updates for large cached prompts.
    if (percent < 100 && lastPercent >= 0 && percent - lastPercent < 0.1) return;
    lastPercent = percent;
    setText(`프롬프트 처리 중 ${percent.toFixed(2)}%${roundSuffix}`);
  };
  const complete = () => {
    if (removed) return;
    removed = true;
    controller.remove();
  };
  return { waitingForPrompt, progress, firstToken: complete, complete };
}

const SYNTHETIC_REASONING_SEPARATOR =
  "__LM_STUDIO_INTERNAL_LSEP_SYNTHETIC_REASONING_END_f4e9a8d2c6b14d0c9e5f3a7b8c1d2e6a__";

function visibleAssistantOutput(text: string): { visibleText: string; hasFooter: boolean; note: unknown } {
  const raw = String(text || "");
  const separatorIndex = raw.lastIndexOf(SYNTHETIC_REASONING_SEPARATOR);
  return modelNotes.splitVisibleAnswer(separatorIndex >= 0
    ? raw.slice(separatorIndex + SYNTHETIC_REASONING_SEPARATOR.length) : raw);
}

export function createPredictionLoopHandler(
  noteStore: InstanceType<typeof modelNotes.ContinuityNoteStore> = new modelNotes.ContinuityNoteStore(),
  client?: LMStudioClient,
): PredictionLoopHandler {
  return async (ctl) => {
  ctl.guardAbort();
  const config = readConfig(ctl);
  const originalHistory = await ctl.pullHistory();
  const tokenSource = await ctl.tokenSource();
  if (selectedSourceIsThisPlugin(tokenSource)) {
    throw new Error("Select the actual Qwen/LLM in LM Studio. The context compactor is middleware, not a chat model.");
  }

  let workingDirectory = "";
  try { workingDirectory = ctl.getWorkingDirectory(); } catch { /* No stable chat workspace is available. */ }
  const noteWorkingDirectory = config.observeOnly ? "" : workingDirectory;
  const toolSession = await ctl.startToolUseSession();
  const mentionedProject = detectMentionedProject(originalHistory.getMessagesArray());
  const scope = resolveToolScope(
    config.projectEngine,
    config.projectIdentity,
    workingDirectory,
    toolSession.tools as Array<ScopedTool>,
    mentionedProject,
  );
  const attachmentContext = config.observeOnly
    ? { tools: [], instruction: "", attachmentCount: 0 }
    : createAttachmentContext(originalHistory, client);
  const scopedRemoteTools = config.observeOnly
    ? toolSession.tools as Array<ScopedTool>
    : filterToolsForScope(toolSession.tools as Array<ScopedTool>, scope);
  const modelTools = [...scopedRemoteTools, ...attachmentContext.tools] as Array<RemoteToolLike>;
  const scopeInstructions = config.observeOnly ? [] : [
    ...(scope.availableUnityTools + scope.availableUnrealTools > 0
      ? [renderToolScopeInstruction(scope)] : []),
    ...(attachmentContext.instruction ? [attachmentContext.instruction] : []),
  ];
  const emitter = createMessageEmitter(ctl, modelTools);
  const visibleHistory = Chat.from(originalHistory);
  const objectiveFingerprint = modelNotes.objectiveFingerprint(originalHistory.getMessagesArray());
  const originalMessages = originalHistory.getMessagesArray();
  const priorHistoryKey = originalMessages.at(-1)?.isUserMessage()
    ? modelNotes.historyKey(originalMessages.slice(0, -1), noteWorkingDirectory) : "";
  const priorNote = priorHistoryKey ? noteStore.read(priorHistoryKey) : null;
  let activeNote = priorNote?.scope.objectiveFingerprint === objectiveFingerprint
    ? modelNotes.reconcileStoredNote(priorNote, originalMessages) : null;
  let workingHistory = originalHistory;
  let roundIndex = 0;
  let activeActivity: ReturnType<typeof createRoundActivityTracker> | null = null;
  try {
    while (true) {
      ctl.guardAbort();
      activeActivity = createRoundActivityTracker(ctl, roundIndex);
      const noteEnabled = Boolean(noteWorkingDirectory && objectiveFingerprint && !config.observeOnly);
      if (noteEnabled && activeNote) {
        activeNote = modelNotes.reconcileStoredNote(activeNote, visibleHistory.getMessagesArray());
      }
      const beforeInput = composeModelHistory(
        workingHistory, noteEnabled ? activeNote : null, config, scopeInstructions, noteEnabled,
      );
      const before = await measureContext(tokenSource, beforeInput.history, config, modelTools);
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
            const measuredCandidate = composeModelHistory(
              candidate.history, noteEnabled ? activeNote : null, config, scopeInstructions, noteEnabled,
            ).history;
            const afterFirst = await measureContext(tokenSource, measuredCandidate, config, modelTools);
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
      const modelInput = composeModelHistory(
        workingHistory, noteEnabled ? activeNote : null, config, scopeInstructions, noteEnabled,
      ).history;

      if (config.showDebugInfo) ctl.debug({
        event: "direct_context_measurement",
        roundIndex,
        compacted,
        observeOnly: config.observeOnly,
        exactMeasurement: before.exact,
        messageCount: before.messageCount,
        inputTokens: before.inputTokens,
        remainingTokens: before.remainingTokens,
        projectEngine: scope.engine,
        projectScopeSource: scope.source,
        projectIdentity: scope.projectIdentity || undefined,
        availableUnityTools: scope.availableUnityTools,
        availableUnrealTools: scope.availableUnrealTools,
        visibleToolCount: modelTools.length,
        attachmentCount: attachmentContext.attachmentCount,
      });
      ctl.guardAbort();

      // SDK call IDs are guaranteed unique only within one .act invocation.
      // Clear correlation state after the prior round's captured messages have
      // been emitted, while keeping guard/finalized registration idempotent.
      emitter.beginRound();
      const stream = new PredictionStreamRenderer(ctl, modelNotes.splitVisibleAnswer, noteEnabled);
      const toolGeneration = createToolGenerationTracker(ctl);
      activeActivity.waitingForPrompt();
      const displayedMessages = new Set<ChatMessage>();
      const liveMessages = new Map<ChatMessage, { visibleMessage: ChatMessage; textStreamed: boolean }>();
      const captured = await runOneToolRound(
        tokenSource,
        modelInput,
        modelTools,
        ctl.abortSignal,
        {
          onPromptProcessingProgress: activeActivity.progress,
          onFirstToken: activeActivity.firstToken,
          onPredictionFragment: (fragment) => {
            activeActivity?.firstToken();
            stream.onFragment(fragment);
          },
          onToolCallRequestStart: (modelRoundIndex, callId) => {
            activeActivity?.firstToken();
            toolGeneration.start(modelRoundIndex, callId);
          },
          onToolCallRequestNameReceived: toolGeneration.name,
          onToolCallRequestArgumentFragmentGenerated: toolGeneration.argument,
          onToolCallRequestEnd: toolGeneration.end,
          onToolCallRequestFailure: toolGeneration.failure,
          onToolCallRequestFinalized: (_roundIndex, callId, info) => {
            emitter.registerRequest(callId, info.toolCallRequest);
            emitter.emitRequest(callId, info.toolCallRequest);
            toolGeneration.finalized(callId);
          },
          onMessageCaptured: (message) => {
            activeActivity?.firstToken();
            let visibleMessage = message;
            let textStreamed = false;
            if (message.isAssistantMessage() && message.getText()) {
              const streamed = stream.consumeAssistant(message.getText());
              const fallback = visibleAssistantOutput(message.getText());
              const visibleText = streamed.streamed ? streamed.visibleText : fallback.visibleText;
              textStreamed = streamed.streamed;
              if (visibleText !== message.getText()) {
                visibleMessage = ChatMessage.from(message);
                visibleMessage.replaceText(visibleText);
              }
            }
            liveMessages.set(message, { visibleMessage, textStreamed });
            emitter.emit(visibleMessage, textStreamed);
            displayedMessages.add(message);
          },
          guardToolCall: async (_roundIndex, callId, controller) => {
            const request = controller.toolCallRequest;
            // The SDK does not invoke onToolCallRequestFinalized for denied
            // calls, so register the stable call ID at the confirmation edge.
            emitter.registerRequest(callId, request);
            const allowedTool = modelTools.find((tool) => tool.name === request.name);
            if (!allowedTool) {
              controller.deny("Tool withheld by the deterministic project-engine scope.");
              return;
            }
            const projectBindingIdentity = config.observeOnly ? "" : scope.projectIdentity;
            const boundArguments = bindProjectArguments(
              allowedTool as ScopedTool, request, projectBindingIdentity,
            );
            const proposedArguments = boundArguments || request.arguments || {};
            if (isObservationOnlyToolCall(allowedTool, { ...request, arguments: proposedArguments })) {
              toolGeneration.executing(callId);
              if (boundArguments) controller.allowAndOverrideParameters(proposedArguments);
              else controller.allow();
              return;
            }
            toolGeneration.waitingForApproval(callId);
            const decision = await ctl.requestConfirmToolCall({
              callId,
              pluginIdentifier: toolPluginIdentifier(modelTools, request.name),
              name: request.name,
              parameters: proposedArguments,
            });
            if (decision.type === "deny") controller.deny(decision.denyReason);
            else {
              const confirmedArguments = decision.toolArgsOverride || proposedArguments;
              const reboundArguments = bindProjectArguments(
                allowedTool as ScopedTool,
                { ...request, arguments: confirmedArguments },
                projectBindingIdentity,
              );
              const finalArguments = reboundArguments || confirmedArguments;
              if (boundArguments || decision.toolArgsOverride || reboundArguments) {
                controller.allowAndOverrideParameters(finalArguments);
              } else controller.allow();
              toolGeneration.executing(callId);
            }
          },
        },
      );
      activeActivity.complete();
      activeActivity = null;
      for (const message of captured.messages) {
        const live = liveMessages.get(message);
        let visibleMessage = live?.visibleMessage || message;
        let textAlreadyStreamed = live?.textStreamed || false;
        if (message.isAssistantMessage() && message.getText()) {
          const extracted = modelNotes.splitVisibleAnswer(message.getText());
          if (!live) {
            const streamed = stream.consumeAssistant(message.getText());
            const fallback = visibleAssistantOutput(message.getText());
            textAlreadyStreamed = streamed.streamed;
            visibleMessage = ChatMessage.from(message);
            visibleMessage.replaceText(streamed.streamed ? streamed.visibleText : fallback.visibleText);
          }
          if (extracted.hasFooter) {
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
        if (!displayedMessages.has(message)) emitter.emit(visibleMessage, textAlreadyStreamed);
      }
      if (captured.failure !== undefined) throw captured.failure;
      if (!captured.continueAfterTools) break;
      roundIndex += 1;
    }
    if (activeNote && noteWorkingDirectory) {
      const nextHistoryKey = modelNotes.historyKey(visibleHistory.getMessagesArray(), noteWorkingDirectory);
      const verifiedNote = modelNotes.reconcileStoredNote(activeNote, visibleHistory.getMessagesArray());
      if (nextHistoryKey && verifiedNote) noteStore.write(nextHistoryKey, verifiedNote);
    }
  } finally {
    activeActivity?.complete();
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
  resolveToolScope,
  filterToolsForScope,
  bindProjectArguments,
  isObservationOnlyToolCall,
  createRoundActivityTracker,
};
