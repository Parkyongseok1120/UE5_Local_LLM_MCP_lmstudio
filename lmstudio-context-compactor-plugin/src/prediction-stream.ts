import type {
  LLMPredictionFragmentWithRoundIndex,
  PredictionProcessContentBlockController,
  PredictionLoopHandlerController,
} from "@lmstudio/sdk";
import { InternalDataOutputFilter } from "./internal-data-output";
import { ToolProtocolOutputFilter, stripUnexecutedToolProtocol, hasRawToolIntent, visibleTextFromMessages } from "./raw-tool-intent";
import { ChatMessage } from "@lmstudio/sdk";
import { DELIVERED_REPORT_HEADER as FRAME_OPEN, DELIVERED_REPORT_END as FRAME_CLOSE } from "./review-patterns.js";
import type { ReviewAcceptance } from "./review-acceptance";
const { visibleAssistantText } = require("./continuity-text.js") as { visibleAssistantText(text: string): string };

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
  dataFilter: InternalDataOutputFilter;
  protocolFilter: ToolProtocolOutputFilter;
  echoFilter: DeliveredPrefixEchoFilter;
  visibleText: string;
  /** Review output held until settleReview() decides what is published. */
  heldText: string;
  settled: boolean;
};

/** Decides what a held report review may publish; see review-acceptance.ts. */
export type ReviewGate = (text: string, complete: boolean) => ReviewAcceptance;

export type StreamedAssistant = {
  streamed: boolean;
  visibleText: string;
  duplicateSuppressed?: boolean;
  suppressedChars?: number;
  suppressedProtocol?: boolean;
  incompleteProtocol?: boolean;
};

const FOOTER_MARKER = "\n<!-- direct-continuity-note-v";

/** A supplement may echo a delivered answer before producing its actual delta.
 * Hold only a possible prefix; unrelated replies and short partial matches pass.
 * The echo can be the whole data block the report was shown in, its header and
 * end line included (conversation 1794306949131, round 52): both lines are
 * stripped, so the block is recognized as the echo it is. */
