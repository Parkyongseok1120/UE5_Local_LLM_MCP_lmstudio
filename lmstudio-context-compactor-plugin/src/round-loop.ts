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
};

export type CapturedRound = {
  messages: Array<ChatMessage>;
  continueAfterTools: boolean;
  failure?: unknown;
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

  const forwardAbort = () => {
    if (!roundAbort.signal.aborted) roundAbort.abort(parentSignal.reason);
  };
  if (parentSignal.aborted) forwardAbort();
  else parentSignal.addEventListener("abort", forwardAbort, { once: true });

  try {
    const { onMessageCaptured, ...actCallbacks } = callbacks;
    await tokenSource.act(history, tools, {
      signal: roundAbort.signal,
      ...actCallbacks,
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
    if (!(boundaryRequested && error === boundaryReason)) failure = error;
  } finally {
    parentSignal.removeEventListener("abort", forwardAbort);
  }

  return {
    messages,
    continueAfterTools: boundaryRequested,
    ...(failure === undefined ? {} : { failure }),
  };
}
