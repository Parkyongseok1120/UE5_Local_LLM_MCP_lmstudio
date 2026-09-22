import {
  type Chat,
  type ChatMessage,
  type LLM,
  type LLMActionOpts,
  type LLMGeneratorHandle,
  type Tool,
} from "@lmstudio/sdk";

type TokenSource = LLM | LLMGeneratorHandle;

const RAW_TOOL_INTENT_PATTERN = /<(?:tool_call|function=|\|(?:tool_call|python_tag)\|)/iu;
const RAW_TOOL_INTENT_CARRY_CHARS = 96;

type RoundCallbacks = {
  onToolCallRequestFinalized: NonNullable<LLMActionOpts["onToolCallRequestFinalized"]>;
  guardToolCall: NonNullable<LLMActionOpts["guardToolCall"]>;
  onPromptProcessingProgress?: NonNullable<LLMActionOpts["onPromptProcessingProgress"]>;
  onFirstToken?: NonNullable<LLMActionOpts["onFirstToken"]>;
  onPredictionFragment?: NonNullable<LLMActionOpts["onPredictionFragment"]>;
  onToolCallRequestStart?: NonNullable<LLMActionOpts["onToolCallRequestStart"]>;
  onToolCallRequestNameReceived?: NonNullable<LLMActionOpts["onToolCallRequestNameReceived"]>;
  onToolCallRequestArgumentFragmentGenerated?: NonNullable<LLMActionOpts["onToolCallRequestArgumentFragmentGenerated"]>;
  onToolCallRequestEnd?: NonNullable<LLMActionOpts["onToolCallRequestEnd"]>;
  onToolCallRequestFailure?: NonNullable<LLMActionOpts["onToolCallRequestFailure"]>;
  onMessageCaptured?: (message: ChatMessage) => void;
  abortAfterPredictionFragment?: (fragment: Parameters<NonNullable<LLMActionOpts["onPredictionFragment"]>>[0]) => Error | undefined;
};

export type CapturedRound = {
  messages: Array<ChatMessage>;
  continueAfterTools: boolean;
  failure?: unknown;
  finishReason?: string;
  predictionStats?: {
    stopReason: string;
    promptTokensCount?: number;
    predictedTokensCount?: number;
    totalTokensCount?: number;
  };
  timing: {
    actElapsedMs: number;
    promptProcessingMs: number | null;
  };
  predictionUsage: {
    fragmentCount: number;
    reasoningTokensCount: number;
    visibleTokensCount: number;
    visibleChars: number;
    rawToolIntentCandidate: boolean;
    rawToolIntentFirstFragment: number | null;
    rawToolIntentFirstVisibleChar: number | null;
    structuralTokensCount: number;
    toolArgumentChars: number;
    toolArgumentTokensCount: number | null;
    unattributedTokensCount: number | null;
  };
};