export class DeliveredPrefixEchoFilter {
  private buffer = "";
  private passthrough = false;
  private readonly prefix: string;
  private readonly literalWhitespace: boolean;
  duplicateSuppressed = false;
  suppressedChars = 0;
  constructor(deliveredPrefix = "") {
    this.literalWhitespace = /^\s*(?:```|~~~)|^(?: {4}|\t)\S/mu.test(deliveredPrefix);
    this.prefix = this.normalize(deliveredPrefix);
  }
  /** Canonicalize prose whitespace, preserving inline-code and fenced-code
   * characters. Offsets use this same mapping when removing an exact echo. */
  private canonical(value: string): { text: string; ends: number[] } {
    let text = "", pendingSpace = -1, ticks = 0;
    const ends: number[] = [];
    const append = (part: string, end: number) => { text += part; ends.push(end); };
    for (let i = 0; i < value.length;) {
      const c = value[i];
      if (this.literalWhitespace) {
        if (c === "\r" && value[i + 1] === "\n") { append("\n", i + 2); i += 2; }
        else { append(c, i + 1); i++; }
        continue;
      }
      if (!ticks && /\s/u.test(c)) { pendingSpace = ++i; continue; }
      if (pendingSpace >= 0) { if (text) append(" ", pendingSpace); pendingSpace = -1; }
      if (c === "`") {
        let count = 1; while (value[i + count] === "`") count++;
        ticks = ticks === count ? 0 : ticks || count;
        for (let n = 0; n < count; n++) append("`", ++i);
      } else { append(c, ++i); }
    }
    // Literal mode only ignores whitespace around the whole framed report.
    if (this.literalWhitespace) {
      const from = text.length - text.trimStart().length, to = text.trimEnd().length;
      return { text: text.slice(from, to), ends: ends.slice(from, to) };
    }
    return { text, ends };
  }
  private normalize(value: string) { return this.canonical(value).text; }
  /** null until the start of the output shows whether it copies the frame header. */
  private framed: boolean | null = null;
  private stripFrameHeader(final: boolean): boolean {
    if (this.framed !== null) return true;
    const head = this.buffer.trimStart();
    if (!final && head.length < FRAME_OPEN.length && FRAME_OPEN.startsWith(head)) return false;
    this.framed = head.startsWith(FRAME_OPEN);
    if (this.framed) {
      const cut = this.buffer.length - head.length + FRAME_OPEN.length;
      this.duplicateSuppressed = true; this.suppressedChars += cut; this.buffer = this.buffer.slice(cut);
    }
    return true;
  }
  push(text: string): string {
    if (this.passthrough || !this.prefix) return this.release(text);
    this.buffer += text;
    if (!this.stripFrameHeader(false)) return "";
    const current = this.normalize(this.buffer);
    if (this.prefix.startsWith(current)) return "";
    // Only an exact, complete report followed by a separated continuation is
    // removable. Insertions, edits, deletions and reordering keep their context.
    const end = this.canonical(this.buffer).ends[this.prefix.length - 1];
    if (current.startsWith(this.prefix) && end !== undefined && /^[ \t]*\r?\n/u.test(this.buffer.slice(end))) {
      this.passthrough = true; return this.cutEcho(this.prefix.length);
    }
    this.passthrough = true; return this.drain();
  }
  private cutEcho(matched: number): string {
    const cut = this.canonical(this.buffer).ends[matched - 1] || 0;
    this.duplicateSuppressed = true; this.suppressedChars += cut;
    this.buffer = this.buffer.slice(cut);
    this.heldSpace = true; this.closing = true;
    return this.drain();
  }
  // Whitespace right after a suppressed echo is shown only with the delta it
  // precedes; alone it would deliver an empty supplement.
  private heldSpace = false;
  private space = "";
  /** Right after a suppressed echo the frame's end line may follow; it is dropped too. */
  private closing = false;
  private release(text: string, final = false): string {
    if (!this.heldSpace) return text;
    this.space += text;
    let body = this.space.trimStart();
    if (this.closing && body) {
      if (!final && body.length < FRAME_CLOSE.length && FRAME_CLOSE.startsWith(body)) return "";
      this.closing = false;
      if (body.startsWith(FRAME_CLOSE)) {
        this.suppressedChars += this.space.length - body.length + FRAME_CLOSE.length;
        this.space = body = body.slice(FRAME_CLOSE.length);
        if (!body.trim()) return "";
      }
    }
    if (!body) {
      if (final) { this.suppressedChars += this.space.length; this.space = ""; }
      return "";
    }
    this.heldSpace = false; const out = this.space; this.space = ""; return out;
  }
  finish(): string {
    if (this.passthrough || !this.prefix) { this.passthrough = true; return this.release(this.drain(), true); }
    this.passthrough = true;
    this.stripFrameHeader(true);
    const out = this.normalize(this.buffer) === this.prefix ? this.cutEcho(this.prefix.length) : this.drain();
    return out + this.release("", true);
  }
  private drain() { const text = this.buffer; this.buffer = ""; return this.release(text); }
}

export class GenerationRepetitionDetector {
  private text = "";
  private detected = false;
  private totalChars = 0;
  private lastCheckedAt = 0;
  private block = "";
  /** The block that repeated, once detected. */
  get repeatedBlock() { return this.block; }

  constructor(private readonly repeatCount = 3, private readonly minimumBlockChars = 80) {}

  observe(fragment: LLMPredictionFragmentWithRoundIndex): boolean {
    if (this.detected || fragment.isStructural || fragment.reasoningType === "reasoningStartTag"
      || fragment.reasoningType === "reasoningEndTag" || !fragment.content) return false;
    this.totalChars += fragment.content.length;
    this.text = (this.text + fragment.content).slice(-12_000);
    if (this.totalChars - this.lastCheckedAt < 32) return false;
    this.lastCheckedAt = this.totalChars;
    const maximumBlock = Math.min(2_000, Math.floor(this.text.length / this.repeatCount));
    for (let size = this.minimumBlockChars; size <= maximumBlock; size += 1) {
      const suffix = this.text.slice(-size);
      if (new Set(suffix.replace(/\s/gu, "")).size < 12) continue;
      let repeated = true;
      for (let copy = 2; copy <= this.repeatCount; copy += 1) {
        if (this.text.slice(-size * copy, -size * (copy - 1)) !== suffix) {
          repeated = false;
          break;
        }
      }
      if (repeated) {
        this.detected = true;
        this.block = suffix;
        return true;
      }
    }
    return false;
  }
}

