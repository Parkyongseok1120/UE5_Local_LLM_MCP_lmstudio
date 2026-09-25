import {
  Chat,
  ChatMessage,
  type ChatMessagePartData,
  type ChatMessageData,
  type ChatMessageRoleData,
  type PredictionLoopHandlerController,
  type PredictionProcessStatusController,
  type ToolCallRequest
} from "@lmstudio/sdk";
import { type RemoteToolLike } from "./tool-capability-registry";

type ContentController = Pick<PredictionLoopHandlerController, "createContentBlock">;

/** Owns the host-visible transcript for one execution. SDK messages are not
 * host messages: streamed text and each finalized request occupy separate
 * blocks. Record exactly those blocks, including partial visible output on
 * cancellation, while excluding thinking blocks. WorkingContext separately
 * owns which completed planning/evidence is safe to carry to the next turn. */
export class VisibleHistoryRecorder {
  private readonly blocks: Array<{ role: ChatMessageRoleData; text: string; parts: ChatMessagePartData[] }> = [];
  private readonly initial: Chat;
  readonly controller: ContentController;

  constructor(ctl: ContentController, initial: Chat) {
    this.initial = Chat.from(initial);
    this.controller = { createContentBlock: options => {
      const block = ctl.createContentBlock(options);
      if (options?.includeInContext === false) return block;
      const entry = { role: options?.roleOverride || "assistant", text: "", parts: [] as ChatMessagePartData[] };
      this.blocks.push(entry);
      return new Proxy(block, { get(target, key) {
        if (key === "appendText") return (...args: Parameters<typeof block.appendText>) => {
          const result = target.appendText(...args); entry.text += args[0]; return result;
        };
        if (key === "appendToolRequest") return (...args: Parameters<typeof block.appendToolRequest>) => {
          const result = target.appendToolRequest(...args), request = args[0];
          // Same shape as LM Studio's rehydrated function requests.
          entry.parts.push({ type: "toolCallRequest", toolCallRequest: {
            id: request.toolCallRequestId, type: "function", arguments: structuredClone(request.parameters || {}),
            name: request.name,
          } });
          return result;
        };
        if (key === "appendToolResult") return (...args: Parameters<typeof block.appendToolResult>) => {
          const result = target.appendToolResult(...args), value = args[0];
          entry.parts.push({ type: "toolCallResult", toolCallId: value.toolCallRequestId, content: value.content });
          return result;
        };
        const value = Reflect.get(target, key, target);
        return typeof value === "function" ? value.bind(target) : value;
      } });
    } };
  }

  snapshot(): Chat {
    const history = Chat.from(this.initial);
    for (const entry of this.blocks) {
      const content: ChatMessagePartData[] = [...(entry.text ? [{ type: "text" as const, text: entry.text }] : []), ...entry.parts];
      if (content.length) history.append(ChatMessage.from({ role: entry.role, content } as ChatMessageData));
    }
    return history;
  }
}

export function selectedSourceIsThisPlugin(source: unknown): boolean {
  const identifier = String((source as { identifier?: string })?.identifier || "").toLowerCase();
  return identifier.includes("unreal-context-compactor") || identifier.includes("codex/unreal-context-compactor");
}

export function toolPluginIdentifier(tools: Array<RemoteToolLike>, name: string): string | undefined {
  return tools.find((tool) => tool.name === name)?.pluginIdentifier;
}

export function createMessageEmitter(
  ctl: ContentController,
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

export function createToolGenerationTracker(ctl: PredictionLoopHandlerController) {
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
  const hasUnfinished = () => statuses.size > 0;
  return { start, name, argument, end, waitingForApproval, executing, finalized, failure, hasUnfinished };
}

export function createRoundActivityTracker(
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
