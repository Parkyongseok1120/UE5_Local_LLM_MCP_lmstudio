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
};

export async function runOneToolRound(
  tokenSource: TokenSource,
  history: Chat,
  tools: Array<Tool>,
  parentSignal: AbortSignal,
  callbacks: RoundCallbacks,
): Promise<CapturedRound> {
  const messages: Array<ChatMessage> = [];
  const roundAbort = new AbortController();
  const boundaryReason = new Error("LM Studio context-compactor round boundary");
  boundaryReason.name = "ContextCompactorRoundBoundary";
  let hasToolResults = false;
  let boundaryRequested = false;
  let failure: unknown;
  let finishReason: string | undefined;
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
        finishReason = String((result as { stats?: { stopReason?: unknown } }).stats?.stopReason || "unknown");
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
    ...(failure === undefined ? {} : { failure }),
  };
}