export class PredictionStreamRenderer {
  private readonly states = new Map<number, StreamState>();
  private readonly consumedMessages = new WeakMap<ChatMessage, StreamedAssistant>();
  private fallbackSuppressed = 0;
  get suppressedChars() { return this.fallbackSuppressed + [...this.states.values()].reduce((n, s) => n + s.echoFilter.suppressedChars, 0); }
  consumeMessage(message: ChatMessage): StreamedAssistant {
    let result = this.consumedMessages.get(message);
    if (!result) { result = this.consumeAssistant(message.getText()); this.consumedMessages.set(message, result); }
    return result;
  }
  visibleMessage(message: ChatMessage): ChatMessage {
    if (!message.isAssistantMessage() || !message.getText()) return message;
    const copy = ChatMessage.from(message); copy.replaceText(this.consumeMessage(message).visibleText); return copy;
  }
  /** Use the same filtered prose for the model while retaining SDK reasoning.
   * Tool intent remains a separate captured observation, never displayed prose. */
  historyMessage(message: ChatMessage): ChatMessage {
    if (!message.isAssistantMessage() || !message.getText()) return message;
    const raw = this.splitVisibleAnswer(message.getText()).visibleText;
    const prose = visibleAssistantText(raw), reasoning = raw.slice(0, raw.length - prose.length);
    const copy = ChatMessage.from(message); copy.replaceText(reasoning + this.consumeMessage(message).visibleText); return copy;
  }
  normalizeCaptured<T extends { messages: ChatMessage[]; predictionUsage: { rawToolIntentCandidate?: boolean } }>(captured: T) {
    const unexecutedToolIntent = hasRawToolIntent(visibleTextFromMessages(captured.messages, this.internalDataParts), captured.messages);
    const messages = captured.messages.map(message => this.visibleMessage(message));
    const observations = captured.messages.filter(m => m.isAssistantMessage() && m.getText()).map(m => this.consumeMessage(m));
    return { ...captured, messages, unexecutedToolIntent, presentation: {
      generatedVisibleChars: captured.messages.filter(m => m.isAssistantMessage()).reduce((n, m) => n + visibleAssistantText(m.getText()).length, 0),
      deliveredChars: messages.filter(m => m.isAssistantMessage()).reduce((n, m) => n + m.getText().length, 0),
      suppressedInternalProtocol: observations.some(o => o.suppressedProtocol),
      incompleteInternalProtocol: observations.some(o => o.incompleteProtocol),
      duplicateSuppressedChars: this.suppressedChars,
      ...(this.reviewGate ? { reviewAcceptance: this.reviewAcceptance } : {}),
    },
      predictionUsage: { ...captured.predictionUsage, rawToolIntentCandidate: captured.predictionUsage.rawToolIntentCandidate || unexecutedToolIntent } };
  }

  constructor(
    private readonly ctl: Pick<PredictionLoopHandlerController, "createContentBlock">,
    private readonly splitVisibleAnswer: SplitVisibleAnswer,
    private readonly protectContinuityFooter: boolean,
    private readonly suppressReasoning = false,
    private readonly internalDataParts: readonly string[] = [],
    private readonly deliveredPrefix?: string,
    /** Set for a report review: its output is held and published once, as decided. */
    private readonly reviewGate?: ReviewGate,
  ) {}

  private reviewDecisions: ReviewAcceptance[] = [];
  private reviewComplete: boolean | null = null;
  /** The decision on this round's review output; one per generated message, merged. */
  get reviewAcceptance(): ReviewAcceptance | undefined {
    const decisions = this.reviewDecisions;
    if (!decisions.length) return undefined;
    const rank = ["accepted", "salvaged", "rejected", "withheld"] as const;
    const worst = decisions.reduce((a, b) => rank.indexOf(b.decision) > rank.indexOf(a.decision) ? b : a);
    return { ...worst, text: decisions.map(item => item.text).join(""),
      generatedChars: decisions.reduce((n, item) => n + item.generatedChars, 0) };
  }