export async function runOneToolRound(
  tokenSource: TokenSource,
  history: Chat,
  tools: Array<Tool>,
  parentSignal: AbortSignal,
  callbacks: RoundCallbacks,
  predictionOptions: Pick<LLMActionOpts, "maxTokens"> = {},
): Promise<CapturedRound> {
  const actStartedAt = Date.now();
  let firstTokenAt: number | null = null;
  const messages: Array<ChatMessage> = [];
  const roundAbort = new AbortController();
  const boundaryReason = new Error("LM Studio context-compactor round boundary");
  boundaryReason.name = "ContextCompactorRoundBoundary";
  let hasToolResults = false;
  let boundaryRequested = false;
  let failure: unknown;
  let finishReason: string | undefined;
  let predictionStats: CapturedRound["predictionStats"];
  let fragmentAbortReason: Error | undefined;
  let visibleFragmentTail = "";
  const predictionUsage = {
    fragmentCount: 0,
    reasoningTokensCount: 0,
    visibleTokensCount: 0,
    visibleChars: 0,
    rawToolIntentCandidate: false,
    rawToolIntentFirstFragment: null as number | null,
    rawToolIntentFirstVisibleChar: null as number | null,
    structuralTokensCount: 0,
    toolArgumentChars: 0,
    toolArgumentTokensCount: null as number | null,
    unattributedTokensCount: null as number | null,
  };

  const forwardAbort = () => {
    if (!roundAbort.signal.aborted) roundAbort.abort(parentSignal.reason);
  };
  if (parentSignal.aborted) forwardAbort();
  else parentSignal.addEventListener("abort", forwardAbort, { once: true });

  try {
    const { onMessageCaptured, abortAfterPredictionFragment, onFirstToken, ...actCallbacks } = callbacks;
    await tokenSource.act(history, tools, {
      signal: roundAbort.signal,
      ...predictionOptions,
      ...actCallbacks,
      onFirstToken: (...args) => {
        firstTokenAt ??= Date.now();
        onFirstToken?.(...args);
      },
      onPredictionFragment: (fragment) => {
        firstTokenAt ??= Date.now();
        const tokensCount = Number(fragment.tokensCount);
        predictionUsage.fragmentCount += 1;
        if (Number.isFinite(tokensCount) && tokensCount >= 0) {
          if (fragment.reasoningType === "reasoning") predictionUsage.reasoningTokensCount += tokensCount;
          else if (fragment.isStructural || fragment.reasoningType === "reasoningStartTag"
            || fragment.reasoningType === "reasoningEndTag") predictionUsage.structuralTokensCount += tokensCount;
          else predictionUsage.visibleTokensCount += tokensCount;
        }
        if (fragment.reasoningType !== "reasoning" && !fragment.isStructural
          && fragment.reasoningType !== "reasoningStartTag" && fragment.reasoningType !== "reasoningEndTag") {
          const visibleContent = String(fragment.content || "");
          const visibleCharsBeforeFragment = predictionUsage.visibleChars;
          predictionUsage.visibleChars += visibleContent.length;
          const candidate = `${visibleFragmentTail}${visibleContent}`;
          const rawMatch = RAW_TOOL_INTENT_PATTERN.exec(candidate);
          if (rawMatch) {
            predictionUsage.rawToolIntentCandidate = true;
            predictionUsage.rawToolIntentFirstFragment ??= predictionUsage.fragmentCount;
            predictionUsage.rawToolIntentFirstVisibleChar ??= Math.max(0,
              visibleCharsBeforeFragment - visibleFragmentTail.length + rawMatch.index);
          }
          visibleFragmentTail = candidate.slice(-RAW_TOOL_INTENT_CARRY_CHARS);
        }
        actCallbacks.onPredictionFragment?.(fragment);
        const reason = abortAfterPredictionFragment?.(fragment);
        if (reason && !roundAbort.signal.aborted) {
          fragmentAbortReason = reason;
          roundAbort.abort(reason);
        }
      },
      onToolCallRequestArgumentFragmentGenerated: (roundIndex, callId, content) => {
        predictionUsage.toolArgumentChars += String(content || "").length;
        actCallbacks.onToolCallRequestArgumentFragmentGenerated?.(roundIndex, callId, content);
      },
      onPredictionCompleted: (result) => {
        const stats = (result as { stats?: {
          stopReason?: unknown;
          promptTokensCount?: unknown;
          predictedTokensCount?: unknown;
          totalTokensCount?: unknown;
        } }).stats;
        finishReason = String(stats?.stopReason || "unknown");
        const finite = (value: unknown) => Number.isFinite(Number(value)) ? Number(value) : undefined;
        predictionStats = {
          stopReason: finishReason,
          ...(finite(stats?.promptTokensCount) === undefined ? {} : { promptTokensCount: finite(stats?.promptTokensCount) }),
          ...(finite(stats?.predictedTokensCount) === undefined ? {} : { predictedTokensCount: finite(stats?.predictedTokensCount) }),
          ...(finite(stats?.totalTokensCount) === undefined ? {} : { totalTokensCount: finite(stats?.totalTokensCount) }),
        };
      },
      onMessage: (message) => {
        messages.push(message);
        onMessageCaptured?.(message);
        if (message.getToolCallResults().length > 0) hasToolResults = true;
      },
      onRoundEnd: () => {
        if (!hasToolResults || roundAbort.signal.aborted) return;
        boundaryRequested = true;
        roundAbort.abort(boundaryReason);
      },
    });
  } catch (error) {
    if (!(boundaryRequested && error === boundaryReason) && error !== fragmentAbortReason) failure = error;
  } finally {
    parentSignal.removeEventListener("abort", forwardAbort);
  }

  return {
    messages,
    continueAfterTools: boundaryRequested,
    ...(fragmentAbortReason ? { finishReason: "generation_repetition_paused" }
      : finishReason === undefined ? {} : { finishReason }),
    ...(predictionStats ? { predictionStats } : {}),
    predictionUsage: {
      ...predictionUsage,
      unattributedTokensCount: predictionStats?.predictedTokensCount === undefined
        ? null
        : Math.max(0, predictionStats.predictedTokensCount
          - predictionUsage.reasoningTokensCount
          - predictionUsage.visibleTokensCount
          - predictionUsage.structuralTokensCount),
    },
    timing: {
      actElapsedMs: Math.max(0, Date.now() - actStartedAt),
      promptProcessingMs: firstTokenAt === null ? null : Math.max(0, firstTokenAt - actStartedAt),
    },
    ...(failure === undefined ? {} : { failure }),
  };
}
