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
import crypto from "node:crypto";
import { selectMeasuredCandidate, boundPastReasoning } from "./context-budget";
import { attachmentBoundary } from "./attachment-boundary";
import { createAttachmentContext } from "./attachment-tools";
import { directConfigSchematics } from "./direct-config";
import { runOneToolRound } from "./round-loop";
import { GenerationRepetitionDetector, PredictionStreamRenderer } from "./prediction-stream";
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
  invariantSystemText(message: NormalizedMessage): string;
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
  reviewClaims?: Array<unknown>;
};
const { REASONING_SEPARATOR } = require("./continuity-text.js") as { REASONING_SEPARATOR: string };
const inputAvailability = require("./input-availability.js") as {
  currentRawObservations(messages: Array<NormalizedMessage>): Array<Record<string, unknown>>;
  historicalAvailabilityFromMemory(memory: unknown): Array<Record<string, unknown>>;
  projectInputAvailability(messages: Array<NormalizedMessage>, historical: Array<Record<string, unknown>>,
    options: { modelInputId: string; maxEntries?: number }): InputAvailabilityProjection;
  renderInputAvailabilityMetadata(projection: InputAvailabilityProjection): string;
  traceToolRound(messages: Array<NormalizedMessage>, options: {
    executionId: string; modelInputId: string; roundIndex: number;
  }): Record<string, unknown>;
};

const VOLATILE_OBSERVATION_KEYS = new Set([
  "observedAt", "lastObservedAt", "snapshotCapturedAt", "snapshotId", "nextCursor",
]);

function canonicalTelemetryValue(value: unknown, semantic = false): unknown {
  if (Array.isArray(value)) return value.map(item => canonicalTelemetryValue(item, semantic));
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value as Record<string, unknown>)
    .filter(([key]) => !(semantic && VOLATILE_OBSERVATION_KEYS.has(key)))
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, item]) => [key, canonicalTelemetryValue(item, semantic)]));
}

function telemetryFingerprint(value: unknown, semantic = false): string {
  let parsed = value;
  if (typeof value === "string") {
    try { parsed = JSON.parse(value); } catch { parsed = value; }
  }
  const serialized = JSON.stringify(canonicalTelemetryValue(parsed, semantic));
  return crypto.createHash("sha256")
    .update(serialized === undefined ? "undefined" : serialized)
    .digest("hex");
}

function resultContent(result: unknown): unknown {
  if (!result || typeof result !== "object") return result;
  return (result as { content?: unknown }).content;
}

function messageEvidenceTelemetry(messages: Array<ChatMessage>) {
  const calls = messages.flatMap(message => message.getToolCallRequests()).map(request => ({
    toolName: request.name,
    argumentsFingerprint: telemetryFingerprint(request.arguments || {}),
  }));
  const results = messages.flatMap(message => message.getToolCallResults()).map(result => {
    const content = resultContent(result);
    return {
      resultFingerprint: telemetryFingerprint(content),
      semanticResultFingerprint: telemetryFingerprint(content, true),
    };
  });
  return { calls, results };
}

function serializedCheckpointCounts(checkpoint: string) {
  try {
    const marker = checkpoint.indexOf("[Direct continuity state v2]");
    const start = checkpoint.indexOf("{", marker);
    const parsed = JSON.parse(checkpoint.slice(start));
    return {
      serializedSchemaVersion: parsed?.schemaVersion ?? null,
      serializedCompactionGeneration: parsed?.compactionGeneration ?? null,
      serializedToolOutcomeCount: parsed?.currentWorkStatus?.recentToolOutcomes?.length || 0,
      serializedGitObservationCount: parsed?.currentWorkStatus?.gitObservations?.length || 0,
      serializedFileObservationCount: parsed?.currentWorkStatus?.modifiedOrObservedFiles?.length || 0,
      serializedObservedRangeCount: (parsed?.currentWorkStatus?.modifiedOrObservedFiles || [])
        .reduce((count: number, file: { observedLineRanges?: Array<unknown> }) => (
          count + (Array.isArray(file?.observedLineRanges) ? file.observedLineRanges.length : 0)
        ), 0),
    };
  } catch {
    return { serializedSchemaVersion: null, serializedCompactionGeneration: null,
      serializedToolOutcomeCount: 0, serializedGitObservationCount: 0,
      serializedFileObservationCount: 0, serializedObservedRangeCount: 0 };
  }
}

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
  memory?: {
    currentWorkStatus?: { modifiedOrObservedFiles?: Array<Record<string, unknown>> };
  };
  serializationDiagnostics?: Record<string, unknown>;
};