  /** Publish held review output once the round's end is known. Called after
   * finishAll() and before any message is consumed, so the screen, the saved
   * transcript and the next model input all get the decided text. */
  settleReview(complete: boolean): void {
    if (!this.reviewGate) return;
    this.reviewComplete = complete;
    for (const state of this.states.values()) this.settle(state);
  }

  private settle(state: StreamState): void {
    if (!this.reviewGate || state.settled || !state.sawFragment) return;
    state.settled = true;
    const decision = this.reviewGate(state.heldText, this.reviewComplete === true);
    this.reviewDecisions.push(decision);
    state.visibleText = decision.text;
    if (decision.text) this.answerBlock(state).appendText(decision.text);
  }

  private state(roundIndex: number): StreamState {
    let state = this.states.get(roundIndex);
    if (!state) {
      state = {
        rawVisible: "", pendingVisible: "", footerSeen: false,
        sawFragment: false, finished: false, consumed: false,
        dataFilter: new InternalDataOutputFilter(this.internalDataParts),
        protocolFilter: new ToolProtocolOutputFilter(),
        echoFilter: new DeliveredPrefixEchoFilter(this.deliveredPrefix), visibleText: "",
        heldText: "", settled: false,
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
    text = state.echoFilter.push(state.dataFilter.push(state.protocolFilter.push(text)));
    if (!text) return;
    if (this.reviewGate) { state.heldText += text; return; }
    state.visibleText += text;
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
      if (this.suppressReasoning) return;
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
    const show = (text: string) => {
      if (!text) return;
      if (this.reviewGate) { state.heldText += text; return; }
      state.visibleText += text; this.answerBlock(state).appendText(text);
    };
    if (!state.footerSeen && state.pendingVisible) {
      show(state.echoFilter.push(state.dataFilter.push(state.protocolFilter.push(state.pendingVisible))));
      state.pendingVisible = "";
    }
    show(state.echoFilter.push(state.dataFilter.push(state.protocolFilter.finish()) + state.dataFilter.finish())
      + state.echoFilter.finish());
    if (state.thinkingBlock) {
      state.thinkingBlock.setStyle({ type: "thinking", ended: true, title: "생각" });
    }
  }

  finishAll(): void { for (const roundIndex of this.states.keys()) this.finish(roundIndex); }

  consumeAssistant(rawText: string): StreamedAssistant {
    const entry = [...this.states.entries()]
      .sort(([left], [right]) => left - right)
      .find(([, state]) => state.sawFragment && !state.consumed);
    if (!entry) {
      const echo = new DeliveredPrefixEchoFilter(this.deliveredPrefix);
      const dataFilter = new InternalDataOutputFilter(this.internalDataParts);
      const raw = stripUnexecutedToolProtocol(this.splitVisibleAnswer(visibleAssistantText(rawText)).visibleText);
      const text = dataFilter.push(raw) + dataFilter.finish();
      let visibleText = echo.push(text) + echo.finish();
      this.fallbackSuppressed += echo.suppressedChars;
      if (this.reviewGate) {
        const decision = this.reviewGate(visibleText, this.reviewComplete === true);
        this.reviewDecisions.push(decision); visibleText = decision.text;
      }
      return { streamed: false, visibleText,
        ...dataFilter.observation,
        duplicateSuppressed: echo.duplicateSuppressed, suppressedChars: echo.suppressedChars };
    }
    const [roundIndex, state] = entry;
    this.finish(roundIndex);
    // Consumed before the round's end was known: nothing unjudged is published.
    this.settle(state);
    state.consumed = true;
    return {
      streamed: true,
      visibleText: state.visibleText, duplicateSuppressed: state.echoFilter.duplicateSuppressed,
      ...state.dataFilter.observation,
      suppressedChars: state.echoFilter.suppressedChars,
    };
  }
}
