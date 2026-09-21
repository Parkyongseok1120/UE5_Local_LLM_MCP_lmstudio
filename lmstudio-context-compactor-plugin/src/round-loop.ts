import {
  type Chat,
  type ChatMessage,
  type LLM,
  type LLMActionOpts,
  type LLMGeneratorHandle,
  type Tool,
} from "@lmstudio/sdk";

type TokenSource = LLM | LLMGeneratorHandle;

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
};

export async function runOneToolRound(
  tokenSource: TokenSource,
  history: Chat,
  tools: Array<Tool>,
  parentSignal: AbortSignal,
  callbacks: RoundCallbacks,
  predictionOptions: Pick<LLMActionOpts, "maxTokens"> = {},
): Promise<CapturedRound> {
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

  const forwardAbort = () => {
    if (!roundAbort.signal.aborted) roundAbort.abort(parentSignal.reason);
  };
  if (parentSignal.aborted) forwardAbort();
  else parentSignal.addEventListener("abort", forwardAbort, { once: true });

  try {
    const { onMessageCaptured, abortAfterPredictionFragment, ...actCallbacks } = callbacks;
    await tokenSource.act(history, tools, {
      signal: roundAbort.signal,
      ...predictionOptions,
      ...actCallbacks,
      onPredictionFragment: (fragment) => {
        actCallbacks.onPredictionFragment?.(fragment);
        const reason = abortAfterPredictionFragment?.(fragment);
        if (reason && !roundAbort.signal.aborted) {
          fragmentAbortReason = reason;
          roundAbort.abort(reason);
        }
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
    ...(failure === undefined ? {} : { failure }),
  };
}