type InputAvailabilityProjection = {
  modelInputId: string;
  verificationBoundary: "final_sdk_chat";
  hostInputVerification: "unknown" | "verified";
  entries: Array<Record<string, unknown>>;
  omittedEntryCount: number;
  entryListComplete: boolean;
  metrics: Record<string, number>;
};

export type ContextMeasurement = {
  contextLength: number;
  inputTokens: number;
  remainingTokens: number;
  exact: boolean;
  messageCount: number;
  fit: boolean | null;
};

type DirectConfig = {
  projectEngine: ProjectEngineSetting;
  projectIdentity: string;
  observeOnly: boolean;
  showDebugInfo: boolean;
  pastReasoningTokens: number;
  reviewProgress: boolean;
  softRemainingTokens: number;
  hardRemainingTokens: number;
  maxOutputReserve: number;
  safetyMarginTokens: number;
  assumedContextLength: number;
  recentCompleteTurns: number;
  compactAboveMessageCount: number;
  maxCheckpointChars: number;
  maxToolResultChars: number;
  toolStagnationAction: StagnationAction;
  toolStagnationRounds: number;
  generationRepetitionAction: StagnationAction;
  generationRepeatCount: number;
  inputAvailabilityMode: InputAvailabilityMode;
};

type StagnationAction = "off" | "warn" | "pause";
type InputAvailabilityMode = "off" | "observe" | "inject";

type RemoteToolLike = Tool & {
  name: string;
  pluginIdentifier?: string;
  description: string;
  parametersJsonSchema?: unknown;
};

const UNITY_OBSERVATION_TOOLS = new Set([
  "workspace_status", "git_status", "git_log", "git_changed_files", "git_diff_file", "git_read_file",
  "unity_references",
  "unity_snapshot",
  "unity_debug_query",
  "unity_symbols",
  "unity_status",
  "unity_git",
  "list_directory",
  "search_files",
  "read_file",
  "structured_data_read",
  "unity_find",
  "unity_object_read",
  "unity_logs",
]);

