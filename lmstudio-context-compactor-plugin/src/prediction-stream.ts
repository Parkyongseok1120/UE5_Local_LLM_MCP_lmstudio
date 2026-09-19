import type {
  LLMPredictionFragmentWithRoundIndex,
  PredictionProcessContentBlockController,
  PredictionLoopHandlerController,
} from "@lmstudio/sdk";

type SplitVisibleAnswer = (text: string) => {
  visibleText: string;
  hasFooter: boolean;
  note: unknown;
};

type StreamState = {
  answerBlock?: PredictionProcessContentBlockController;
  thinkingBlock?: PredictionProcessContentBlockController;
  rawVisible: string;
  pendingVisible: string;
  footerSeen: boolean;
  sawFragment: boolean;
  finished: boolean;
  consumed: boolean;
};

export type StreamedAssistant = {
  streamed: boolean;
  visibleText: string;
};

const FOOTER_MARKER = "\n<!-- direct-continuity-note-v1 -->";

export class PredictionStreamRenderer {
  private readonly states = new Map<number, StreamState>();

  constructor(
    private readonly ctl: PredictionLoopHandlerController,
    private readonly splitVisibleAnswer: SplitVisibleAnswer,
    private readonly protectContinuityFooter: boolean,
  ) {}

  private state(roundIndex: number): StreamState {
    let state = this.states.get(roundIndex);
    if (!state) {
      state = {
        rawVisible: "", pendingVisible: "", footerSeen: false,
        sawFragment: false, finished: false, consumed: false,
      };
      this.states.set(roundIndex, state);
    }
    return state;
  }

  private answerBlock(state: StreamState): PredictionProcessContentBlockController {
    state.answerBlock ??= this.ctl.createContentBlock({ roleOverride: "assistant" });
    return state.answerBlock;
  }

  private thinkingBlock(state: StreamState): PredictionProcessContentBlockController {
    state.thinkingBlock ??= this.ctl.createContentBlock({
      roleOverride: "assistant",
      includeInContext: false,
      style: { type: "thinking", ended: false, title: "생각 중" },
    });
    return state.thinkingBlock;
  }

  private appendVisible(state: StreamState, text: string, fragment: LLMPredictionFragmentWithRoundIndex): void {
    if (!text) return;
    this.answerBlock(state).appendText(text, {
      tokensCount: fragment.tokensCount,
      fromDraftModel: fragment.containsDrafted,
      isStructural: false,
    });
  }

  private streamVisible(state: StreamState, fragment: LLMPredictionFragmentWithRoundIndex): void {
    state.rawVisible += fragment.content;
    if (!this.protectContinuityFooter) {
      this.appendVisible(state, fragment.content, fragment);
      return;
    }
    if (state.footerSeen) return;
    state.pendingVisible += fragment.content;
    const markerIndex = state.pendingVisible.indexOf(FOOTER_MARKER);
    if (markerIndex >= 0) {
      this.appendVisible(state, state.pendingVisible.slice(0, markerIndex), fragment);
      state.pendingVisible = "";
      state.footerSeen = true;
      return;
    }
    const safeLength = Math.max(0, state.pendingVisible.length - FOOTER_MARKER.length + 1);
    if (safeLength > 0) {
      this.appendVisible(state, state.pendingVisible.slice(0, safeLength), fragment);
      state.pendingVisible = state.pendingVisible.slice(safeLength);
    }
  }

  onFragment = (fragment: LLMPredictionFragmentWithRoundIndex): void => {
    const state = this.state(fragment.roundIndex);
    state.sawFragment = true;
    if (fragment.reasoningType === "reasoning") {
      this.thinkingBlock(state).appendText(fragment.content, {
        tokensCount: fragment.tokensCount,
        fromDraftModel: fragment.containsDrafted,
        isStructural: false,
      });
      return;
    }
    if (fragment.reasoningType === "reasoningStartTag"
      || fragment.reasoningType === "reasoningEndTag" || fragment.isStructural) return;
    this.streamVisible(state, fragment);
  };

  finish(roundIndex: number): void {
    const state = this.state(roundIndex);
    if (state.finished) return;
    state.finished = true;
    if (!state.footerSeen && state.pendingVisible) {
      this.answerBlock(state).appendText(state.pendingVisible);
      state.pendingVisible = "";
    }
    if (state.thinkingBlock) {
      state.thinkingBlock.setStyle({ type: "thinking", ended: true, title: "생각" });
    }
  }

  consumeAssistant(rawText: string): StreamedAssistant {
    const entry = [...this.states.entries()]
      .sort(([left], [right]) => left - right)
      .find(([, state]) => state.sawFragment && !state.consumed);
    if (!entry) return { streamed: false, visibleText: this.splitVisibleAnswer(rawText).visibleText };
    const [roundIndex, state] = entry;
    this.finish(roundIndex);
    state.consumed = true;
    return {
      streamed: true,
      visibleText: this.splitVisibleAnswer(state.rawVisible).visibleText,
    };
  }
}