const UNREAL_OBSERVATION_TOOLS = new Set([
  "workspace_status", "git_status", "git_log", "git_changed_files", "git_diff_file", "git_read_file",
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

function assistantNoteTelemetry(note: ContinuityNote | null, enabled: boolean) {
  const items = note ? [note.decisions, note.rejectedHypotheses, note.openQuestions].flat() as Array<{
    status?: unknown;
  }> : [];
  const statusCounts = { open: 0, resolved: 0, superseded: 0 };
  for (const item of items) {
    const status = String(item?.status || "open") as keyof typeof statusCounts;
    if (Object.hasOwn(statusCounts, status)) statusCounts[status] += 1;
  }
  return {
    protocol: enabled ? "enabled" : "disabled",
    state: note ? "injected" : "absent",
    itemCount: items.length,
    reviewClaimCount: note?.reviewClaims?.length || 0,
    statusCounts,
  };
}

function stagnationAction(value: unknown): StagnationAction {
  return value === "off" || value === "pause" ? value : "warn";
}

function inputAvailabilityMode(value: unknown): InputAvailabilityMode {
  return value === "off" || value === "inject" ? value : "observe";
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
    showDebugInfo: config.get("showDebugInfo") !== false,
    reviewProgress: config.get("reviewProgress") === true,
    pastReasoningTokens: numeric(config.get("pastReasoningTokens"), 0, 0, 16384),
    softRemainingTokens: numeric(config.get("softRemainingTokens"), 14000, 0, 1_000_000),
    hardRemainingTokens: numeric(config.get("hardRemainingTokens"), 8000, 0, 1_000_000),
    maxOutputReserve: numeric(config.get("maxOutputReserve"), 8192, 256, 131072),
    safetyMarginTokens: numeric(config.get("safetyMarginTokens"), 1536, 0, 131072),
    assumedContextLength: numeric(config.get("assumedContextLength"), 65536, 2048, 4_000_000),
    recentCompleteTurns: numeric(config.get("recentCompleteTurns"), 2, 0, 20),
    compactAboveMessageCount: numeric(config.get("compactAboveMessageCount"), 24, 4, 10000),
    maxCheckpointChars: numeric(config.get("maxCheckpointChars"), 22000, 2000, 100000),
    maxToolResultChars: numeric(config.get("maxToolResultChars"), 1200, 200, 10000),
    toolStagnationAction: stagnationAction(config.get("toolStagnationAction")),
    toolStagnationRounds: numeric(config.get("toolStagnationRounds"), 3, 2, 20),
    generationRepetitionAction: stagnationAction(config.get("generationRepetitionAction")),
    generationRepeatCount: numeric(config.get("generationRepeatCount"), 3, 2, 10),
    inputAvailabilityMode: inputAvailabilityMode(config.get("inputAvailabilityMode")),
  };
}

class ToolRoundStagnationDetector {
  private previous = "";
  private count = 0;

  observe(messages: Array<ChatMessage>) {
    const evidence = messageEvidenceTelemetry(messages);
    if (!evidence.calls.length || !evidence.results.length) {
      this.previous = "";
      this.count = 0;
      return { repeated: false, count: 0, fingerprint: "" };
    }
    const fingerprint = telemetryFingerprint({
      calls: evidence.calls,
      results: evidence.results.map(result => result.semanticResultFingerprint),
    });
    this.count = fingerprint === this.previous ? this.count + 1 : 1;
    this.previous = fingerprint;
    return { repeated: this.count > 1, count: this.count, fingerprint };
  }
}

function normalizeMessages(messages: Array<ChatMessage>): Array<NormalizedMessage> {
  return messages.map((message) => ({
    role: message.getRole(),
    text: message.getText(),
    hasFiles: message.hasFiles(),
    toolRequests: message.getToolCallRequests(),
    toolResults: message.getToolCallResults(),
  }));
}

function normalizeHistory(history: Chat): Array<NormalizedMessage> {
  return normalizeMessages(history.getMessagesArray());
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
  const remainingTokens = contextLength - inputTokens - config.maxOutputReserve - config.safetyMarginTokens;
  return {
    contextLength,
    inputTokens,
    remainingTokens,
    exact,
    messageCount: history.length,
    fit: remainingTokens >= 0 ? (exact ? true : null) : false,
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
    ...normalized.filter(message => message.role === "system")
      .map(message => core.invariantSystemText(message)).filter(Boolean),
    checkpoint.checkpoint,
  ].filter(Boolean).join("\n\n");
  if (systemText) compacted.append("system", systemText);
  if (checkpoint.assistantCheckpoint) compacted.append("assistant", checkpoint.assistantCheckpoint);
  for (let index = 0; index < messages.length; index += 1) {
    if (!messages[index].isSystemPrompt() && retained.has(index)) compacted.append(messages[index]);
  }
  return { history: compacted, checkpoint };
}

type CompactedHistory = ReturnType<typeof buildCompactedHistory>;

type SoftCompactionSelection = {
  candidate: CompactedHistory;
  targetRemainingTokens: number;
  remainingTokensAfter: number | null;
  maxCurrentTurnMessages: number | null;
  fit: boolean | null;
};

async function selectSoftCompaction(
  history: Chat,
  config: DirectConfig,
  checkpointOptions: Record<string, unknown>,
  measureCandidate: (history: Chat) => Promise<ContextMeasurement>,
): Promise<SoftCompactionSelection> {
  const targetRemainingTokens = config.softRemainingTokens
    + Math.max(1024, Math.trunc(Math.max(0,
      config.softRemainingTokens - config.hardRemainingTokens) / 2));
  const recent = buildCompactedHistory(
    history, config.recentCompleteTurns, config, checkpointOptions,
  );
  if (recent.history !== history) {
    const measured = await measureCandidate(recent.history);
    if (measured.remainingTokens >= targetRemainingTokens) {
      return { candidate: recent, targetRemainingTokens,
        remainingTokensAfter: measured.remainingTokens, maxCurrentTurnMessages: null, fit: true };
    }
  }

  const selected = await selectMeasuredCandidate(history.length, targetRemainingTokens,
    cap => buildCompactedHistory(history, 0, config, { ...checkpointOptions, maxCurrentTurnMessages: cap }),
    async candidate => candidate.history === history ? null : (await measureCandidate(candidate.history)).remainingTokens);
  return { ...selected, targetRemainingTokens };

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
  const instruction = config.reviewProgress ? modelNotes.NOTE_INSTRUCTION.replace(
    "No other keys. refs may contain",
    "The optional reviewClaims array uses [{path, sha256, reviewScope, statement, refs}]; reviewClaims require an observed file hash and tool-call refs and are assistant claims, never verified completion. No other keys. refs may contain") : modelNotes.NOTE_INSTRUCTION;
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

function visibleAssistantOutput(text: string): { visibleText: string; hasFooter: boolean; note: unknown } {
  const raw = String(text || "");
  const separatorIndex = raw.lastIndexOf(REASONING_SEPARATOR);
  return modelNotes.splitVisibleAnswer(separatorIndex >= 0
    ? raw.slice(separatorIndex + REASONING_SEPARATOR.length) : raw);
}

function modelHistoryMessage(message: ChatMessage): ChatMessage {
  if (!message.isAssistantMessage() || !message.getText()) return message;
  const extracted = modelNotes.splitVisibleAnswer(message.getText());
  if (!extracted.hasFooter || extracted.visibleText === message.getText()) return message;
  const copy = ChatMessage.from(message);
  // Keep the SDK's raw reasoning/non-reasoning serialization for the next
  // tool round. Only the private continuity footer is removed from model input.
  copy.replaceText(extracted.visibleText);
  return copy;
}

export function createPredictionLoopHandler(
  noteStore: InstanceType<typeof modelNotes.ContinuityNoteStore> = new modelNotes.ContinuityNoteStore(),
  client?: LMStudioClient,
): PredictionLoopHandler {
  return async (ctl) => {
  ctl.guardAbort();
  const config = readConfig(ctl);
  const pulledHistory = await ctl.pullHistory();
  const attachments = config.observeOnly ? { modelHistory: pulledHistory, attachmentHistory: pulledHistory } : attachmentBoundary.restore(pulledHistory, client);
  const originalHistory = attachments.modelHistory;
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
    : createAttachmentContext(attachments.attachmentHistory, client);
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
  const executionId = crypto.randomUUID();
  const historicalAvailabilityLedger: Array<Record<string, unknown>> = [];
  if (config.inputAvailabilityMode !== "off") {
    const normalizedOriginal = normalizeHistory(originalHistory);
    historicalAvailabilityLedger.push(...inputAvailability.currentRawObservations(normalizedOriginal));
    const bootstrap = core.buildCheckpoint(normalizedOriginal, {
      recentCompleteTurns: config.recentCompleteTurns,
      maxCheckpointChars: config.maxCheckpointChars,
      maxToolResultChars: config.maxToolResultChars,
    });
    historicalAvailabilityLedger.push(...inputAvailability.historicalAvailabilityFromMemory(bootstrap.memory));
  }
  const objectiveFingerprint = modelNotes.objectiveFingerprint(originalHistory.getMessagesArray());
  const originalMessages = originalHistory.getMessagesArray();
  const priorHistoryKey = originalMessages.at(-1)?.isUserMessage()
    ? modelNotes.historyKey(originalMessages.slice(0, -1), noteWorkingDirectory) : "";
  const priorNote = priorHistoryKey ? noteStore.read(priorHistoryKey) : null;
  let activeNote = priorNote?.scope.objectiveFingerprint === objectiveFingerprint
    ? modelNotes.reconcileStoredNote(priorNote, originalMessages) : null;
  // A newly attached rubric may change what "review C" means even when the
  // request text and source hashes are identical. Never carry review claims
  // across this unverified criterion boundary.
  if (activeNote && attachments.attachmentHistory.getMessagesArray().at(-1)?.hasFiles()) {
    delete activeNote.reviewClaims;
  }
  let workingHistory = originalHistory;
  let roundIndex = 0;
  let activeActivity: ReturnType<typeof createRoundActivityTracker> | null = null;
  const toolStagnation = new ToolRoundStagnationDetector();
  try {
    while (true) {
      ctl.guardAbort();
      const modelInputId = `${executionId}:prediction-${roundIndex + 1}`;
      activeActivity = createRoundActivityTracker(ctl, roundIndex);
      const noteEnabled = Boolean(noteWorkingDirectory && objectiveFingerprint && !config.observeOnly);
      if (noteEnabled && activeNote) {
        activeNote = modelNotes.reconcileStoredNote(activeNote, visibleHistory.getMessagesArray());
      }
      const beforeInput = composeModelHistory(
        workingHistory, noteEnabled ? activeNote : null, config, scopeInstructions, noteEnabled,
      );
      let before = await measureContext(tokenSource, beforeInput.history, config, modelTools);
      let modelHistory = workingHistory;
      let compacted = false;
      let compactionCheckpoint: CheckpointResult | null = null;
      let compactionRetention: Record<string, unknown> | null = null;
      if (core.shouldCompact(before, config)) {
        const counter = tokenSource as { countTokens?: (text: string) => Promise<number> };
        if (config.pastReasoningTokens > 0 && counter.countTokens) {
          workingHistory = await boundPastReasoning(workingHistory, config.pastReasoningTokens, text => counter.countTokens!(text));
          modelHistory = workingHistory;
          before = await measureContext(tokenSource,
            composeModelHistory(workingHistory, noteEnabled ? activeNote : null, config, scopeInstructions, noteEnabled).history,
            config, modelTools);
        }
      }
      if (core.shouldCompact(before, config)) {
        const hard = before.remainingTokens <= config.hardRemainingTokens;
        const checkpointOptions = {
          maxCheckpointChars: config.maxCheckpointChars - beforeInput.overhead,
        };
        let candidate: CompactedHistory;
        if (!hard && before.exact) {
          const selected = await selectSoftCompaction(
            workingHistory, config, checkpointOptions,
            async (candidateHistory) => measureContext(
              tokenSource,
              composeModelHistory(candidateHistory, noteEnabled ? activeNote : null,
                config, scopeInstructions, noteEnabled).history,
              config,
              modelTools,
            ),
          );
          candidate = selected.candidate;
          compactionRetention = {
            mode: "soft_token_budget",
            targetRemainingTokens: selected.targetRemainingTokens,
            remainingTokensAfter: selected.remainingTokensAfter,
            maxCurrentTurnMessages: selected.maxCurrentTurnMessages,
          };
        } else {
          candidate = buildCompactedHistory(
            workingHistory,
            hard ? 0 : config.recentCompleteTurns,
            config,
            { ...checkpointOptions, ...(hard ? { maxCurrentTurnMessages: 2 } : {}) },
          );
        }
        if (!hard && !before.exact) {
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
              { ...checkpointOptions, maxCurrentTurnMessages: 2 },
            );
          }
          compactionRetention = { mode: "inexact_message_fallback",
            maxCurrentTurnMessages: needsBoundedCurrentTurn ? 2 : null };
        } else if (hard) {
          compactionRetention = { mode: "hard_latest_exchange",
            maxCurrentTurnMessages: 2 };
        }
        if (candidate.history !== workingHistory) {
          modelHistory = candidate.history;
          compacted = modelHistory !== workingHistory;
          compactionCheckpoint = candidate.checkpoint;
        }
      }
      workingHistory = modelHistory;
      const assembleModelInput = async (sourceHistory: Chat) => {
        const baseComposition = composeModelHistory(
          sourceHistory, noteEnabled ? activeNote : null, config, scopeInstructions, noteEnabled,
        );
        const baseMeasurement = await measureContext(tokenSource, baseComposition.history, config, modelTools);
        let projection = config.inputAvailabilityMode === "off" ? null
          : inputAvailability.projectInputAvailability(
            normalizeHistory(baseComposition.history), historicalAvailabilityLedger, { modelInputId },
          );
        if (config.inputAvailabilityMode !== "inject" || !projection || projection.entries.length === 0) {
          return {
            composition: baseComposition,
            history: baseComposition.history,
            measurement: baseMeasurement,
            projection,
            metadataChars: 0,
            metadataTokens: 0,
          };
        }
        let metadata = inputAvailability.renderInputAvailabilityMetadata(projection);
        let composition = composeModelHistory(
          sourceHistory, noteEnabled ? activeNote : null, config,
          [...scopeInstructions, metadata], noteEnabled,
        );
        let measurement = await measureContext(tokenSource, composition.history, config, modelTools);
        const finalProjection = inputAvailability.projectInputAvailability(
          normalizeHistory(composition.history), historicalAvailabilityLedger, { modelInputId },
        );
        const finalMetadata = inputAvailability.renderInputAvailabilityMetadata(finalProjection);
        // One bounded reassembly is sufficient: metadata is derived only from
        // tool-result bodies, and adding the metadata does not create one.
        if (finalMetadata !== metadata) {
          metadata = finalMetadata;
          composition = composeModelHistory(
            sourceHistory, noteEnabled ? activeNote : null, config,
            [...scopeInstructions, metadata], noteEnabled,
          );
          measurement = await measureContext(tokenSource, composition.history, config, modelTools);
        }
        projection = inputAvailability.projectInputAvailability(
          normalizeHistory(composition.history), historicalAvailabilityLedger, { modelInputId },
        );
        return {
          composition,
          history: composition.history,
          measurement,
          projection,
          metadataChars: metadata.length,
          metadataTokens: baseMeasurement.exact && measurement.exact
            ? Math.max(0, measurement.inputTokens - baseMeasurement.inputTokens) : null,
        };
      };
      let assembledInput = await assembleModelInput(workingHistory);
      let modelComposition = assembledInput.composition;
      let modelInput = assembledInput.history;
      let finalMeasurement = assembledInput.measurement;
      if (finalMeasurement.remainingTokens < 0 && !config.observeOnly) {
        const emergency = buildCompactedHistory(workingHistory, 0, config, {
          maxCheckpointChars: config.maxCheckpointChars - modelComposition.overhead,
          maxCurrentTurnMessages: 2,
        });
        if (emergency.history !== workingHistory) {
          workingHistory = emergency.history;
          modelHistory = emergency.history;
          compacted = true;
          compactionCheckpoint = emergency.checkpoint;
          compactionRetention = { mode: "final_budget_emergency", maxCurrentTurnMessages: 2 };
          assembledInput = await assembleModelInput(workingHistory);
          modelComposition = assembledInput.composition;
          modelInput = assembledInput.history;
          finalMeasurement = assembledInput.measurement;
        }
      }

      if (finalMeasurement.remainingTokens < 0 && !config.observeOnly) {
        if (config.showDebugInfo) ctl.debug({
          event: "direct_context_budget_rejected",
          executionId,
          modelInputId,
          roundIndex,
          exactMeasurement: finalMeasurement.exact,
          fit: false,
          contextLength: finalMeasurement.contextLength,
          inputTokens: finalMeasurement.inputTokens,
          outputReserve: config.maxOutputReserve,
          safetyMargin: config.safetyMarginTokens,
          remainingTokens: finalMeasurement.remainingTokens,
          modelCallSkipped: true,
        });
        ctl.createStatus({
          status: "error",
          text: "최소 연속성 컨텍스트도 선택한 모델의 입력 예산을 초과해 모델 호출을 중단했습니다.",
        });
        throw new Error(`CONTEXT_BUDGET_EXCEEDED: final model input exceeds the context budget by ${-finalMeasurement.remainingTokens} tokens`);
      }

      const retainedIndexes = compactionCheckpoint?.retainedIndexes || [];
      const retainedIndexLimit = 64;
      const inputEvidence = messageEvidenceTelemetry(modelInput.getMessagesArray());
      const serializedCounts = compactionCheckpoint
        ? serializedCheckpointCounts(compactionCheckpoint.checkpoint)
        : serializedCheckpointCounts("");
      if (config.showDebugInfo) ctl.debug({
        event: "direct_context_measurement",
        executionId,
        modelInputId,
        roundIndex,
        compacted,
        observeOnly: config.observeOnly,
        exactMeasurement: before.exact,
        messageCount: before.messageCount,
        inputTokens: before.inputTokens,
        remainingTokens: before.remainingTokens,
        finalExactMeasurement: finalMeasurement.exact,
        finalInputTokens: finalMeasurement.inputTokens,
        finalRemainingTokens: finalMeasurement.remainingTokens,
        finalFit: finalMeasurement.fit,
        finalMessageCount: finalMeasurement.messageCount,
        assistantNote: assistantNoteTelemetry(activeNote, noteEnabled),
        projectEngine: scope.engine,
        projectScopeSource: scope.source,
        projectIdentity: scope.projectIdentity || undefined,
        availableUnityTools: scope.availableUnityTools,
        availableUnrealTools: scope.availableUnrealTools,
        visibleToolCount: modelTools.length,
        attachmentCount: attachmentContext.attachmentCount,
        inputAvailability: assembledInput.projection ? {
          mode: config.inputAvailabilityMode,
          verificationBoundary: assembledInput.projection.verificationBoundary,
          hostInputVerification: assembledInput.projection.hostInputVerification,
          entries: assembledInput.projection.entries,
          omittedEntryCount: assembledInput.projection.omittedEntryCount,
          entryListComplete: assembledInput.projection.entryListComplete,
          metrics: assembledInput.projection.metrics,
          metadataChars: assembledInput.metadataChars,
          metadataTokens: assembledInput.metadataTokens,
          metadataTokenMeasurement: assembledInput.metadataTokens === null ? "unknown" : "prompt_delta",
        } : { mode: "off" },
        compactionDetails: compacted && compactionCheckpoint ? {
          omittedMessageCount: compactionCheckpoint.omittedMessageCount,
          retainedMessageCount: retainedIndexes.length,
          retainedMessageIndexes: retainedIndexes.slice(-retainedIndexLimit),
          retainedMessageIndexesTruncated: retainedIndexes.length > retainedIndexLimit,
          checkpointChars: compactionCheckpoint.checkpoint.length,
          assistantCheckpointChars: compactionCheckpoint.assistantCheckpoint.length,
          ...serializedCounts,
          serialization: compactionCheckpoint.serializationDiagnostics,
          inputToolResultCount: inputEvidence.results.length,
          inputLatestToolResultFingerprint: inputEvidence.results.at(-1)?.resultFingerprint,
          inputLatestSemanticResultFingerprint: inputEvidence.results.at(-1)?.semanticResultFingerprint,
          retention: compactionRetention,
        } : undefined,
      });
      ctl.guardAbort();

      // SDK call IDs are guaranteed unique only within one .act invocation.
      // Clear correlation state after the prior round's captured messages have
      // been emitted, while keeping guard/finalized registration idempotent.
      emitter.beginRound();
      const stream = new PredictionStreamRenderer(ctl, modelNotes.splitVisibleAnswer, noteEnabled);
      const generationRepetition = new GenerationRepetitionDetector(config.generationRepeatCount);
      let generationRepetitionReported = false;
      const toolGeneration = createToolGenerationTracker(ctl);
      const runtimeToolTrace = new Map<number, Record<string, unknown>>();
      const traceCall = (callId: number, values: Record<string, unknown>) => {
        runtimeToolTrace.set(callId, {
          ...(runtimeToolTrace.get(callId) || {}),
          callKey: `${executionId}:${roundIndex}:sdk-${callId}`,
          causalModelInputId: modelInputId,
          sdkCallId: callId,
          ...values,
        });
      };
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
          abortAfterPredictionFragment: config.observeOnly || config.generationRepetitionAction === "off"
            ? undefined
            : (fragment) => {
              if (!generationRepetition.observe(fragment) || generationRepetitionReported) return undefined;
              generationRepetitionReported = true;
              const pause = config.generationRepetitionAction === "pause";
              ctl.createStatus({
                status: pause ? "canceled" : "done",
                text: pause
                  ? "생성 중 반복 블록을 감지해 이 응답을 일시 중지했습니다."
                  : "생성 중 반복 블록을 감지했습니다. 응답은 계속 진행합니다.",
              });
              if (config.showDebugInfo) ctl.debug({
                event: "within_generation_repetition",
                roundIndex,
                action: config.generationRepetitionAction,
                repeatCount: config.generationRepeatCount,
              });
              if (!pause) return undefined;
              const reason = new Error("Generation paused after repeated text blocks");
              reason.name = "ContextCompactorGenerationRepetition";
              return reason;
            },
          onToolCallRequestStart: (modelRoundIndex, callId) => {
            activeActivity?.firstToken();
            toolGeneration.start(modelRoundIndex, callId);
            traceCall(callId, { sdkPredictionRound: modelRoundIndex, generationState: "started" });
          },
          onToolCallRequestNameReceived: (modelRoundIndex, callId, name) => {
            toolGeneration.name(modelRoundIndex, callId, name);
            traceCall(callId, { toolName: name });
          },
          onToolCallRequestArgumentFragmentGenerated: (modelRoundIndex, callId, content) => {
            toolGeneration.argument(modelRoundIndex, callId, content);
            const prior = runtimeToolTrace.get(callId);
            traceCall(callId, { argumentChars: Number(prior?.argumentChars || 0) + content.length });
          },
          onToolCallRequestEnd: (modelRoundIndex, callId) => {
            toolGeneration.end(modelRoundIndex, callId);
            traceCall(callId, { generationState: "generated" });
          },
          onToolCallRequestFailure: (modelRoundIndex, callId) => {
            toolGeneration.failure(modelRoundIndex, callId);
            traceCall(callId, { generationState: "failed", executionState: "not_executed" });
          },
          onToolCallRequestFinalized: (_roundIndex, callId, info) => {
            emitter.registerRequest(callId, info.toolCallRequest);
            emitter.emitRequest(callId, info.toolCallRequest);
            toolGeneration.finalized(callId);
            traceCall(callId, {
              generationState: "finalized",
              providerRequestId: info.toolCallRequest.id || undefined,
              toolName: info.toolCallRequest.name,
              proposedArgumentsFingerprint: telemetryFingerprint(info.toolCallRequest.arguments || {}),
            });
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
              traceCall(callId, { validationState: "denied_scope", executionState: "not_executed" });
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
              traceCall(callId, {
                validationState: "allowed",
                approvalState: "not_required_observation",
                executionState: "dispatched",
                proposedArgumentsFingerprint: telemetryFingerprint(request.arguments || {}),
                executedArgumentsFingerprint: telemetryFingerprint(proposedArguments),
              });
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
            if (decision.type === "deny") {
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
              if (boundArguments || decision.toolArgsOverride || reboundArguments) {
                controller.allowAndOverrideParameters(finalArguments);
              } else controller.allow();
              toolGeneration.executing(callId);
              traceCall(callId, {
                validationState: "allowed",
                approvalState: "allowed",
                executionState: "dispatched",
                proposedArgumentsFingerprint: telemetryFingerprint(request.arguments || {}),
                executedArgumentsFingerprint: telemetryFingerprint(finalArguments),
              });
            }
          },
        },
      );
      activeActivity.complete();
      activeActivity = null;
      let noteLifecycle = activeNote ? "injected_existing" : "absent";
      for (const message of captured.messages) {
        const live = liveMessages.get(message);
        let visibleMessage = live?.visibleMessage || message;
        let historyMessage = message;
        let textAlreadyStreamed = live?.textStreamed || false;
        if (message.isAssistantMessage() && message.getText()) {
          const extracted = modelNotes.splitVisibleAnswer(message.getText());
          historyMessage = modelHistoryMessage(message);
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
                + activeNote.openQuestions.length + (activeNote.reviewClaims?.length || 0) === 0) {
                activeNote = null;
                noteLifecycle = "rejected_empty";
              } else noteLifecycle = activeNote ? "accepted" : "rejected_invalid";
            } else {
              noteLifecycle = noteEnabled ? "rejected_invalid" : "ignored_disabled";
            }
          }
        }
        workingHistory.append(historyMessage);
        visibleHistory.append(visibleMessage);
        if (!displayedMessages.has(message)) emitter.emit(visibleMessage, textAlreadyStreamed);
      }
      if (config.showDebugInfo) {
        const outputEvidence = messageEvidenceTelemetry(captured.messages);
        const normalizedCaptured = normalizeMessages(captured.messages);
        ctl.debug({
          event: "direct_round_observation",
          executionId,
          modelInputId,
          roundIndex,
          finishReason: captured.finishReason || (captured.failure === undefined ? "unknown" : "failed"),
          continueAfterTools: captured.continueAfterTools,
          modelInputToolResultCount: inputEvidence.results.length,
          modelInputLatestResultFingerprint: inputEvidence.results.at(-1)?.resultFingerprint,
          modelInputLatestSemanticResultFingerprint: inputEvidence.results.at(-1)?.semanticResultFingerprint,
          calls: outputEvidence.calls,
          results: outputEvidence.results,
          toolTrace: {
            runtime: [...runtimeToolTrace.values()],
            captured: inputAvailability.traceToolRound(normalizedCaptured, {
              executionId, modelInputId, roundIndex,
            }),
          },
          modelNoteLifecycle: noteLifecycle,
        });
      }
      if (config.inputAvailabilityMode !== "off") {
        historicalAvailabilityLedger.push(...inputAvailability.currentRawObservations(
          normalizeMessages(captured.messages),
        ));
      }
      if (!config.observeOnly && config.toolStagnationAction !== "off" && captured.continueAfterTools) {
        const stagnation = toolStagnation.observe(captured.messages);
        if (stagnation.count >= config.toolStagnationRounds) {
          const pause = config.toolStagnationAction === "pause";
          ctl.createStatus({
            status: pause ? "canceled" : "done",
            text: pause
              ? `동일한 도구 라운드가 ${stagnation.count}회 반복되어 후속 호출을 일시 중지했습니다.`
              : `동일한 도구 라운드가 ${stagnation.count}회 반복되었습니다. 후속 호출은 계속합니다.`,
          });
          if (config.showDebugInfo) ctl.debug({
            event: "tool_round_stagnation",
            roundIndex,
            action: config.toolStagnationAction,
            repeatCount: stagnation.count,
            semanticFingerprint: stagnation.fingerprint,
          });
          if (pause) captured.continueAfterTools = false;
        }
      }
      if (captured.failure !== undefined) throw captured.failure;
      if (!captured.continueAfterTools) break;
      roundIndex += 1;
    }
    if (activeNote && noteWorkingDirectory) {
      const nextHistoryKey = modelNotes.historyKey(visibleHistory.getMessagesArray(), noteWorkingDirectory);
      const verifiedNote = modelNotes.reconcileStoredNote(activeNote, visibleHistory.getMessagesArray());
      const stored = Boolean(nextHistoryKey && verifiedNote && noteStore.write(nextHistoryKey, verifiedNote));
      if (config.showDebugInfo) ctl.debug({
        event: "assistant_note_persistence",
        status: stored ? "stored" : "not_stored",
        reason: stored ? undefined : !nextHistoryKey ? "unstable_history_key"
          : !verifiedNote ? "scope_reconciliation_failed" : "atomic_write_failed",
        itemCount: verifiedNote
          ? verifiedNote.decisions.length + verifiedNote.rejectedHypotheses.length + verifiedNote.openQuestions.length
          : 0,
        reviewClaimCount: verifiedNote?.reviewClaims?.length || 0,
      });
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
  telemetryFingerprint,
  messageEvidenceTelemetry,
  serializedCheckpointCounts,
  ToolRoundStagnationDetector,
  GenerationRepetitionDetector,
  modelHistoryMessage,
  selectSoftCompaction,
};
